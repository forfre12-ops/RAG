#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""'미탐률' 한 낱말이 가리키던 네 가지를 쪼개서 따로 낸다 — 각각 권위 게이트를 통과시킨다.

## 왜 (2026-09-14)

같은 36건에서 이렇게 갈린다(holdout109 실측).

    모델 raw 하향      6/36 = 16.67%   ← 서빙 가드 이전의 모델 판정
    서빙 최종 하향     2/36 =  5.56%   ← 가드 뒤 최종 등급. needs_review 도 미탐으로 센다
    그중 자동확정      1/36 =  2.78%   ← 검수로 간 것은 사람이 보므로 뺀 값
    검수로 포착        1건

**6배 차이다.** 그런데 지금까지 셋 다 "미탐률" 이라 불렸다. 그래서 계약 목표
"재현율 90%" 를 어느 지표로 재느냐에 따라 83.3% · 94.4% · 97.2% 가 된다 —
모델 단독이면 미달이고 서빙이면 통과다. 이건 우리가 고를 일이 아니라
**발주처가 정할 일**이고, 정해지기 전에는 어느 쪽도 PASS 로 낼 수 없다.

## 무엇을 세는가 (records.jsonl 한 벌로 전부 나온다)

    model_recall                      truth 고등급인데 model_grade 가 더 낮다
    serving_recall                    truth 고등급인데 predicted 가 더 낮다
                                      (status 무관 — 검수로 갔어도 등급은 틀린 것이다)
    high_grade_auto_confirm_fn_rate   위 중 status != needs_review 인 것만
    review_capture_rate               하향된 고등급 중 검수로 보낸 비율
    review_load                       전체 중 검수로 간 비율(업무량 축)

판정은 `koipa.eval_authority.assess()` 가 한다 — 이 스크립트는 세기만 하고
PASS 를 만들 권한이 없다.

사용:
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/measure_four_metrics.py \
        --records reports/TIEBREAK_OFF/holdout109.records.jsonl \
        --eval-set datasets/gold_real/holdout_eval.jsonl \
        --json reports/FOUR_METRICS.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "src"))
sys.path.insert(0, str(POC / "scripts"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from audit_eval_ground_truth import tier_of  # noqa: E402
from koipa.eval_authority import (  # noqa: E402
    CIMethod,
    EvalEvidence,
    MetricName,
    Representativeness,
    TargetSpec,
    TruthTier,
    assess,
)

GRADE_ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
HIGH = ("TS", "S1")
LOW = ("S2", "S3")
TRAIN_POOLS = (
    "datasets/gold_real/train_subset.jsonl",
    "datasets/labeled_p1_v5_clean/train.jsonl",
    "datasets/labeled_p1_v5_clean/val.jsonl",
    "datasets/labeled_p1_v5_clean/test.jsonl",
)
TEXT_KEYS = ("text", "content", "body")
LABEL_KEYS = ("label", "target", "grade", "gold", "y")

TARGET_TODAY = TargetSpec.unresolved(
    conflicting=("RFP 재현율 90%", "KL 품질계획서 80%", "시나리오 KPI 미탐 5%")
)
REPRESENTATIVENESS_BLOCKER = (
    "고객사 실문서 분포가 있어야 한다 — document_origin=customer_real 현재 0건(실측 2026-09-14)"
)


def _lower(pred: str | None, truth: str | None) -> bool:
    """pred 가 truth 보다 낮은 등급인가."""
    if not pred or not truth:
        return False
    if pred not in GRADE_ORDER or truth not in GRADE_ORDER:
        return False
    return GRADE_ORDER[pred] > GRADE_ORDER[truth]


def _rows(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _text(row: dict) -> str:
    for k in TEXT_KEYS:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _label(row: dict) -> str:
    for k in LABEL_KEYS:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _h(t: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", t or "").encode("utf-8")).hexdigest()


def suite_context(eval_path: Path) -> tuple[TruthTier, int, Counter]:
    """평가면의 정답 등급(가장 낮은 것이 한계)과 학습 겹침 — 고등급 행 기준."""
    train: set[str] = set()
    for rel in TRAIN_POOLS:
        for row in _rows(POC / rel):
            t = _text(row)
            if t:
                train.add(_h(t))
    tiers: Counter = Counter()
    overlap = 0
    for row in _rows(eval_path):
        if _label(row) not in HIGH:
            continue
        tiers[tier_of(row)[0]] += 1
        t = _text(row)
        if t and _h(t) in train:
            overlap += 1
    for name in ("NONE", "UNKNOWN", "CIRCULAR", "BRONZE", "SILVER", "GOLD"):
        if tiers.get(name):
            return TruthTier(name), overlap, tiers
    return TruthTier.NONE, overlap, tiers


def compute(records: list[dict]) -> dict:
    """records 한 벌에서 네 지표 + 두 운영 지표를 센다."""
    n_all = len(records)
    hi = [r for r in records if r.get("truth") in HIGH]
    n_hi = len(hi)

    model_miss = [r for r in hi if _lower(r.get("model_grade") or r.get("predicted"), r["truth"])]
    serving_miss = [r for r in hi if _lower(r.get("predicted"), r["truth"])]
    auto_miss = [r for r in serving_miss if r.get("status") != "needs_review"]
    captured = [r for r in serving_miss if r.get("status") == "needs_review"]
    review_load = [r for r in records if r.get("status") == "needs_review"]

    def rate(k: int, n: int) -> float | None:
        return (k / n) if n else None

    # 과탐 축 — EVAL_CRITERIA 제4조 "동반 필수: 과탐률. 미탐만 보면 속는다".
    # 과소분류 7건으로 가장 좋아 보인 판의 S3 과탐이 25/25 = 100% 였던 전례가 있다.
    lo = [r for r in records if r.get("truth") in LOW]
    n_lo = len(lo)
    model_over = [r for r in lo if _lower(r["truth"], r.get("model_grade") or r.get("predicted"))]
    serving_over = [r for r in lo if _lower(r["truth"], r.get("predicted"))]
    # 격상(severe) — 공개·내부문서를 고등급(TS·S1)이라 부른 것. 감리 185(가)가 지목한 축.
    severe_over = [r for r in lo if r.get("predicted") in HIGH]
    auto_over = [r for r in serving_over if r.get("status") != "needs_review"]

    return {
        "n_all": n_all,
        "n_high_grade": n_hi,
        "n_low_grade": n_lo,
        "model_overclass": {"hits": len(model_over), "n": n_lo, "rate": rate(len(model_over), n_lo)},
        "serving_overclass": {"hits": len(serving_over), "n": n_lo, "rate": rate(len(serving_over), n_lo)},
        "severe_overclass": {"hits": len(severe_over), "n": n_lo, "rate": rate(len(severe_over), n_lo)},
        "auto_confirmed_overclass": {"hits": len(auto_over), "n": n_lo, "rate": rate(len(auto_over), n_lo)},
        "model_recall": {"misses": len(model_miss), "n": n_hi, "rate": rate(len(model_miss), n_hi)},
        "serving_recall": {"misses": len(serving_miss), "n": n_hi, "rate": rate(len(serving_miss), n_hi)},
        "high_grade_auto_confirm_fn_rate": {"misses": len(auto_miss), "n": n_hi, "rate": rate(len(auto_miss), n_hi)},
        "review_capture_rate": {
            "captured": len(captured), "underclassified": len(serving_miss),
            "rate": rate(len(captured), len(serving_miss)),
        },
        "review_load": {"reviewed": len(review_load), "n": n_all, "rate": rate(len(review_load), n_all)},
        "model_grade_missing": sum(1 for r in records if not r.get("model_grade")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", action="append", required=True,
                    help="measure_serving_fnr 이 낸 *.records.jsonl")
    ap.add_argument("--eval-set", action="append", required=True,
                    help="그 records 의 원 평가셋 (정답 등급·겹침 판정용)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    if len(args.records) != len(args.eval_set):
        print("--records 와 --eval-set 개수가 다르다")
        return 2

    out_all = []
    for rec_rel, eval_rel in zip(args.records, args.eval_set):
        rec_path, eval_path = POC / rec_rel, POC / eval_rel
        records = list(_rows(rec_path))
        if not records:
            print(f"[건너뜀] {rec_rel} — 레코드 없음")
            continue
        tier, overlap, tier_ct = suite_context(eval_path)
        m = compute(records)
        # stem 이 records·raw·train 처럼 흔한 말이면 폴더명을 붙인다 — 표에서 구분이 안 된다
        suite = eval_path.stem
        if suite in ("records", "raw", "train", "val", "test", "eval"):
            suite = f"{eval_path.parent.name}/{suite}"

        print("=" * 78)
        print(f"{suite}  ·  고등급 {m['n_high_grade']}건 / 전체 {m['n_all']}건")
        print(f"  정답등급 {tier.value} {dict(tier_ct)} · 학습겹침 {overlap}건")
        print("=" * 78)

        verdicts = {}
        for key, metric in (
            ("model_recall", MetricName.MODEL_RECALL),
            ("serving_recall", MetricName.SERVING_RECALL),
            ("high_grade_auto_confirm_fn_rate", MetricName.HIGH_GRADE_AUTO_CONFIRM_FN_RATE),
        ):
            d = m[key]
            ev = EvalEvidence(
                suite_id=suite, metric=metric, truth_tier=tier,
                n=d["n"], misses=d["misses"],
                ci_method=CIMethod.CLOPPER_PEARSON_ONE_SIDED_95,
                target=TARGET_TODAY,
                representativeness=Representativeness.UNPROVABLE_NOW,
                representativeness_blocker=REPRESENTATIVENESS_BLOCKER,
                training_overlap_checked=True, training_overlap_count=overlap,
            )
            v = assess(ev)
            verdicts[key] = v.to_dict()
            r = d["rate"]
            print(f"\n  {metric.value}")
            print(f"    {d['misses']}/{d['n']} = {'-' if r is None else f'{r * 100:.2f}%'}"
                  f"   상한 {'-' if v.ci_upper is None else f'{v.ci_upper * 100:.2f}%'}"
                  f"   → {v.status.value}")

        print(f"\n  review_capture_rate  {m['review_capture_rate']['captured']}"
              f"/{m['review_capture_rate']['underclassified']} 하향분을 검수로 포착")
        print(f"  review_load          {m['review_load']['reviewed']}/{m['review_load']['n']}"
              f" = {m['review_load']['rate'] * 100:.1f}% 가 검수로")
        out_all.append({
            "suite_id": suite, "records": rec_rel, "eval_set": eval_rel,
            "truth_tier": tier.value, "truth_tier_counts": dict(tier_ct),
            "training_overlap_count": overlap, "metrics": m, "verdicts": verdicts,
        })

    print("\n" + "=" * 78)
    print("★ 같은 문서에서 지표가 갈리는 폭")
    print("=" * 78)
    for o in out_all:
        m = o["metrics"]
        def pct(k):
            r = m[k]["rate"]
            return "-" if r is None else f"{r * 100:5.2f}%"
        print(f"  {o['suite_id']:<24} 모델 {pct('model_recall')} · "
              f"서빙 {pct('serving_recall')} · 자동확정 {pct('high_grade_auto_confirm_fn_rate')}")
    print()
    print("⚠ 계약 목표 '재현율 90%' 가 이 셋 중 무엇인지 정해지지 않았다.")
    print("  검수 라우팅을 성공으로 셀 것인가 — 발주처 확정 사항이다.")

    if args.json:
        out = POC / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"suites": out_all}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\n저장: {out.relative_to(POC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
