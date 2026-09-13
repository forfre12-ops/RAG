#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""측정 리포트에 평가 권위 게이트를 걸어 claim_status 를 찍는다.

## 왜 (2026-09-14)

`reports/` 에 수치는 많은데(JSON 638개 · auto_confirm_rate 보유 66개) **그 수치로
무엇을 주장할 수 있는지**가 어디에도 안 적혀 있다. 그래서 사람이 요약할 때 빠뜨린다.
실제로 같은 날 두 시간 안에 두 번 잘못 보고했다.

이 도구는 리포트 하나를 받아 네 가지를 스스로 확인하고 판정을 붙인다.

    1) 정답 등급   평가셋의 label_source 를 전수로 세어 tier 를 정한다
                   (audit_eval_ground_truth.tier_of 를 그대로 import — 기준을 두 벌 두지 않는다)
    2) 학습 겹침   본문 해시로 학습풀과 교차한다. **안 재면 '겹침 0' 이 아니라 '미검사' 다**
    3) 목표 수치   지금은 미확정이다(RFP 90% · KL 80% · KPI 5% 충돌) → TARGET_UNRESOLVED
    4) 대표성      customer_real 0건이라 unprovable_now

판정은 `koipa.eval_authority.assess()` 가 한다. 이 스크립트는 증거를 모을 뿐이고
PASS 를 만들 권한이 없다.

사용:
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/audit_claim_status.py \
        --report reports/TIEBREAK_OFF/holdout109.json
    ... --glob "reports/**/*.json"      # 전수 소급
    ... --json reports/CLAIM_STATUS.json
"""
from __future__ import annotations

import argparse
import glob as globmod
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

#: 지금 학습에 실제로 쓰이는 풀. 겹침은 이 셋과 본다.
TRAIN_POOLS = (
    "datasets/gold_real/train_subset.jsonl",
    "datasets/labeled_p1_v5_clean/train.jsonl",
    "datasets/labeled_p1_v5_clean/val.jsonl",
    "datasets/labeled_p1_v5_clean/test.jsonl",
)

#: 계약 목표가 아직 하나로 안 정해졌다 — REMAINING_WORK.md C-1
TARGET_TODAY = TargetSpec.unresolved(
    conflicting=("RFP 재현율 90%", "KL 품질계획서 80%", "시나리오 KPI 미탐 5%")
)

#: 대표성을 증명할 경로가 지금 없다 — document_origin=customer_real 이 전 데이터셋 0건
REPRESENTATIVENESS_BLOCKER = (
    "고객사 실문서 분포가 있어야 한다 — document_origin=customer_real 현재 0건(실측 2026-09-14)"
)

TEXT_KEYS = ("text", "content", "body")
LABEL_KEYS = ("label", "target", "grade", "gold", "y")
HIGH = ("TS", "S1")


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def _h(t: str) -> str:
    return hashlib.sha256(_norm(t).encode("utf-8")).hexdigest()


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


def _train_hashes() -> set[str]:
    out: set[str] = set()
    for rel in TRAIN_POOLS:
        for row in _rows(POC / rel):
            t = _text(row)
            if t:
                out.add(_h(t))
    return out


def _suite_tier(eval_path: Path, *, high_only: bool) -> tuple[TruthTier, Counter, int]:
    """평가면의 정답 등급 — 섞여 있으면 **가장 낮은 것**이 그 셋의 한계다."""
    tiers: Counter = Counter()
    n = 0
    for row in _rows(eval_path):
        if high_only and _label(row) not in HIGH:
            continue
        n += 1
        tiers[tier_of(row)[0]] += 1
    worst_first = ("NONE", "UNKNOWN", "CIRCULAR", "BRONZE", "SILVER", "GOLD")
    for name in worst_first:
        if tiers.get(name):
            return TruthTier(name), tiers, n
    return TruthTier.NONE, tiers, n


def _overlap(eval_path: Path, train: set[str], *, high_only: bool) -> int:
    c = 0
    for row in _rows(eval_path):
        if high_only and _label(row) not in HIGH:
            continue
        t = _text(row)
        if t and _h(t) in train:
            c += 1
    return c


def audit_one(report_path: Path, train: set[str]) -> dict | None:
    try:
        rep = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(rep, dict):
        return None
    eval_rel = rep.get("eval_set")
    if not eval_rel or "high_grade_documents" not in rep:
        return None  # 이 도구가 판정할 수 있는 모양이 아니다

    eval_path = POC / str(eval_rel)
    tier, tier_counts, _n_eval = _suite_tier(eval_path, high_only=True)
    overlap = _overlap(eval_path, train, high_only=True)

    ev = EvalEvidence(
        suite_id=Path(str(eval_rel)).stem,
        # measure_serving_fnr 이 재는 것은 재현율이 아니다 — needs_review 는 제외된다
        metric=MetricName.HIGH_GRADE_AUTO_CONFIRM_FN_RATE,
        truth_tier=tier,
        n=int(rep.get("high_grade_documents") or 0),
        misses=int(rep.get("silent_miss") or 0),
        ci_method=CIMethod.CLOPPER_PEARSON_ONE_SIDED_95,
        target=TARGET_TODAY,
        representativeness=Representativeness.UNPROVABLE_NOW,
        representativeness_blocker=REPRESENTATIVENESS_BLOCKER,
        training_overlap_checked=True,
        training_overlap_count=overlap,
        ship_model_id=str(((rep.get("provenance") or {}).get("git_sha")) or ""),
        measured_at=str(((rep.get("provenance") or {}).get("measured_at")) or ""),
    )
    v = assess(ev)
    d = v.to_dict()
    d["report"] = report_path.relative_to(POC).as_posix()
    d["eval_set"] = str(eval_rel)
    d["truth_tier_counts"] = dict(tier_counts)
    d["headline"] = v.as_headline()
    return d


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--report", action="append", default=[], help="리포트 JSON (여러 번 가능)")
    ap.add_argument("--glob", default=None, help='예: "reports/**/*.json"')
    ap.add_argument("--json", default=None, help="판정 결과를 JSON 으로 저장")
    args = ap.parse_args()

    paths: list[Path] = [POC / p for p in args.report]
    if args.glob:
        paths += [Path(p) for p in globmod.glob(str(POC / args.glob), recursive=True)]
    paths = sorted({p.resolve() for p in paths})
    if not paths:
        print("대상 리포트가 없다 — --report 또는 --glob 을 주라")
        return 2

    print("학습풀 해시 적재 중…")
    train = _train_hashes()
    print(f"  학습풀 고유본문 {len(train):,}건 ({len(TRAIN_POOLS)}개 파일)")
    print()

    results = [r for r in (audit_one(p, train) for p in paths) if r]
    skipped = len(paths) - len(results)

    status_ct: Counter = Counter(r["claim_status"] for r in results)
    print("=" * 78)
    print(f"판정 대상 {len(results)}건 / 검사한 파일 {len(paths)}건 "
          f"(모양이 안 맞아 건너뜀 {skipped}건)")
    print("=" * 78)
    for r in results:
        print(f"\n  {r['headline']}")
        print(f"      리포트 {r['report']}")
        print(f"      정답등급 분포 {r['truth_tier_counts']} · 학습겹침 {r['training_overlap_count']}건")
    print("\n" + "=" * 78)
    print("요약: " + " · ".join(f"{k} {v}건" for k, v in status_ct.most_common()))
    print("=" * 78)
    print("⚠ PASS 가 0건인 것은 결함이 아니라 현재 상태다 — GOLD 정답이 0건이고")
    print("  계약 목표 수치가 아직 하나로 확정되지 않았다(RFP 90% · KL 80% · KPI 5%).")

    if args.json:
        out = POC / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"audited": len(results), "skipped": skipped,
             "status_counts": dict(status_ct), "results": results},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {out.relative_to(POC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
