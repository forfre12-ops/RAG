"""Evaluate one classifier on the separate public-real S3 challenge."""

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

from lloydk.proxy_model_comparison import ProxyComparisonError  # noqa: E402
from lloydk.public_s3_challenge import (  # noqa: E402
    evaluate_public_s3_challenge,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Immutable one-model evaluation on the exact 300-record public-real "
            "S3 overclassification challenge"
        )
    )
    parser.add_argument("--challenge-records", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument(
        "--output-root", default="reports/public_s3_challenge_evaluations"
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="character-chunk forward batch size; M5 serving uses 8",
    )
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args(argv)
    try:
        run_dir, manifest, complete = evaluate_public_s3_challenge(
            challenge_path=Path(args.challenge_records),
            model_dir=Path(args.model_dir),
            output_root=Path(args.output_root),
            run_id=args.run_id,
            batch_size=args.batch_size,
            device=args.device,
        )
    except (OSError, ProxyComparisonError, ValueError) as exc:
        raise SystemExit(f"public S3 challenge evaluation failed: {exc}") from exc
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "run_id": manifest["run_id"],
                "claim_scope": manifest["claim_scope"],
                "manifest_sha256": complete["artifacts"]["manifest"]["sha256"],
                "complete": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
