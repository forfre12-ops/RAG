"""Read-only quality gate for generated or externally supplied synthetic documents.

This deployment-local wrapper deliberately keeps the policy implementation in
``koipa.services.synthetic_import_quality``.  It exists so air-gapped API and
worker images carry the same reproducible import gate used before a batch is
made visible to expert reviewers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from koipa.services.synthetic_import_quality import (
    audit_records,
    build_manifest,
    discover_input_files,
    load_metadata_index,
    load_records,
    merge_metadata,
    report_checksum,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON/JSONL file or directory to audit")
    parser.add_argument("--report", type=Path, help="write the complete JSON report")
    parser.add_argument("--manifest", type=Path, help="write the reproducibility manifest")
    parser.add_argument("--metadata-manifest", type=Path, help="internal manifest joined by review ID")
    parser.add_argument("--template-min-documents", type=int, default=3)
    parser.add_argument("--structure-concentration-threshold", type=float, default=0.80)
    args = parser.parse_args(argv)

    input_files = discover_input_files(args.input)
    records, read_errors = load_records(input_files)
    manifest_inputs = list(input_files)
    if args.metadata_manifest:
        metadata, metadata_errors = load_metadata_index(args.metadata_manifest)
        records = merge_metadata(records, metadata)
        read_errors.extend(metadata_errors)
        manifest_inputs.append(args.metadata_manifest)
    report = audit_records(
        records,
        template_min_documents=args.template_min_documents,
        structure_concentration_threshold=args.structure_concentration_threshold,
    )
    report["read_errors"] = read_errors
    report["report_sha256"] = report_checksum(report)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    if args.manifest:
        manifest = build_manifest(manifest_inputs, report, read_errors=read_errors)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
