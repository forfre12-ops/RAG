"""Compare baseline and candidate classifiers on frozen proxy gold."""

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
    compare_proxy_models,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed baseline-vs-candidate comparison on the exact frozen "
            "1,000-record proxy corpus"
        )
    )
    parser.add_argument("--frozen-corpus", required=True)
    parser.add_argument(
        "--frozen-manifest",
        required=True,
        help="ready assembly report that attests the frozen corpus SHA-256",
    )
    parser.add_argument("--baseline-model-dir", required=True)
    parser.add_argument("--candidate-model-dir", required=True)
    parser.add_argument(
        "--baseline-training-manifest",
        help="committed materialization manifest for the baseline model",
    )
    parser.add_argument(
        "--candidate-training-manifest",
        help="committed materialization manifest for the candidate model",
    )
    parser.add_argument(
        "--baseline-legacy-training-attestation",
        help="raw-model-only immutable train/validation/test provenance for baseline",
    )
    parser.add_argument(
        "--candidate-legacy-training-attestation",
        help="raw-model-only immutable train/validation/test provenance for candidate",
    )
    parser.add_argument(
        "--public-s3-challenge",
        help=(
            "optional exact 300-record public-real S3 JSONL; evaluated only as a "
            "separate false-positive/overclassification challenge"
        ),
    )
    parser.add_argument(
        "--output-root", default="reports/proxy_model_comparisons"
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="character-chunk forward batch size; M5 serving uses 8",
    )
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument(
        "--comparison-mode",
        choices=["raw_model", "bundle_operating_point"],
        required=True,
        help=(
            "raw_model=T=1+argmax, bundle_operating_point=독립 calibration에 묶인 "
            "T+tau. production CLI는 모드 명시 필수"
        ),
    )
    parser.add_argument(
        "--apply-bundle-operating-point",
        action="store_true",
        help="--comparison-mode bundle_operating_point의 명시적 단축 옵션",
    )
    parser.add_argument("--bootstrap-replicates", type=int, default=2_000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260808)
    args = parser.parse_args(argv)
    if args.apply_bundle_operating_point:
        parser.error(
            "--apply-bundle-operating-point is retired; explicitly pass "
            "--comparison-mode bundle_operating_point"
        )
    comparison_mode = args.comparison_mode
    try:
        run_dir, report, complete = compare_proxy_models(
            frozen_corpus_path=Path(args.frozen_corpus),
            frozen_manifest_path=Path(args.frozen_manifest),
            baseline_model_dir=Path(args.baseline_model_dir),
            candidate_model_dir=Path(args.candidate_model_dir),
            baseline_training_manifest_path=(
                Path(args.baseline_training_manifest)
                if args.baseline_training_manifest
                else None
            ),
            candidate_training_manifest_path=(
                Path(args.candidate_training_manifest)
                if args.candidate_training_manifest
                else None
            ),
            baseline_legacy_training_attestation_path=(
                Path(args.baseline_legacy_training_attestation)
                if args.baseline_legacy_training_attestation
                else None
            ),
            candidate_legacy_training_attestation_path=(
                Path(args.candidate_legacy_training_attestation)
                if args.candidate_legacy_training_attestation
                else None
            ),
            public_s3_challenge_path=(
                Path(args.public_s3_challenge)
                if args.public_s3_challenge
                else None
            ),
            output_root=Path(args.output_root),
            run_id=args.run_id,
            batch_size=args.batch_size,
            device=args.device,
            bootstrap_replicates=args.bootstrap_replicates,
            bootstrap_seed=args.bootstrap_seed,
            comparison_mode=comparison_mode,
        )
    except (OSError, ProxyComparisonError, ValueError) as exc:
        raise SystemExit(f"proxy model comparison failed: {exc}") from exc
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "run_id": report["run_id"],
                "claim_scope": report["claim_scope"],
                "comparison_sha256": complete["artifacts"]["comparison"]["sha256"],
                "complete": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
