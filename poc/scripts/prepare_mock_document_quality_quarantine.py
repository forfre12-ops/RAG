"""Create a reversible first-pass quality manifest for generated mock documents.

The source JSONL is never edited or deleted.  The manifest records one of four
statuses per record:

* ``excluded_hard``: answer/grade leakage or clear textual corruption/repetition.
* ``quarantined_low_document_shape``: too thin to put in the normal expert queue.
* ``manual_review_priority``: structurally weak but plausible short document.
* ``admit``: no first-pass issue found.

This is intentionally a quality-routing tool, not a truth-label validator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from koipa.proxy_corpus import measure_text_quality
from koipa.services.synth_quality import _exposes_grade_token

GRADES = {"TS", "S1", "S2", "S3"}


def _row_id(row: dict, index: int) -> str:
    return str(row.get("doc_id") or row.get("id") or f"row-{index}")


def _decide(text: str) -> tuple[str, list[str], dict[str, float | int]]:
    metrics = measure_text_quality(text)
    hard: list[str] = []
    quarantine: list[str] = []
    priority: list[str] = []

    if _exposes_grade_token(text):
        hard.append("grade_token_exposed")
    if metrics["hangul_letter_ratio"] < 0.30:
        hard.append("very_low_hangul_ratio")
    if metrics["unique_char4_ratio"] < 0.30:
        hard.append("very_low_unique_char4_ratio")
    if metrics["max_alnum_char_share"] > 0.20:
        hard.append("dominant_repeated_character")
    if metrics["duplicate_long_block_ratio"] > 0.35:
        hard.append("repeated_long_blocks")

    if metrics["blocks"] < 3:
        quarantine.append("fewer_than_3_blocks")
    if metrics["long_blocks"] < 2:
        quarantine.append("fewer_than_2_substantive_blocks")
    if metrics["numeric_facts"] < 2:
        quarantine.append("fewer_than_2_concrete_facts")

    if metrics["blocks"] < 5:
        priority.append("below_full_document_structure")
    if metrics["long_blocks"] < 3:
        priority.append("below_3_substantive_blocks")
    if metrics["numeric_facts"] < 3:
        priority.append("below_3_concrete_facts")

    if hard:
        return "excluded_hard", hard, metrics
    if quarantine:
        return "quarantined_low_document_shape", quarantine, metrics
    if priority:
        return "manual_review_priority", priority, metrics
    return "admit", [], metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--label-field", default="label")
    args = parser.parse_args()

    records: list[dict] = []
    for index, line in enumerate(args.input.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        text = str(row.get("text") or "").strip()
        label = str(row.get(args.label_field) or "")
        doc_id = _row_id(row, index)
        if not text or label not in GRADES:
            status, reasons, metrics = "excluded_hard", ["missing_text_or_valid_grade"], {}
        else:
            status, reasons, metrics = _decide(text)
        records.append({
            "doc_id": doc_id,
            "label": label,
            "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "quality_status": status,
            "reasons": reasons,
            "metrics": metrics,
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "input": str(args.input),
        "label_field": args.label_field,
        "records": len(records),
        "by_status": dict(Counter(record["quality_status"] for record in records)),
        "by_label": {
            label: dict(Counter(record["quality_status"] for record in records if record["label"] == label))
            for label in sorted(GRADES)
        },
        "note": "Source data is unchanged. Use excluded_hard and quarantined_low_document_shape "
                "as the initial expert-assignment exclusion set; manual_review_priority is not auto-deleted.",
    }
    summary_path = args.output.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
