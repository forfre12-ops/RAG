"""Read-only audit for KOIPA/generated or external synthetic JSON/JSONL documents.

Examples (no input is ever modified)::

    python scripts/audit_synthetic_documents.py poc/datasets/review_batch --report qa.json
    python scripts/audit_synthetic_documents.py supplier.jsonl --manifest qa.manifest.json
    python scripts/audit_synthetic_documents.py batch.json --prompt-echo-lexicon lexicon.json

Exit status is 0 when the command completed.  A non-release-ready report is data,
not a tooling failure; callers decide whether to block a release from
``summary.release_ready`` and ``release_blockers``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "poc" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.services.synthetic_import_quality import (  # noqa: E402
    audit_records,
    build_manifest,
    discover_input_files,
    load_metadata_index,
    load_records,
    merge_metadata,
    report_checksum,
)


def _load_lexicon(path: Path | None) -> dict[str, tuple[str, ...]] | None:
    if path is None:
        return None
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("prompt echo lexicon must be a JSON object of grade -> string array")
    return {
        str(grade).upper(): tuple(str(term) for term in terms)
        for grade, terms in raw.items()
        if isinstance(terms, list)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON/JSONL file or directory to audit (read-only)")
    parser.add_argument("--report", type=Path, help="write complete JSON report; omitted means stdout only")
    parser.add_argument("--manifest", type=Path, help="write reproducibility manifest JSON")
    parser.add_argument("--prompt-echo-lexicon", type=Path, help="JSON grade -> [observed prompt phrases]")
    parser.add_argument(
        "--metadata-manifest", type=Path,
        help="internal JSON/JSONL manifest to join by review_id/document_id (not copied to reviewer output)",
    )
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
        prompt_echo_lexicon=_load_lexicon(args.prompt_echo_lexicon),
        template_min_documents=args.template_min_documents,
        structure_concentration_threshold=args.structure_concentration_threshold,
    )
    report["read_errors"] = read_errors
    report["report_sha256"] = report_checksum(report)
    manifest = build_manifest(manifest_inputs, report, read_errors=read_errors)

    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
