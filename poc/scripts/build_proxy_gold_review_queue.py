"""Build a deterministic, single-reviewer queue from the frozen 1,000 candidates."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "datasets" / "proxy_gold" / "frozen_candidates" / "proxy_gold_candidates_1000.v1.manifest.json"
OUT_DIR = ROOT / "datasets" / "proxy_gold" / "review_queues"
JSON_OUT = OUT_DIR / "proxy_gold_review_queue_1000.v1.json"
CSV_OUT = OUT_DIR / "proxy_gold_review_queue_1000.v1.csv"
PER_BATCH = {"TS": 20, "S1": 25, "S2": 25, "S3": 30}


def ordered(entries: list[dict[str, object]], manifest_hash: str) -> list[dict[str, object]]:
    return sorted(
        entries,
        key=lambda row: hashlib.sha256(f"{manifest_hash}:{row['doc_id']}".encode("utf-8")).hexdigest(),
    )


def main() -> int:
    if JSON_OUT.exists() or CSV_OUT.exists():
        raise FileExistsError("review queue already exists; do not silently replace a review order")
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    entries = frozen["entries"]
    if len(entries) != 1000:
        raise RuntimeError("review queue requires frozen 1,000 candidate manifest")
    buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        buckets[str(entry["proposed_grade"])].append(entry)
    for grade, per_batch in PER_BATCH.items():
        buckets[grade] = ordered(buckets[grade], str(frozen["manifest_sha256"]))
        if len(buckets[grade]) != per_batch * 10:
            raise RuntimeError(f"unexpected {grade} count: {len(buckets[grade])}")
    queue = []
    position = 0
    for batch in range(1, 11):
        for grade in ("TS", "S1", "S2", "S3"):
            start = (batch - 1) * PER_BATCH[grade]
            for entry in buckets[grade][start:start + PER_BATCH[grade]]:
                position += 1
                queue.append({
                    "position": position,
                    "batch": batch,
                    "doc_id": entry["doc_id"],
                    "proposed_grade": grade,
                    "document_origin": entry["document_origin"],
                    "frozen_document_sha256": entry["document_sha256"],
                    "review_status": "not_started",
                    "allowed_actions": ["approve", "change", "defer", "reject"],
                    "required_for_change_or_defer_or_reject": "reason",
                })
    if Counter(row["proposed_grade"] for row in queue) != Counter({grade: count * 10 for grade, count in PER_BATCH.items()}):
        raise RuntimeError("queue composition mismatch")
    payload = {
        "artifact": "proxy_gold_single_reviewer_queue",
        "version": "v1",
        "reviewer_model": "single reviewer; no inter-annotator agreement claim",
        "source_freeze": str(FROZEN.relative_to(ROOT)).replace("\\", "/"),
        "source_manifest_sha256": frozen["manifest_sha256"],
        "batch_size": 100,
        "batch_count": 10,
        "scope": "review workflow only; decisions remain in the append-only candidate decision ledger",
        "entries": queue,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with CSV_OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["position", "batch", "doc_id", "proposed_grade", "document_origin", "frozen_document_sha256", "review_status"])
        writer.writeheader()
        for row in queue:
            writer.writerow({key: row[key] for key in writer.fieldnames})
    print(JSON_OUT)
    print(CSV_OUT)
    print(json.dumps({"entries": len(queue), "batches": 10, "distribution": dict(Counter(row["proposed_grade"] for row in queue))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
