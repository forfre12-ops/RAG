"""Create a fact-preserving, structured v2 review package.

Short records are reformatted only.  The transformation adds neutral section
headings and moves existing field labels into headings; it never calls an LLM
or invents narrative facts.  The source package stays intact and the v2 audit
keeps each original/revised pair for administrator review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _blocks(text: str) -> list[str]:
    return [block.strip() for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]


def structure_short_document(text: str) -> str:
    """Format existing content without adding or paraphrasing factual statements."""
    blocks = _blocks(text)
    if len(blocks) < 2:
        return text

    result = [f"제목: {blocks[0]}"]
    first_plain_block = True
    for block in blocks[1:]:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        labelled = [line for line in lines if ":" in line and line.index(":") < 30]
        if labelled:
            for line in lines:
                if line in labelled:
                    heading, body = line.split(":", 1)
                    result.extend((heading.strip(), body.strip()))
                else:
                    result.extend(("추가 내용", line))
            continue
        if first_plain_block:
            result.extend(("자료 개요", block))
            first_plain_block = False
        else:
            result.extend(("세부 내용", block))
    return "\n\n".join(result)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-original-chars", type=int, default=499)
    args = parser.parse_args()

    review_path = args.input_dir / "reviewer_documents.jsonl"
    manifest_path = args.input_dir / "internal_manifest.jsonl"
    review_rows = load_jsonl(review_path)
    manifest_rows = load_jsonl(manifest_path)
    manifest_by_id = {row["review_id"]: row for row in manifest_rows}
    if len(review_rows) != len(manifest_rows) or set(manifest_by_id) != {row["review_id"] for row in review_rows}:
        raise ValueError("reviewer document and internal manifest IDs must match one-to-one")

    revised_review: list[dict[str, Any]] = []
    revised_manifest: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    changed_by_source = Counter()

    for review in review_rows:
        review_id = review["review_id"]
        original = str(review["text"])
        changed = len(original) <= args.max_original_chars
        revised = structure_short_document(original) if changed else original
        status = "faithful_restructure_v1" if changed else "unchanged"
        if changed:
            changed_by_source[manifest_by_id[review_id]["source_name"]] += 1
        revised_review.append({"review_id": review_id, "text": revised})
        revised_manifest.append({
            **manifest_by_id[review_id],
            "original_text_sha256": sha256(original),
            "revised_text_sha256": sha256(revised),
            "enrichment_status": status,
            "enrichment_rule": (
                "existing_content_reordered_with_neutral_headings_only" if changed else "not_applicable"
            ),
        })
        if changed:
            audit_rows.append({
                "review_id": review_id,
                "source_name": manifest_by_id[review_id]["source_name"],
                "original_chars": len(original),
                "revised_chars": len(revised),
                "original_text": original,
                "revised_text": revised,
                "original_text_sha256": sha256(original),
                "revised_text_sha256": sha256(revised),
                "rule": "existing_content_reordered_with_neutral_headings_only",
            })

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "reviewer_documents.jsonl", revised_review)
    write_jsonl(args.output_dir / "internal_manifest.jsonl", revised_manifest)
    write_jsonl(args.output_dir / "enrichment_audit.jsonl", audit_rows)
    summary = {
        "package_version": "expert_review_mock_1000_v2_structured",
        "input_package": str(args.input_dir),
        "total_documents": len(revised_review),
        "structured_documents": len(audit_rows),
        "unchanged_documents": len(revised_review) - len(audit_rows),
        "max_original_chars": args.max_original_chars,
        "method": "fact-preserving structural formatting; no generative expansion",
        "structured_by_source": dict(changed_by_source),
        "reviewer_fields": ["review_id", "text"],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
