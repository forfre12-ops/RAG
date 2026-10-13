"""Fast, scoped verification for ordinary commits; never a release approval.

Use this for rapid feedback after a narrowly scoped change.  It intentionally
does not build images, run migrations, or attest a deployable artifact.  Those
remain the job of ``run_full_verification_gate.py`` / ``make verify-release``.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


_POC = Path(__file__).resolve().parents[1]
_PROFILES: dict[str, tuple[str, ...]] = {
    "console": (
        "tests/test_e2e_console_scenarios.py",
        "tests/test_e2e_console_api_contract.py",
        "tests/test_e2e_console_rendered_snapshot.py",
    ),
    "synthesis": (
        "tests/test_synth_generation_three_gaps.py",
        "tests/test_synth_provider_status.py",
        "tests/test_synth_failure_is_visible.py",
    ),
    "classify": (
        "tests/test_policy_engine.py",
        "tests/test_policy_engine_defects.py",
        "tests/test_four_path_shadow.py",
        "tests/test_factor_shadow.py",
        "tests/test_schema_grade_serving_guard.py",
        "tests/test_classify_kl_wire_excludes_internal_fields.py",
    ),
}


def commands(profile: str) -> list[list[str]]:
    """Commands for a profile, exposed for contract tests and auditability."""
    tests = _PROFILES[profile]
    return [
        [sys.executable, "-m", "uv", "lock", "--locked"],
        [sys.executable, "scripts/openapi_consistency.py", "--strict"],
        [sys.executable, "-m", "pytest", "-q", *tests],
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Scoped fast verification (not release approval)")
    parser.add_argument("--profile", choices=sorted(_PROFILES), required=True)
    args = parser.parse_args()

    env = os.environ.copy()
    env.setdefault("TESTING", "1")
    print(f"[verify-fast] profile={args.profile}; not deploy-eligible; run verify-release before promotion")
    for command in commands(args.profile):
        print(f"[verify-fast] {' '.join(command)}")
        if subprocess.run(command, cwd=_POC, env=env).returncode:
            return 1
    print("[verify-fast] PASS (scoped only; no image/model/deployment attestation)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
