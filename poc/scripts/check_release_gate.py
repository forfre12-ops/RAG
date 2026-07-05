"""Check whether the current readiness report is releasable.

Operational readiness can be CONDITIONALLY_READY while external human review is
still pending. This release gate is stricter: by default every gate must be PASS.

Two failure classes are distinguished (mirroring build_operational_readiness):
  - FAIL    = a genuine regression / defect (P1 F1/FNR, P2 recall/latency, gold
              quality). These ALWAYS block a release.
  - BLOCKED = a data-ceiling / self-resolving item — human_review below the
              minimum, or model parity pending a deploy-time CLASSIFIER_MODEL_DIR
              promotion. These block by default.

--allow-conditional (env RELEASE_GATE_ALLOW_CONDITIONAL=1) turns on PILOT mode:
a CONDITIONALLY_READY verdict (only BLOCKED gates, no FAIL) is treated as
releasable, with a loud audited waiver listing exactly which gates are waived.
It NEVER waives a FAIL gate or a missing report — a real regression blocks the
pilot exactly as it blocks GA. This lets the release gate be wired into the
deploy path (so genuine regressions block shipping) without turning red on the
known human_review data ceiling during the pilot.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# Gate statuses a PILOT (--allow-conditional) release is permitted to waive.
# Everything else — notably FAIL, and any unrecognised status — is a hard blocker.
_WAIVABLE = {"BLOCKED"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readiness", default="reports/operational_readiness.json")
    ap.add_argument(
        "--allow-conditional",
        action="store_true",
        default=_env_flag("RELEASE_GATE_ALLOW_CONDITIONAL"),
        help=(
            "PILOT mode: treat a CONDITIONALLY_READY verdict (only BLOCKED gates — "
            "e.g. human_review below minimum, model parity pending) as releasable, "
            "with an audited waiver. NEVER waives a FAIL gate or a missing report. "
            "Env: RELEASE_GATE_ALLOW_CONDITIONAL=1."
        ),
    )
    args = ap.parse_args()

    path = Path(args.readiness)
    if not path.exists():
        # A missing report is an evidence gap, not a known data ceiling — never waivable.
        print(f"[release-gate] FAIL: missing readiness report: {path}")
        return 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    verdict = payload.get("verdict", "UNKNOWN")
    gates = payload.get("gates", [])

    non_pass = [g for g in gates if g.get("status") != "PASS"]
    hard_blockers = [g for g in non_pass if g.get("status") not in _WAIVABLE]
    waivable = [g for g in non_pass if g.get("status") in _WAIVABLE]

    # A genuine regression (any FAIL / unknown status, or a FAIL verdict) always
    # blocks, regardless of pilot mode.
    if hard_blockers or verdict == "FAIL":
        print(f"[release-gate] BLOCKED (hard): verdict={verdict}")
        for gate in (hard_blockers or non_pass):
            print(f"- {gate.get('name')}: {gate.get('status')} ({gate.get('detail')})")
        return 1

    if not waivable:
        print("[release-gate] PASS")
        return 0

    # Only data-ceiling / self-resolving BLOCKED gates remain.
    if args.allow_conditional:
        # ASCII-only output: this may print to a cp949/Windows console at the
        # customer site, where a non-ASCII glyph would raise UnicodeEncodeError
        # and crash the gate (blocking a pilot release for the wrong reason).
        print(f"[release-gate] PASS (PILOT WAIVER): verdict={verdict}")
        print(
            "  [!] AUDITED WAIVER - the gates below are waived for a PILOT release "
            "(NOT GA). Genuine regressions (FAIL) are never waived:"
        )
        for gate in waivable:
            print(f"  - WAIVED {gate.get('name')}: {gate.get('status')} ({gate.get('detail')})")
        print(
            "  -> GA still requires these to reach PASS (fill human_review, promote "
            "CLASSIFIER_MODEL_DIR). Re-run without --allow-conditional for the GA gate."
        )
        return 0

    print(f"[release-gate] BLOCKED: verdict={verdict}")
    for gate in waivable:
        print(f"- {gate.get('name')}: {gate.get('status')} ({gate.get('detail')})")
    print(
        "  (PILOT: re-run with --allow-conditional to waive data-ceiling gates "
        "for a pilot release; FAIL gates are never waived.)"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
