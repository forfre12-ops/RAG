#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""재학습 후보를 승격할지 **한 명령으로** 판정한다 — 기준을 문서가 아니라 코드에 둔다.

## 왜 (2026-09-14)

재학습이 여섯 번 실패했고 여섯 번 다 같은 방식이었다.

    v6 판례교정      미탐 16 → 83
    판례 재라벨       미탐 0.179 → 0.801   (과분류는 0.850 → 0.000)
    v7 계열          "교환일 뿐"
    v8 요소모델       자동확정이 전부 최고등급 → 승격 불가
    LLM 코퍼스        미탐 0.2% 인데 사람라벨셋 정확도 0.31~0.33
    창 단위 학습      세 면 전부 하락 · 공개문서 과탐 9~13 → 19~37

공통점은 **판정 기준이 없었다**는 것이다. 한 축만 보면 늘 좋아 보인다 —
과탐을 줄이면 미탐이 늘고, 미탐을 줄이면 과탐이 는다. 그 교환을 한 화면에서
보지 않으면 매번 "좋아졌다" 고 착각한다.

## 이 도구가 정하는 것

    1) 어느 면에서 재는가        제외 목록에 없고 학습 겹침이 0 인 면만
    2) 무엇을 재는가             미탐 3축 + 과탐 4축 (합치지 않는다)
    3) 무엇을 주장할 수 있는가    eval_authority 게이트가 판정한다
    4) 승격 가능한가             아래 규칙

승격 규칙 — **한 축이라도 나빠지면 자동 승격은 없다.**

    REJECT     어느 면에서든 미탐이 늘었다 (계약 핵심목표가 미탐 최소화다)
    REJECT     어느 면에서든 고등급 격상(severe overclass)이 늘었다
    HOLD       개선은 있으나 다른 축이 나빠졌다 — 교환이므로 사람이 정한다
    HOLD       판정면의 claim_status 가 BLOCKED 다 (정답 권위 없음)
    PROMOTE    모든 축이 같거나 나아졌고 판정면이 최소 하나 DIAGNOSTIC_ONLY 이상

⚠ PROMOTE 는 "배포하라" 가 아니라 "배포를 **검토할 수 있다**" 는 뜻이다.
   배포는 지시가 있을 때만 한다.

사용:
    # 기준선 스냅샷 (현행 배포본)
    ... judge_model_candidate.py --snapshot reports/MODEL_BASELINE.json \\
        --records <면>=<records.jsonl> --eval <면>=<eval.jsonl> ...

    # 후보 판정
    ... judge_model_candidate.py --baseline reports/MODEL_BASELINE.json \\
        --records <면>=<후보 records.jsonl> --eval <면>=<eval.jsonl> ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "src"))
sys.path.insert(0, str(POC / "scripts"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from koipa.eval_authority import load_suite_exclusions  # noqa: E402
from measure_four_metrics import _rows, compute, suite_context  # noqa: E402

#: 나빠지면 즉시 REJECT 인 축 — 계약 핵심목표(미탐 최소화)와 감리 185(가)(공개문서 격상)
HARD_AXES = (
    ("high_grade_auto_confirm_fn_rate", "고등급 자동확정 미탐"),
    ("serving_recall", "서빙 미탐"),
    ("severe_overclass", "고등급 격상"),
)
#: 나빠지면 HOLD 인 축 — 교환일 수 있으므로 사람이 본다
SOFT_AXES = (
    ("model_overclass", "모델 과탐"),
    ("serving_overclass", "서빙 과탐"),
    ("model_recall", "모델 미탐"),
    ("review_load", "검수 업무량"),
)
#: 비율 비교에서 이 이하 차이는 같다고 본다(부동소수 잡음)
EPS = 1e-9


def _pair(arg: str) -> tuple[str, str]:
    if "=" not in arg:
        raise argparse.ArgumentTypeError(f"'면=경로' 형식이어야 한다: {arg}")
    k, v = arg.split("=", 1)
    return k.strip(), v.strip()


def measure(face: str, rec_rel: str, eval_rel: str, exclusions: dict) -> dict:
    rows = list(_rows(POC / rec_rel))
    if not rows:
        return {"face": face, "error": f"레코드 없음: {rec_rel}"}
    excl = exclusions.get(eval_rel.replace("\\", "/"), {})
    excl_reason = (str(excl.get("reason") or "")
                   if "scoring" in (excl.get("excluded_from") or []) else "")
    tier, overlap, tier_ct = suite_context(POC / eval_rel)
    m = compute(rows)
    usable = (not excl_reason) and overlap == 0
    return {
        "face": face, "records": rec_rel, "eval_set": eval_rel,
        "truth_tier": tier.value, "truth_tier_counts": dict(tier_ct),
        "training_overlap": overlap,
        "excluded_reason": excl_reason,
        "usable_for_judgement": usable,
        "metrics": m,
    }


def _rate(m: dict, key: str) -> float | None:
    d = m.get(key)
    if not isinstance(d, dict):
        return None
    return d.get("rate")


def judge(baseline: dict, current: list[dict]) -> dict:
    base_by_face = {f["face"]: f for f in baseline.get("faces", []) if "metrics" in f}
    verdict = "PROMOTE"
    reasons: list[str] = []
    deltas: list[dict] = []
    judged_faces = 0

    for cur in current:
        face = cur["face"]
        if "metrics" not in cur:
            reasons.append(f"[{face}] {cur.get('error')}")
            verdict = "HOLD"
            continue
        if not cur["usable_for_judgement"]:
            why = cur["excluded_reason"] or f"학습 겹침 {cur['training_overlap']}건"
            reasons.append(f"[{face}] 판정면으로 못 씀 — {why[:60]}")
            continue
        base = base_by_face.get(face)
        if not base:
            reasons.append(f"[{face}] 기준선에 없는 면 — 비교 불가")
            verdict = "HOLD"
            continue
        judged_faces += 1
        for key, label in HARD_AXES + SOFT_AXES:
            b, c = _rate(base["metrics"], key), _rate(cur["metrics"], key)
            if b is None or c is None:
                continue
            if abs(c - b) <= EPS:
                continue
            row = {"face": face, "axis": key, "label": label,
                   "baseline": b, "candidate": c, "delta": c - b}
            deltas.append(row)
            if c > b:  # 비율이 커졌다 = 나빠졌다 (미탐·과탐·검수 모두 낮을수록 좋다)
                hard = any(key == k for k, _ in HARD_AXES)
                row["worse"] = True
                if hard:
                    verdict = "REJECT"
                    reasons.append(
                        f"[{face}] {label}이 {b * 100:.2f}% → {c * 100:.2f}% 로 나빠졌다 — 즉시 기각"
                    )
                elif verdict != "REJECT":
                    verdict = "HOLD"
                    reasons.append(
                        f"[{face}] {label}이 {b * 100:.2f}% → {c * 100:.2f}% 로 나빠졌다 — 교환 판단 필요"
                    )

    if judged_faces == 0:
        verdict = "HOLD"
        reasons.append("판정 가능한 면이 하나도 없다 — 정답 권위·학습 겹침을 먼저 고칠 것")
    return {"verdict": verdict, "reasons": reasons, "deltas": deltas,
            "judged_faces": judged_faces}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", action="append", type=_pair, required=True,
                    help="면=records.jsonl (여러 번)")
    ap.add_argument("--eval", action="append", type=_pair, required=True,
                    help="면=eval.jsonl (여러 번)")
    ap.add_argument("--snapshot", default=None, help="기준선으로 저장할 경로")
    ap.add_argument("--baseline", default=None, help="이 기준선과 비교")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    recs = dict(args.records)
    evals = dict(args.eval)
    missing = set(recs) ^ set(evals)
    if missing:
        print(f"면 이름이 안 맞는다: {sorted(missing)}")
        return 2

    exclusions = load_suite_exclusions(POC)
    faces = [measure(f, recs[f], evals[f], exclusions) for f in sorted(recs)]

    print("=" * 86)
    print("면별 상태")
    print("=" * 86)
    print(f"{'면':<18} {'정답등급':<8} {'겹침':>5} {'판정가능':>8}  "
          f"{'미탐(자동)':>10} {'격상':>8} {'검수율':>8}")
    print("-" * 86)
    for f in faces:
        if "metrics" not in f:
            print(f"{f['face']:<18} {f.get('error')}")
            continue
        m = f["metrics"]

        def p(k):
            r = _rate(m, k)
            return "   -  " if r is None else f"{r * 100:5.1f}%"
        print(f"{f['face']:<18} {f['truth_tier']:<8} {f['training_overlap']:>5} "
              f"{'예' if f['usable_for_judgement'] else '아니오':>8}  "
              f"{p('high_grade_auto_confirm_fn_rate'):>10} {p('severe_overclass'):>8} "
              f"{p('review_load'):>8}")
        if f["excluded_reason"]:
            print(f"{'':>18} ⛔ {f['excluded_reason'][:64]}")

    out = {"faces": faces}

    if args.snapshot:
        p = POC / args.snapshot
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n기준선 저장: {p.relative_to(POC)}")
        usable = [f for f in faces if f.get("usable_for_judgement")]
        print(f"  판정에 쓸 수 있는 면 {len(usable)}/{len(faces)}개")
        if not usable:
            print("  ⚠ 판정 가능한 면이 0개다 — 이 기준선으로는 어떤 후보도 판정할 수 없다")
        return 0

    if args.baseline:
        base = json.loads((POC / args.baseline).read_text(encoding="utf-8"))
        res = judge(base, faces)
        out["judgement"] = res
        print()
        print("=" * 86)
        print(f"판정: {res['verdict']}   (판정한 면 {res['judged_faces']}개)")
        print("=" * 86)
        for r in res["reasons"]:
            print(f"  · {r}")
        if res["deltas"]:
            print()
            print("  축별 변화 (비율은 낮을수록 좋다)")
            for d in sorted(res["deltas"], key=lambda x: -abs(x["delta"]))[:12]:
                mark = "나빠짐" if d.get("worse") else "나아짐"
                print(f"    [{d['face']}] {d['label']:<14} "
                      f"{d['baseline'] * 100:6.2f}% → {d['candidate'] * 100:6.2f}%  {mark}")
        print()
        print("  ⚠ PROMOTE 는 '배포하라' 가 아니라 '배포를 검토할 수 있다' 는 뜻이다.")
        print("    배포는 지시가 있을 때만 한다.")

    if args.json:
        q = POC / args.json
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {q.relative_to(POC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
