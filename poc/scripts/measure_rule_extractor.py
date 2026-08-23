"""룰 추출기가 S/V/M 을 얼마나 맞히는지 잰다 — 시드를 고치기 전에 기준선부터.

왜 필요한가(실측 2026-08-12). 서빙 자동확정이 분포 밖 데이터에서 0% 였고, 사유의
99.4% 가 `rule under-detected S/V/M` 이었다. 즉 자동화를 막는 것은 분류기가 아니라
**룰 추출기**다. 룰과 모델이 다르면 합의 게이트가 무조건 검수로 보내기 때문이다.

원인은 시드 사전에 있다. 404개 중 90%가 복합어이고(1어절 41개뿐) 매칭은 전부 exact
문자열이다. 그래서 사전에 `원가 구조`·`납품 단가 협상`이 있어도 문서가 그냥 "원가",
"협상"이라고 쓰면 못 잡는다. **`유출`·`누출`은 시드에 아예 없다** — 영업비밀 판단에서
가장 중심적인 단어인데도.

시드를 고치려면 먼저 재야 한다. 그런데 심판 요소값이 붙은 문서는 48건(실질 24건)뿐이라
거기 맞춰 튜닝하면 그 24건에 과적합한다. 다행히 v3 final_800 은 `expected_factor_scores`
를 **구성상** 800건 전부 가지고 있다(등급에서 요소 삼중항을 역배정해 만든 셋). 이쪽을
주 측정면으로 쓰고 24건은 보조로 본다.

측정 항목:
    요소별 MAE·부호      룰이 심판/정답보다 낮게 보는가(미탐 방향) 높게 보는가
    등급 일치율          grade_from_svm(룰) 이 정답 등급과 같은가
    무매칭 비율          키워드가 하나도 안 걸린 문서 비율 — 사전 커버리지의 직접 지표

⚠ 이 스크립트는 판정하지 않는다. 시드를 바꾸기 전후로 같은 명령을 돌려 **out-of-sample
(v3 800)에서도 개선됐는지** 보는 것이 목적이다. 24건에서만 좋아졌으면 과적합이다.
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

FACTORS = ("secrecy", "value", "management")


def _rule_levels(engine, text: str) -> tuple[tuple[int, int, int], int]:
    """룰이 뽑은 (S,V,M) 정수 레벨과 매칭 키워드 수."""
    res = engine.label(text)
    f = res.factors
    levels = tuple(int(float(getattr(f, k, 0))) for k in FACTORS)
    n = len(getattr(getattr(res, "rule_result", None), "matched_keywords", []) or [])
    return levels, n  # type: ignore[return-value]


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _truth(row: dict) -> tuple[tuple[int, int, int] | None, str | None]:
    """정답 요소 삼중항과 등급. v3 는 expected_factor_scores, 골든셋은 label_provenance."""
    efs = row.get("expected_factor_scores")
    if isinstance(efs, dict) and all(k in efs for k in FACTORS):
        return tuple(int(efs[k]) for k in FACTORS), row.get("label")  # type: ignore[return-value]
    avg = (row.get("label_provenance") or {}).get("avg_svm")
    if isinstance(avg, dict):
        # 심판 평균은 실수다. 반올림/내림에서 등급이 갈리는 경계건이 있으나(고등급의
        # 11.5%) 요소 오차 측정에는 실수 그대로 쓰는 편이 정보를 덜 버린다.
        return tuple(float(avg[k[0]]) for k in FACTORS), row.get("label")  # type: ignore[return-value]
    return None, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="룰 추출기 요소 정확도 측정")
    parser.add_argument("--eval", required=True, action="append",
                        help="평가 jsonl (여러 번 지정 가능)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    from koipa.modules.m3_labeling.pipeline import LabelingPipeline
    from koipa.modules.m3_labeling.rule_engine import grade_from_svm

    engine = LabelingPipeline()
    report: dict = {"sets": {}}

    for spec in args.eval:
        path = Path(spec)
        rows = _load(path)
        diffs: dict[str, list[float]] = {k: [] for k in FACTORS}
        grade_hit = 0
        no_match = 0
        scored = 0
        for row in rows:
            text = row.get("text") or row.get("content") or ""
            truth, label = _truth(row)
            if truth is None or not text:
                continue
            scored += 1
            levels, n_kw = _rule_levels(engine, text)
            if n_kw == 0:
                no_match += 1
            for i, k in enumerate(FACTORS):
                diffs[k].append(levels[i] - float(truth[i]))
            if label and grade_from_svm(*levels) == label:
                grade_hit += 1

        entry = {
            "n": scored,
            "grade_match_rate": round(grade_hit / scored, 4) if scored else None,
            "no_keyword_match_rate": round(no_match / scored, 4) if scored else None,
            "factors": {},
        }
        for k in FACTORS:
            d = diffs[k]
            entry["factors"][k] = {
                "mean_diff": round(st.mean(d), 3),
                "mae": round(st.mean([abs(x) for x in d]), 3),
                "under_rate": round(sum(1 for x in d if x < 0) / len(d), 3),
                "over_rate": round(sum(1 for x in d if x > 0) / len(d), 3),
            }
        report["sets"][path.name] = entry

        print(f"\n{path.name}  N={scored}")
        print(f"  등급 일치율 {entry['grade_match_rate']:.1%} · "
              f"키워드 무매칭 {entry['no_keyword_match_rate']:.1%}")
        print(f"  {'요소':<12}{'평균차':>8}{'MAE':>8}{'낮게봄':>9}{'높게봄':>9}")
        for k in FACTORS:
            e = entry["factors"][k]
            print(f"  {k:<12}{e['mean_diff']:>+8.2f}{e['mae']:>8.2f}"
                  f"{e['under_rate']:>9.1%}{e['over_rate']:>9.1%}")

    report["note"] = (
        "평균차 음수 = 룰이 정답보다 낮게 봄(미탐 방향). 시드 수정 전후로 같은 명령을 "
        "돌려 v3 final_800(out-of-sample)에서도 개선됐는지 확인할 것 — 24건짜리 골든셋에서만 "
        "좋아졌으면 과적합이다."
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
