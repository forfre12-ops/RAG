"""요소 근거로 S/V/M 레벨을 매기는 임계를 찾는다 — 튜닝면과 검증면을 분리해서.

배경(`docs/RULE_EXTRACTOR_DIAGNOSIS_2026-08-12.md`). 현재 s_lv/v_lv/m_lv 은 요소 근거가
아니라 content_grade(키워드 argmax 등급)에서 역산된다. 그래서 v3 final_800 에서 룰이
800건 전부를 S3 라고 하고 요소값도 전부 (0,0,0) 이다. 누산한 키워드 점수는 표시용으로만
쓰이고 곧 덮인다.

이 스크립트는 **덮기 전의 근거**로 레벨을 매길 수 있는지 본다. 매칭된 키워드의 점수를
정본 3요소로 합산하고, 임계 두 개(t1<t2)로 0/1/2 를 만든 뒤 정답과 대조한다.

⚠ 과적합 방지가 이 스크립트의 핵심 설계다:
    튜닝면  labeled_v6_factor_grounded/train.jsonl  (1,833건, 요소정답 보유)
    검증면  proxy_eval/.../final_800.locked.jsonl   (800건, 손 안 댐)
두 셋의 독립은 report_holdout_independence.py 로 확인했다(계보 독립 True). 튜닝면에서
고른 임계를 검증면에 그대로 적용해 **거기서도 개선되는지**가 판정 기준이다. 튜닝면에서만
좋아지면 임계가 아니라 그 셋을 외운 것이다.

선택 기준은 MAE 최소가 아니다. 본 사업 1차 목표가 미탐 최소화이므로 **낮게 보는 비율
(under_rate)** 을 먼저 줄이고, 그 다음 MAE 를 본다. 요소를 높게 보면 등급이 같거나 더
높아질 뿐이라(grade_from_svm 은 요소에 대해 단조) 과분류 방향 = 안전 방향이다.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

FACTORS = ("SECRECY", "VALUE", "MANAGEMENT")
_TRUTH_KEY = {"SECRECY": "secrecy", "VALUE": "value", "MANAGEMENT": "management"}


def _collect(engine, to_canonical, rows: list[dict]) -> list[tuple[dict[str, float], dict[str, int]]]:
    """문서마다 (요소별 누산 점수, 정답 레벨)."""
    out = []
    for row in rows:
        text = row.get("text") or row.get("content") or ""
        truth = row.get("expected_factor_scores")
        if not text or not isinstance(truth, dict):
            continue
        res = engine.label(text)
        raw = {f: 0.0 for f in FACTORS}
        for mk in getattr(getattr(res, "rule_result", None), "matched_keywords", []) or []:
            canon = to_canonical(mk.factor)
            if canon in raw:
                raw[canon] += float(mk.score)
        out.append((raw, {f: int(truth[_TRUTH_KEY[f]]) for f in FACTORS}))
    return out


def _level(score: float, t1: float, t2: float) -> int:
    return 2 if score >= t2 else (1 if score >= t1 else 0)


def _score_pair(data, factor: str, t1: float, t2: float) -> dict:
    diffs = [_level(raw[factor], t1, t2) - truth[factor] for raw, truth in data]
    return {
        "t1": round(t1, 2),
        "t2": round(t2, 2),
        "mae": round(st.mean([abs(d) for d in diffs]), 4),
        "mean_diff": round(st.mean(diffs), 4),
        "under_rate": round(sum(1 for d in diffs if d < 0) / len(diffs), 4),
        "over_rate": round(sum(1 for d in diffs if d > 0) / len(diffs), 4),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="요소 임계 탐색 (튜닝면/검증면 분리)")
    parser.add_argument("--tune", required=True, help="임계를 고르는 셋")
    parser.add_argument("--verify", required=True, help="손대지 않는 검증 셋")
    parser.add_argument("--max-over-rate", type=float, default=0.35,
                        help="허용 과검출 비율 상한 — 과분류는 안전하지만 검수부담이다")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    from koipa.modules.m3_labeling.pipeline import LabelingPipeline
    from koipa.modules.m3_labeling.seeds import to_canonical_factor

    engine = LabelingPipeline()

    def load(p: str) -> list[dict]:
        return [json.loads(x) for x in Path(p).read_text("utf-8").splitlines() if x.strip()]

    print("[collect] 튜닝면...")
    tune = _collect(engine, to_canonical_factor, load(args.tune))
    print("[collect] 검증면...")
    verify = _collect(engine, to_canonical_factor, load(args.verify))
    print(f"  튜닝 {len(tune)}건 · 검증 {len(verify)}건\n")

    grid = [round(x * 0.15, 2) for x in range(0, 27)]  # 0.00 ~ 3.90
    report: dict = {"tune": args.tune, "verify": args.verify, "factors": {}}

    for factor in FACTORS:
        best = None
        for i, t1 in enumerate(grid):
            for t2 in grid[i:]:
                s = _score_pair(tune, factor, t1, t2)
                if s["over_rate"] > args.max_over_rate:
                    continue
                # 미탐 방향 우선, 동률이면 MAE
                key = (s["under_rate"], s["mae"])
                if best is None or key < (best["under_rate"], best["mae"]):
                    best = s
        if best is None:
            print(f"{factor}: 상한 {args.max_over_rate} 안에서 해 없음")
            continue
        ver = _score_pair(verify, factor, best["t1"], best["t2"])
        report["factors"][factor] = {"chosen": best, "verify": ver}
        print(f"{factor}  임계 t1={best['t1']} t2={best['t2']}")
        print(f"   튜닝면  MAE {best['mae']:.3f} · 낮게봄 {best['under_rate']:.1%} · 높게봄 {best['over_rate']:.1%}")
        print(f"   검증면  MAE {ver['mae']:.3f} · 낮게봄 {ver['under_rate']:.1%} · 높게봄 {ver['over_rate']:.1%}")

    report["note"] = (
        "검증면 수치가 튜닝면과 크게 다르면 임계가 튜닝면을 외운 것이다. 현행(등급 역산) "
        "기준선은 reports/rule_extractor_baseline.json 과 비교할 것."
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"\n[report] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
