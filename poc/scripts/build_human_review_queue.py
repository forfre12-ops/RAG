"""Build a CSV queue for external human review.

The queue is compatible with import_review_corrections.py. It prioritizes
high-risk underclassification examples from the P1 evaluation report, then fills
remaining slots from existing gold_real records.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

HIGH_RISK = {"TS", "S1", "S2"}
FIELDNAMES = [
    "doc_id",
    "model_label",
    "human_label",
    "review_decision",
    "reason_code",
    "reason_text",
    "reviewer_id",
    "domain",
    "document_type",
    "text",
]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl_by_id(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        doc_id = row.get("doc_id")
        if doc_id:
            rows[str(doc_id)] = row
    return rows


def _row(doc_id: str, model_label: str, human_label: str, source: dict) -> dict:
    text = source.get("text") or source.get("text_preview") or ""
    return {
        "doc_id": doc_id,
        "model_label": model_label,
        "human_label": human_label,
        "review_decision": "",
        "reason_code": "",
        "reason_text": "",
        "reviewer_id": "",
        "domain": source.get("domain", ""),
        "document_type": source.get("document_type", ""),
        "text": text,
    }


def build_queue(report: dict, gold_by_id: dict[str, dict], limit: int) -> list[dict]:
    candidates: list[tuple[int, dict]] = []
    seen: set[str] = set()

    for err in report.get("errors_sample", []):
        doc_id = str(err.get("doc_id", ""))
        if not doc_id or doc_id in seen:
            continue
        true_label = str(err.get("true", "")).upper()
        pred_label = str(err.get("pred", "")).upper()
        priority = 0 if true_label in HIGH_RISK and pred_label == "S3" else 1
        source = {**gold_by_id.get(doc_id, {}), **err}
        candidates.append((priority, _row(doc_id, pred_label, true_label, source)))
        seen.add(doc_id)

    for doc_id, source in gold_by_id.items():
        if len(candidates) >= limit * 2:
            break
        if doc_id in seen or source.get("label_source") == "human_review":
            continue
        label = str(source.get("label") or source.get("expected_grade") or "").upper()
        if label not in {"TS", "S1", "S2", "S3"}:
            continue
        priority = 2 if label in HIGH_RISK else 3
        candidates.append((priority, _row(doc_id, "", label, source)))
        seen.add(doc_id)

    candidates.sort(key=lambda item: item[0])
    return [row for _, row in candidates[:limit]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="reports/p1_v3_llm_gold_direct.json")
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--out", default="datasets/corrections/human_review_queue.csv")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    queue = build_queue(_load_json(Path(args.report)), _load_jsonl_by_id(Path(args.gold)), args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(queue)
    print(f"[human-review-queue] wrote {len(queue)} rows -> {out}")
    print("[human-review-queue] fill review_decision/reason_code/reviewer_id, then run:")
    print(f"  python scripts/import_review_corrections.py {out} --merge-gold --dry-run")
    return 0 if queue else 1


if __name__ == "__main__":
    raise SystemExit(main())
