"""Fail fast when legacy acceptance-pack parsers cannot preserve required text.

This is deliberately smaller than ``run_acceptance.py``: it never loads a
classifier or starts a database.  It verifies the two optional parsers that
have caused late release-gate failures (binary HWP and legacy XLS) against the
committed acceptance documents and their required numeric tokens.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


_POC = Path(__file__).resolve().parents[1]
_LEGACY_FORMATS = {"hwp", "xls"}


def validate_document(doc: dict[str, Any], *, text: str, quality: float, error: str | None) -> list[str]:
    """Return concise failure reasons for one legacy acceptance document."""
    reasons: list[str] = []
    if error:
        reasons.append(f"extract_error={error}")
    if quality <= 0 or not text.strip():
        reasons.append("empty_or_zero_quality")
    missing = [str(token) for token in doc.get("expected_numeric_tokens", []) if str(token) not in text]
    if missing:
        reasons.append(f"missing_numeric_tokens={','.join(missing)}")
    return reasons


def check_pack(pack_dir: Path) -> list[str]:
    """Parse HWP/XLS acceptance fixtures without invoking the classifier."""
    manifest = pack_dir / "expected_labels.json"
    if not manifest.is_file():
        return [f"manifest_missing={manifest}"]
    try:
        docs = json.loads(manifest.read_text(encoding="utf-8")).get("docs", [])
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest_unreadable={type(exc).__name__}:{exc}"]
    legacy_docs = [doc for doc in docs if str(doc.get("format", "")).lower() in _LEGACY_FORMATS]
    if not legacy_docs:
        return ["legacy_acceptance_documents_missing"]

    if str(_POC / "src") not in sys.path:
        sys.path.insert(0, str(_POC / "src"))
    from koipa.modules.m2_preprocess.pipeline import PreprocessPipeline  # noqa: PLC0415

    pipeline = PreprocessPipeline()
    failures: list[str] = []
    for doc in legacy_docs:
        path = pack_dir / str(doc["file"])
        try:
            pre = pipeline.run_file(path)
            extraction = pre.extraction
            reasons = validate_document(
                doc,
                text=pre.text,
                quality=float(extraction.quality or 0),
                error=extraction.error,
            )
        except Exception as exc:  # noqa: BLE001
            reasons = [f"parser_exception={type(exc).__name__}:{exc}"]
        if reasons:
            failures.append(f"{doc.get('doc_id', path.name)}: {'; '.join(reasons)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast legacy HWP/XLS acceptance-pack parser preflight")
    parser.add_argument("--pack", default="datasets/acceptance_pack", help="acceptance-pack directory")
    args = parser.parse_args()
    pack_dir = Path(args.pack)
    if not pack_dir.is_absolute():
        pack_dir = _POC / pack_dir
    failures = check_pack(pack_dir)
    if failures:
        print("[parser-preflight] FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("[parser-preflight] PASS legacy HWP/XLS fixtures preserve required numeric tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
