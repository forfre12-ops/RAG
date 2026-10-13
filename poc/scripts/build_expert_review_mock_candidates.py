"""Build a blind expert-review package from the currently approved mock sources.

The package is deliberately a *document-shape* screen, not a label-quality
claim.  It leaves every source JSONL unchanged and produces two outputs:

* ``reviewer_documents.jsonl``: only a blind review ID and document text.
* ``internal_manifest.jsonl``: restricted mapping to source IDs and labels.

Do not give the internal manifest to reviewers.  Reviewed documents must be
assigned exactly one downstream role (training, development evaluation, or
locked gold evaluation) after human sign-off.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from koipa.services.synth_quality import _exposes_grade_token
from koipa.proxy_corpus import measure_text_quality


GRADES = {"TS", "S1", "S2", "S3"}


@dataclass(frozen=True)
class SourceSpec:
    """A source known to contain mock documents, including previous training data."""

    name: str
    path: Path
    include: Callable[[dict[str, Any]], bool]
    # A deterministic, balanced cap for an expansion source.  Omitted means
    # every document-shape admit is retained.
    max_by_label: dict[str, int] | None = None


def decide_document_shape(text: str) -> tuple[str, list[str], dict[str, float | int]]:
    """Return the same conservative screen used for the v3 mock-document gate."""
    metrics = measure_text_quality(text)
    hard: list[str] = []
    quarantine: list[str] = []

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

    if hard:
        return "excluded_hard", hard, metrics
    if quarantine:
        return "quarantined_low_document_shape", quarantine, metrics
    if metrics["blocks"] < 5 or metrics["long_blocks"] < 3 or metrics["numeric_facts"] < 3:
        return "manual_review_priority", ["below_full_document_structure"], metrics
    return "admit", [], metrics


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def source_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def relative_path(path: Path) -> str:
    return path.as_posix()


def default_sources() -> list[SourceSpec]:
    return [
        SourceSpec(
            "selfconsistent_v3",
            Path("labeled_synth_v3_selfconsistent/all_1000_before_filter.jsonl"),
            lambda _: True,
        ),
        SourceSpec(
            "ts_fin_hr_20260911",
            Path("synth_ts_fin_hr_20260911/samples.jsonl"),
            lambda _: True,
        ),
        SourceSpec(
            "combined_synthetic",
            Path("new_build_candidates_2026-09-17/_combined_no_kl_no_patent_proxy.jsonl"),
            lambda row: row.get("source") == "synthetic",
        ),
        # These were used by the deployed v5 training run.  Only the training
        # split is intentionally included: validation/test records must remain
        # available for their historical audit and are not "training documents".
        SourceSpec(
            "v5_train_synthetic_grounded",
            Path("labeled_p1_v5_clean/train.jsonl"),
            lambda row: row.get("source") == "synthetic_grounded",
        ),
        SourceSpec(
            "v5_train_public_scenario",
            Path("labeled_p1_v5_clean/train.jsonl"),
            lambda row: row.get("source") == "public_scenario",
        ),
        # A generated factor-state corpus used only to fill the 1,000-document
        # expert-review target.  The cap balances the previously selected 628
        # records (TS=109, S1=256, S2=145, S3=118) without exposing these
        # provisional labels to reviewers.
        SourceSpec(
            "v8_factor_balance_fill",
            Path("new_build_candidates_2026-09-17/v8"),
            lambda _: True,
            max_by_label={"TS": 139, "S2": 104, "S3": 129},
        ),
    ]


def source_files(dataset_root: Path, relative: Path) -> list[Path]:
    """Return a single JSONL input or every JSONL directly below a source directory."""
    source = dataset_root / relative
    if source.is_dir():
        return sorted(source.glob("*.jsonl"))
    return [source]


def build(dataset_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select exact-text-unique document-shape admits from the declared sources."""
    selected: dict[str, dict[str, Any]] = {}
    scanned = Counter()
    outcomes = Counter()
    selected_by_source = Counter()
    duplicate_selected = Counter()

    for spec in default_sources():
        source_candidates: dict[str, dict[str, Any]] = {}
        for source_path in source_files(dataset_root, spec.path):
            manifest_source_path = source_path.relative_to(dataset_root).as_posix()
            for row in read_jsonl(source_path):
                if not spec.include(row):
                    continue
                scanned[spec.name] += 1
                text = str(row.get("text") or "").strip()
                label = str(row.get("label") or row.get("grade") or "")
                if not text or label not in GRADES:
                    outcomes[(spec.name, "excluded_hard")] += 1
                    continue
                status, reasons, metrics = decide_document_shape(text)
                outcomes[(spec.name, status)] += 1
                if status != "admit":
                    continue

                digest = source_sha256(text)
                source_candidates.setdefault(digest, {
                    "source_sha256": digest,
                    "source_name": spec.name,
                    "source_path": manifest_source_path,
                    "source_doc_id": str(row.get("doc_id") or ""),
                    "source_label": label,
                    "quality_status": status,
                    "quality_reasons": reasons,
                    "quality_metrics": metrics,
                    "text": text,
                })

        candidates = sorted(
            source_candidates.values(),
            key=lambda item: hashlib.sha256(f"source-select-v1:{item['source_sha256']}".encode()).hexdigest(),
        )
        if spec.max_by_label is not None:
            capped: list[dict[str, Any]] = []
            for label, maximum in spec.max_by_label.items():
                capped.extend(
                    [item for item in candidates if item["source_label"] == label][:maximum]
                )
            candidates = capped
        for item in candidates:
            digest = item["source_sha256"]
            if digest in selected:
                duplicate_selected[spec.name] += 1
                continue
            selected[digest] = item
            selected_by_source[spec.name] += 1

    # The order has no relationship to label, source ID, or input-file order.
    ordered = sorted(
        selected.values(),
        key=lambda item: hashlib.sha256(f"mock-review-v1:{item['source_sha256']}".encode()).hexdigest(),
    )
    for index, item in enumerate(ordered, start=1):
        item["review_id"] = f"MR-{index:04d}"

    summary = {
        "selection_version": "mock-review-v1",
        "rule": "exact-text-unique records with document-shape status=admit only",
        "not_a_claim": "This is not a human validation of label correctness.",
        "reviewer_safety": "Give reviewers reviewer_documents.jsonl only; do not expose internal_manifest.jsonl.",
        "downstream_rule": "After sign-off, place each document in exactly one of training, development evaluation, or locked gold evaluation.",
        "scanned_by_source": dict(scanned),
        "outcomes_by_source": {
            source: {
                status: outcomes[(source, status)]
                for status in ("admit", "manual_review_priority", "quarantined_low_document_shape", "excluded_hard")
                if outcomes[(source, status)]
            }
            for source in scanned
        },
        "selected_by_source": dict(selected_by_source),
        "deduplicated_after_admit_by_source": dict(duplicate_selected),
        "selected_by_provisional_label": dict(Counter(item["source_label"] for item in ordered)),
        "selected_total": len(ordered),
    }
    return ordered, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=Path("datasets"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    selected, summary = build(args.dataset_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    reviewer_path = args.output_dir / "reviewer_documents.jsonl"
    manifest_path = args.output_dir / "internal_manifest.jsonl"
    summary_path = args.output_dir / "summary.json"

    with reviewer_path.open("w", encoding="utf-8") as reviewer_file, manifest_path.open("w", encoding="utf-8") as manifest_file:
        for item in selected:
            reviewer_file.write(json.dumps({"review_id": item["review_id"], "text": item["text"]}, ensure_ascii=False) + "\n")
            manifest = {key: value for key, value in item.items() if key != "text"}
            manifest_file.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
