"""Attest historical train/validation/test corpora for raw-model comparison.

This does not upgrade a legacy corpus into a finalized proxy training run.  It
only records immutable text-hash provenance so raw-model comparison can perform
the same frozen-corpus overlap audit.  Bundle operating-point comparison always
requires a full proxy-training-run manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lloydk.proxy_model_comparison import (  # noqa: E402
    ProxyComparisonError,
    create_legacy_training_corpus_attestation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create immutable text-hash provenance for historical train/validation/test "
            "corpora; usable only with raw-model proxy comparison"
        )
    )
    parser.add_argument("--train", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--historical-build-manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        payload = create_legacy_training_corpus_attestation(
            train_path=Path(args.train),
            validation_path=Path(args.validation),
            test_path=Path(args.test),
            model_dir=Path(args.model_dir),
            historical_build_manifest_path=Path(args.historical_build_manifest),
            output_path=Path(args.output),
        )
    except (OSError, ProxyComparisonError, ValueError) as exc:
        print(f"legacy training corpus attestation failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(Path(args.output)),
                "schema_version": payload["schema_version"],
                "claim_scope": payload["claim_scope"],
                "model_tree_sha256": payload["model"]["tree_sha256"],
                "historical_build_manifest_sha256": payload[
                    "historical_build_manifest"
                ]["sha256"],
                "bundle_operating_point_allowed": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
