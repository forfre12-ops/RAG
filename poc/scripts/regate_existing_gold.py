"""[P1c] 기존 정본 gold를 새 P1 게이트로 재평가(re-gate) — 정본 보호, 강등은 needs_review.

배경: 구 정본(classification_gold.jsonl)은 conf 게이트(구 path A/B/C)로 만들어졌다. P1 재설계
게이트(consensus.evaluate_consensus: 합의+실제 근거 span+self-consistency)로 다시 통과시키면,
룰 근거 없는 단일 LLM 라벨(llm_judge_primary 다수)은 gold가 못 되고 needs_review로 **강등**된다
(삭제 아님 — 사람 검토 후보로 남음, review_priority 정렬).

스코프: label_source ∈ {llm_judge_primary, llm_judge_consensus}만 재평가한다. 법적근거/큐레이션
(public_definitive·koipa_case_based·nkt_designated·codex_review)은 이 게이트 밖 → gate_version=external로 보존.

재평가 방식(네트워크 없음): 저장된 text로 룰 엔진을 다시 돌려 rule_grade·has_real_evidence를
재계산하고, LLM 등급은 저장값(llm_grade)을 사용한다. self_consistency는 레거시 행에 측정값이
없으므로 1.0(가장 관대) 가정 — 즉 결과는 '새 게이트 자동 gold의 상한'이다.

정본 보호: --dry-run(기본 권장)은 파일 미작성·통계만. 실제 출력 시에도 정본은 .bak 백업 후
run-스코프 side 파일(builds/regate_<ts>.jsonl: gold_candidate, regate_review_<ts>.jsonl: needs_review)
에만 쓰고 정본을 덮어쓰지 않는다(승격은 promote_golden_candidates 별도 게이트).

사용:
  python scripts/regate_existing_gold.py --dry-run
  python scripts/regate_existing_gold.py --gold datasets/gold_real/classification_gold.jsonl
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from koipa.modules.m3_labeling import LabelingPipeline  # noqa: E402
from koipa.modules.m3_labeling.consensus import evaluate_consensus  # noqa: E402
from koipa.modules.m3_labeling.rule_engine import has_real_evidence  # noqa: E402
from koipa.golden_tiers import tier_of  # noqa: E402

DEFAULT_GOLD = Path("datasets/gold_real/classification_gold.jsonl")
# 이 게이트로 재평가할 라벨 출처(LLM-판정 경로). 나머지는 게이트 밖(보존).
_REGATE_SOURCES = {"llm_judge_primary", "llm_judge_consensus"}
_LABELS = ("TS", "S1", "S2", "S3")


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _save_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="기존 gold를 P1 게이트로 재평가(정본 보호).")
    ap.add_argument("--gold", default=str(DEFAULT_GOLD))
    ap.add_argument("--out-dir", default="datasets/gold_real/builds")
    ap.add_argument("--min-self-consistency", type=float, default=0.67)
    ap.add_argument("--dry-run", action="store_true", help="파일 미작성 — 통계만(권장 1순위)")
    args = ap.parse_args()

    gold_path = Path(args.gold)
    rows = _load_jsonl(gold_path)
    if not rows:
        print(f"[ERROR] gold 없음/빈 파일: {gold_path}", file=sys.stderr)
        return 2

    pipe = LabelingPipeline()  # 룰 전용(LLM fallback off) — 네트워크 없음

    kept_external: list[dict] = []   # 게이트 밖(법적근거/큐레이션) — 보존
    new_gold: list[dict] = []        # 새 게이트 통과 → gold_candidate
    demoted: list[dict] = []         # 강등 → needs_review

    old_gold_by_src = Counter(r.get("label_source") for r in rows)
    new_status = Counter()
    new_gold_grade = Counter()
    demote_reason = Counter()

    for r in rows:
        src = r.get("label_source")
        if src not in _REGATE_SOURCES:
            r2 = dict(r)
            r2["gate_version"] = "external"
            r2["tier"] = tier_of(r2)
            kept_external.append(r2)
            continue

        text = r.get("text") or ""
        rr = pipe.label(text)
        rule_grade = rr.grade.value if hasattr(rr.grade, "value") else str(rr.grade)
        evidence = bool(rr.rule_result) and has_real_evidence(rr.rule_result)
        llm_grade = r.get("llm_grade") or r.get("label") or "S3"
        if llm_grade not in _LABELS:
            llm_grade = "S3"

        verdict = evaluate_consensus(
            rule_grade,
            llm_grade,
            has_real_evidence=evidence,
            self_consistency=1.0,            # 레거시 행: 미측정 → 상한 가정
            shadow_grade=None,
            airgap=False,
            min_self_consistency=args.min_self_consistency,
            sort_conf=float(r.get("llm_confidence") or 0.0),
        )
        new_status[verdict.status] += 1
        r2 = dict(r)
        r2.update(
            label=llm_grade if verdict.is_gold else None,
            label_source=verdict.label_source,
            review_status=verdict.review_status,
            status=verdict.status,
            rule_grade=rule_grade,
            agreement=verdict.agree,
            flags=list(verdict.flags),
            review_priority=round(verdict.review_priority, 4),
            has_real_evidence=evidence,
            gate_version="v2_agreement_evidence",
            regate_from=src,
        )
        r2["tier"] = tier_of(r2)
        if verdict.is_gold:
            new_gold.append(r2)
            new_gold_grade[llm_grade] += 1
        else:
            demoted.append(r2)
            demote_reason[verdict.status] += 1

    # ── 요약 ──
    print("=== 기존 gold 재평가 (P1 게이트) ===")
    print(f"입력: {len(rows)}건  | 게이트 밖 보존(법적/큐레이션): {len(kept_external)}건")
    print(f"재평가 대상(llm_judge_*): {len(new_gold) + len(demoted)}건")
    print(f"  → 새 자동 gold_candidate: {len(new_gold)}건  {dict(new_gold_grade)}")
    print(f"  → needs_review 강등     : {len(demoted)}건  {dict(demote_reason)}")
    print()
    print(f"구 label_source 분포: {dict(old_gold_by_src)}")
    tier_dist = Counter(tier_of(r) for r in (new_gold + demoted + kept_external))
    print(f"신 tier 분포: {dict(tier_dist)}")
    total_auto = len(new_gold) + len(kept_external)
    print(
        f"\n정본 평가용 자동 인정 총계: 구 {len(rows)}건 → "
        f"신 {total_auto}건 (새 gold {len(new_gold)} + 보존 {len(kept_external)}); "
        f"강등 {len(demoted)}건은 사람 검토 큐로"
    )

    if args.dry_run:
        print("\n[DRY-RUN] 파일 미작성 — 정본 무변경")
        return 0

    # 정본 보호: 백업 + side 파일만. 정본 덮어쓰기 안 함.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = gold_path.with_suffix(gold_path.suffix + f".v1_bak_{ts}")
    shutil.copy2(gold_path, bak)
    out = Path(args.out_dir)
    gold_out = out / f"regate_gold_{ts}.jsonl"
    review_out = out / f"regate_review_{ts}.jsonl"
    _save_jsonl(gold_out, new_gold + kept_external)
    _save_jsonl(review_out, demoted)
    print(f"\n[OK] 정본 백업 → {bak}")
    print(f"[OK] 새 gold(+보존) → {gold_out} ({len(new_gold) + len(kept_external)}건)")
    print(f"[OK] 강등(검토 큐) → {review_out} ({len(demoted)}건)")
    print("[INFO] 정본은 미변경. 승격은 promote_golden_candidates(별도 게이트)로.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
