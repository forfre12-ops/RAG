"""
고객사 검수 결과(CSV 또는 JSONL)를 datasets/corrections/ 에 적재.

설계 원칙:
  - 원문이 없어도 동작: doc_id + model_label + human_label + reason_code 로 충분
  - 원문이 있으면 gold_real에도 편입 가능 (--merge-gold)
  - 중복 doc_id는 최신 검수 결과로 덮어씀
  - 배포처(폐쇄망)에서 사람이 작성한 CSV를 그대로 받아들임

입력 포맷 (CSV 헤더 또는 JSONL 필드):
  doc_id          - 필수
  model_label     - 필수  (TS/S1/S2/S3)
  human_label     - 필수  (TS/S1/S2/S3)
  review_decision - 권장  (correct|corrected|rejected|uncertain)
  reason_code     - 권장  (contains_customer_db|pii_detected|trade_secret|low_sensitivity|...)
  reason_text     - 선택
  reviewer_id     - 권장
  text            - 선택  (있으면 gold_real 편입 + DB DocumentLabel 기록 가능)

사용:
  python scripts/import_review_corrections.py corrections.csv
  python scripts/import_review_corrections.py corrections.jsonl --merge-gold
  python scripts/import_review_corrections.py corrections.csv --dry-run
  python scripts/import_review_corrections.py corrections.csv --write-db [--tenant-id default]
  python scripts/import_review_corrections.py corrections.csv --merge-gold --write-db
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

VALID_LABELS = {"TS", "S1", "S2", "S3"}
VALID_DECISIONS = {"correct", "corrected", "rejected", "uncertain"}

CORRECTIONS_DIR = Path("datasets/corrections")
GOLD_REAL_PATH  = Path("datasets/gold_real/classification_gold.jsonl")


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _save_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_input(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            return [row for row in reader]
    else:  # .jsonl or .json
        return _load_jsonl(path)


def validate_record(row: dict, idx: int) -> tuple[dict | None, list[str]]:
    errors: list[str] = []

    doc_id = (row.get("doc_id") or "").strip()
    if not doc_id:
        errors.append(f"[row {idx}] doc_id 없음")

    model_label = (row.get("model_label") or "").strip().upper()
    human_label = (row.get("human_label") or "").strip().upper()
    if model_label not in VALID_LABELS:
        errors.append(f"[row {idx}] model_label 무효: {model_label!r}")
    if human_label not in VALID_LABELS:
        errors.append(f"[row {idx}] human_label 무효: {human_label!r}")

    if errors:
        return None, errors

    decision = (row.get("review_decision") or "").strip().lower()
    if not decision:
        # 자동 추론
        decision = "correct" if model_label == human_label else "corrected"

    return {
        "doc_id":           doc_id,
        "model_label":      model_label,
        "human_label":      human_label,
        "review_decision":  decision,
        "reason_code":      (row.get("reason_code") or "").strip(),
        "reason_text":      (row.get("reason_text") or "").strip(),
        "reviewer_id":      (row.get("reviewer_id") or "human").strip(),
        "review_status":    "accepted",
        "text":             (row.get("text") or "").strip(),
        "domain":           (row.get("domain") or "").strip(),
        "document_type":    (row.get("document_type") or "").strip(),
        "import_date":      datetime.now().strftime("%Y-%m-%d"),
    }, []


def merge_into_gold(records: list[dict], dry_run: bool) -> int:
    """text가 있는 human_label 정정 건을 gold_real에 편입."""
    mergeable = [
        r for r in records
        if r.get("text") and r.get("review_decision") in ("correct", "corrected")
    ]
    if not mergeable:
        print("[INFO] gold_real 편입 가능 건 없음 (text 필드 필요)")
        return 0

    existing_ids: set[str] = set()
    if GOLD_REAL_PATH.exists():
        for rec in _load_jsonl(GOLD_REAL_PATH):
            if rec.get("doc_id"):
                existing_ids.add(rec["doc_id"])

    new_gold: list[dict] = []
    for r in mergeable:
        if r["doc_id"] in existing_ids:
            print(f"[SKIP] 이미 gold_real 존재: {r['doc_id']}")
            continue
        new_gold.append({
            "doc_id":         r["doc_id"],
            "text":           r["text"],
            "label":          r["human_label"],
            "label_source":   "human_review",
            "reviewer_id":    r["reviewer_id"],
            "review_status":  "accepted",
            "source":         "real_deidentified",
            "domain":         r.get("domain", ""),
            "document_type":  r.get("document_type", ""),
            "evidence_spans": [],
            "notes":          (
                f"correction import: model={r['model_label']} → human={r['human_label']}, "
                f"reason={r.get('reason_code','')}"
            ),
        })

    if dry_run:
        print(f"[DRY-RUN] gold_real 편입 예정: {len(new_gold)}건")
        return len(new_gold)

    if new_gold:
        with open(GOLD_REAL_PATH, "a", encoding="utf-8") as f:
            for rec in new_gold:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[OK] gold_real에 {len(new_gold)}건 편입 (human_review)")

    return len(new_gold)


def _doc_id_to_uuid(doc_id: str) -> uuid.UUID:
    """gold_real의 SHA1 해시 doc_id → UUID 변환 (deterministic).

    SHA1은 40자 hex → 앞 32자를 UUID 형식으로 변환.
    이미 UUID 형식이면 그대로 파싱.
    """
    try:
        return uuid.UUID(doc_id)
    except ValueError:
        # SHA1 hex (40자) → 앞 32자로 UUID 생성
        padded = (doc_id + "0" * 32)[:32]
        return uuid.UUID(padded)


def write_to_db(records: list[dict], tenant_id: str, dry_run: bool) -> dict:
    """human_review 검수 결과를 document_labels 테이블에 upsert.

    text 있는 레코드:
      1. documents 테이블 upsert (없으면 신규 생성)
      2. document_labels upsert (labeled_by=human_review, is_verified=True)

    text 없는 레코드:
      - documents에 이미 있으면 document_labels만 upsert
      - 없으면 skip (문서 원문 없이 label 기록 불가)
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from lloydk.db import SessionLocal
    from lloydk.db.models import Document, DocumentLabel
    from lloydk.repositories.classify_repo import ClassifyRepo

    written = skipped = 0
    errors: list[str] = []

    if dry_run:
        for r in records:
            if r.get("text"):
                print(f"  [DRY-DB] DocumentLabel upsert: doc_id={r['doc_id']} "
                      f"label={r['human_label']} reviewer={r['reviewer_id']}")
        print(f"[DRY-DB] {len(records)}건 예정")
        return {"written": 0, "skipped": 0, "dry_run": True}

    db = SessionLocal()
    try:
        repo = ClassifyRepo(db)

        for r in records:
            if r.get("review_decision") not in ("correct", "corrected"):
                skipped += 1
                continue

            raw_doc_id = r["doc_id"]
            doc_uuid = _doc_id_to_uuid(raw_doc_id)
            level_id = repo.level_id_by_code(r["human_label"])
            if level_id is None:
                errors.append(f"level_id 없음: {r['human_label']}")
                skipped += 1
                continue

            # 1. Document upsert (text 있을 때만 신규 생성)
            doc_exists = repo.document_exists(doc_uuid)
            if not doc_exists:
                text = r.get("text", "")
                if not text:
                    skipped += 1
                    continue
                doc = Document(
                    doc_id=doc_uuid,
                    tenant_id=tenant_id,
                    filename=f"human_review_{raw_doc_id[:8]}.txt",
                    source_format="txt",
                    text_preview=text[:2000],
                    char_count=len(text),
                    file_hash=hashlib.sha256(text.encode()).hexdigest(),
                    processing_status="done",
                    created_by=r.get("reviewer_id", "human"),
                    metadata_={"source": "human_review_import", "original_doc_id": raw_doc_id},
                )
                db.add(doc)
                db.flush()

            # 2. DocumentLabel upsert
            stmt = pg_insert(DocumentLabel).values(
                doc_id=doc_uuid,
                level_id=level_id,
                labeled_by="human_review",
                labeler_id=r.get("reviewer_id", "human"),
                is_verified=True,
                verified_by=r.get("reviewer_id", "human"),
                notes=(
                    f"reason_code={r.get('reason_code','')} "
                    f"model={r.get('model_label','')}→human={r['human_label']}"
                ),
            ).on_conflict_do_update(
                index_elements=["doc_id"],
                set_={
                    "level_id":    level_id,
                    "labeler_id":  r.get("reviewer_id", "human"),
                    "is_verified": True,
                    "verified_by": r.get("reviewer_id", "human"),
                    "notes": (
                        f"reason_code={r.get('reason_code','')} "
                        f"model={r.get('model_label','')}→human={r['human_label']}"
                    ),
                },
            )
            db.execute(stmt)
            written += 1

        db.commit()
        print(f"[DB] DocumentLabel upsert: written={written}, skipped={skipped}")
        if errors:
            for e in errors:
                print(f"  [DB-WARN] {e}", file=sys.stderr)
    except Exception as exc:
        db.rollback()
        print(f"[DB-ERROR] rollback: {exc}", file=sys.stderr)
        raise
    finally:
        db.close()

    return {"written": written, "skipped": skipped, "errors": errors}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("input", help="검수 결과 파일 (CSV 또는 JSONL)")
    p.add_argument("--out-dir",    default=str(CORRECTIONS_DIR))
    p.add_argument("--merge-gold", action="store_true",
                   help="text 있는 건을 gold_real/classification_gold.jsonl 에도 편입")
    p.add_argument("--write-db",   action="store_true",
                   help="document_labels 테이블에 human_review label upsert (DB 연결 필요)")
    p.add_argument("--tenant-id",  default="default",
                   help="--write-db 시 사용할 tenant_id (기본: default)")
    p.add_argument("--dry-run",    action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    src = Path(args.input)
    if not src.exists():
        print(f"[ERROR] 입력 파일 없음: {src}", file=sys.stderr)
        return 1

    rows = load_input(src)
    print(f"[INFO] 입력 {len(rows)}건 로드")

    valid: list[dict] = []
    all_errors: list[str] = []
    for i, row in enumerate(rows):
        rec, errs = validate_record(row, i + 1)
        if errs:
            all_errors.extend(errs)
        else:
            valid.append(rec)

    if all_errors:
        for e in all_errors:
            print(f"[WARN] {e}", file=sys.stderr)

    if not valid:
        print("[ERROR] 유효한 레코드 없음", file=sys.stderr)
        return 1

    # 통계
    from collections import Counter
    decisions = Counter(r["review_decision"] for r in valid)
    corrected = [r for r in valid if r["review_decision"] == "corrected"]
    print(f"[INFO] 유효 {len(valid)}건: {dict(decisions)}")
    print(f"[INFO] 정정 건: {len(corrected)}건 "
          f"({Counter((r['model_label'],r['human_label']) for r in corrected)})")

    if args.dry_run:
        print("[DRY-RUN] 실제 파일 수정 없음")
        if args.merge_gold:
            merge_into_gold(valid, dry_run=True)
        if args.write_db:
            write_to_db(valid, tenant_id=args.tenant_id, dry_run=True)
        return 0

    # ── corrections/ 적재 ───────────────────────────────────────────────────
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 기존 corrections 로드 + doc_id 기준 병합 (최신 우선)
    master_path = out_dir / "corrections_master.jsonl"
    existing: dict[str, dict] = {}
    if master_path.exists():
        for rec in _load_jsonl(master_path):
            existing[rec["doc_id"]] = rec

    updated = 0
    for rec in valid:
        if rec["doc_id"] in existing:
            updated += 1
        existing[rec["doc_id"]] = rec

    _save_jsonl(master_path, list(existing.values()))
    print(f"[OK] corrections_master.jsonl → {len(existing)}건 "
          f"(신규 {len(valid)-updated}, 갱신 {updated})")

    # 이번 배치 별도 저장 (날짜별 이력)
    batch_name = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    _save_jsonl(out_dir / batch_name, valid)
    print(f"[OK] 배치 이력 → {out_dir / batch_name}")

    # ── gold_real 편입 (--merge-gold) ────────────────────────────────────────
    if args.merge_gold:
        merge_into_gold(valid, dry_run=False)

    # ── DB write (--write-db) ────────────────────────────────────────────────
    if args.write_db:
        db_result = write_to_db(valid, tenant_id=args.tenant_id, dry_run=False)
        print(f"[OK] DB write: {db_result}")

    # ── 정정 요약 CSV ─────────────────────────────────────────────────────────
    summary_path = out_dir / "corrections_summary.csv"
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["doc_id","model_label","human_label",
                        "review_decision","reason_code","reviewer_id","import_date"],
        )
        writer.writeheader()
        for rec in list(existing.values()):
            writer.writerow({k: rec.get(k, "") for k in writer.fieldnames})
    print(f"[OK] 요약 CSV → {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
