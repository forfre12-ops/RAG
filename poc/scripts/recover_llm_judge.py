"""
uncertain_cases.jsonl에서 LLM 상향 판정 케이스를 gold_real로 회수.

회수 기준 (llm_judge_primary):
  - rule_grade = S3  (룰 라벨러 언더클래스 가능성)
  - llm_grade  ∈ {S1, S2}  (LLM이 더 높은 민감도 판정)
  - llm_confidence >= --min-conf (기본 0.85)

제외 패턴 (uncertain 유지):
  - LLM downgrade 케이스  (rule > llm)  →  판단 불일치, 사람 검수 필요
  - rule=TS 케이스                        →  TS 관련 판단은 별도 프로세스

출력:
  - gold_real/classification_gold.jsonl  에 llm_judge_primary 건 추가
  - uncertain_cases.jsonl                에서 회수된 건 제거
  - datasets/corrections/recovery_log.jsonl  에 이력 기록

사용:
  python scripts/recover_llm_judge.py
  python scripts/recover_llm_judge.py --min-conf 0.90  --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── 회수 기준 ──────────────────────────────────────────────────────────────────
RULE_GRADE_ELIGIBLE = {"S3"}        # 룰 라벨러가 이 등급 → 언더클래스 의심
LLM_GRADE_UPGRADE   = {"S1", "S2"}  # LLM이 이 등급으로 상향 → 회수 대상


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _save_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def is_recoverable(rec: dict, min_conf: float) -> bool:
    rule  = rec.get("rule_grade", "")
    llm   = rec.get("llm_grade", "")
    conf  = rec.get("llm_confidence", 0.0)
    return (
        rule in RULE_GRADE_ELIGIBLE
        and llm in LLM_GRADE_UPGRADE
        and conf >= min_conf
    )


def build_gold_record(rec: dict) -> dict:
    """uncertain_cases 레코드 → gold_real 레코드 변환."""
    text = rec.get("text", "")
    return {
        "doc_id":         rec.get("doc_id") or _sha1(text)[:16],
        "text":           text,
        "label":          rec["llm_grade"],
        "label_source":   "llm_judge_primary",
        "reviewer_id":    rec.get("reviewer_id", "llm_judge"),
        "review_status":  "accepted",
        "source":         rec.get("source", "unknown"),
        "domain":         rec.get("domain", ""),
        "document_type":  rec.get("document_type", ""),
        "original_file":  rec.get("original_file", ""),
        # 원래 판정 정보 보존
        "rule_grade":          rec.get("rule_grade"),
        "rule_confidence":     rec.get("rule_confidence"),
        "llm_grade":           rec.get("llm_grade"),
        "llm_confidence":      rec.get("llm_confidence"),
        "llm_rationale":       rec.get("llm_rationale"),
        "recovery_reason":     "rule_underclass_llm_upgrade",
        "evidence_spans":      [],
        "notes": (
            f"rule={rec.get('rule_grade')} conf={rec.get('rule_confidence'):.3f} → "
            f"llm={rec.get('llm_grade')} conf={rec.get('llm_confidence'):.2f} 상향 회수"
        ),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--uncertain",   default="datasets/gold_real/uncertain_cases.jsonl")
    p.add_argument("--gold-real",   default="datasets/gold_real/classification_gold.jsonl")
    p.add_argument("--corrections", default="datasets/corrections/recovery_log.jsonl")
    p.add_argument("--min-conf",    type=float, default=0.85)
    p.add_argument("--dry-run",     action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    uncertain_path = Path(args.uncertain)
    gold_path      = Path(args.gold_real)
    corr_path      = Path(args.corrections)

    if not uncertain_path.exists():
        print(f"[ERROR] {uncertain_path} 없음", file=sys.stderr)
        return 1

    uncertain = _load_jsonl(uncertain_path)
    print(f"[INFO] uncertain_cases 총 {len(uncertain)}건")

    # 기존 gold_real doc_id/hash 수집
    existing_ids: set[str]    = set()
    existing_hashes: set[str] = set()
    if gold_path.exists():
        for rec in _load_jsonl(gold_path):
            if rec.get("doc_id"):
                existing_ids.add(rec["doc_id"])
            existing_hashes.add(_sha1(rec.get("text", "")))

    # ── 회수 대상 선별 ──────────────────────────────────────────────────────
    to_recover: list[dict] = []
    skipped_dup: list[dict] = []
    kept_uncertain: list[dict] = []

    for rec in uncertain:
        if not is_recoverable(rec, args.min_conf):
            kept_uncertain.append(rec)
            continue

        doc_id = rec.get("doc_id") or _sha1(rec.get("text", ""))[:16]
        h = _sha1(rec.get("text", ""))
        if doc_id in existing_ids or h in existing_hashes:
            print(f"[SKIP] 이미 gold_real 존재: {doc_id}")
            skipped_dup.append(rec)
            kept_uncertain.append(rec)
            continue

        to_recover.append(rec)
        existing_ids.add(doc_id)
        existing_hashes.add(h)

    print(f"[INFO] 회수 대상: {len(to_recover)}건  "
          f"(min_conf={args.min_conf}, rule=S3→llm=S2/S1)")
    print(f"[INFO] uncertain 유지: {len(kept_uncertain)}건  "
          f"(LLM downgrade·TS 관련·중복)")

    if not to_recover:
        print("[OK] 회수할 건 없음")
        return 0

    # 분포 요약
    from collections import Counter
    lbl_cnt = Counter(r["llm_grade"] for r in to_recover)
    print(f"[INFO] 회수 등급 분포: {dict(lbl_cnt)}")

    if args.dry_run:
        print("[DRY-RUN] 실제 파일 수정 없음")
        for r in to_recover[:3]:
            print(f"  doc_id={r.get('doc_id')} rule={r.get('rule_grade')} "
                  f"→ llm={r.get('llm_grade')} conf={r.get('llm_confidence'):.2f}")
        return 0

    # ── gold_real 추가 ────────────────────────────────────────────────────────
    gold_records = [build_gold_record(r) for r in to_recover]
    with open(gold_path, "a", encoding="utf-8") as f:
        for rec in gold_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[OK] gold_real에 {len(gold_records)}건 추가 (label_source=llm_judge_primary)")

    # ── uncertain_cases 갱신 (회수분 제거) ────────────────────────────────────
    _save_jsonl(uncertain_path, kept_uncertain)
    print(f"[OK] uncertain_cases → {len(kept_uncertain)}건 (회수분 {len(to_recover)}건 제거)")

    # ── 회수 이력 기록 ────────────────────────────────────────────────────────
    log_entries = []
    for orig, gold in zip(to_recover, gold_records):
        log_entries.append({
            "doc_id":          gold["doc_id"],
            "rule_grade":      orig.get("rule_grade"),
            "rule_confidence": orig.get("rule_confidence"),
            "llm_grade":       orig.get("llm_grade"),
            "llm_confidence":  orig.get("llm_confidence"),
            "recovered_label": gold["label"],
            "label_source":    "llm_judge_primary",
            "recovery_date":   datetime.now().strftime("%Y-%m-%d"),
            "source":          orig.get("source", ""),
            "domain":          orig.get("domain", ""),
        })

    # 기존 로그에 이어쓰기
    corr_path.parent.mkdir(parents=True, exist_ok=True)
    with open(corr_path, "a", encoding="utf-8") as f:
        for entry in log_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[OK] 회수 이력 → {corr_path} ({len(log_entries)}건)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
