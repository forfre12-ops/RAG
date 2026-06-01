"""Check whether the current readiness report is releasable.

Operational readiness can be CONDITIONALLY_READY while external human review is
still pending. This release gate is stricter: every gate must be PASS.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readiness", default="reports/operational_readiness.json")
    args = ap.parse_args()

    path = Path(args.readiness)
    if not path.exists():
        print(f"[release-gate] FAIL: missing readiness report: {path}")
        return 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    verdict = payload.get("verdict", "UNKNOWN")
    gates = payload.get("gates", [])
    non_pass = [g for g in gates if g.get("status") != "PASS"]

    if verdict == "PASS" and not non_pass:
        print("[release-gate] PASS")
        return 0

    print(f"[release-gate] BLOCKED: verdict={verdict}")
    for gate in non_pass:
        print(f"- {gate.get('name')}: {gate.get('status')} ({gate.get('detail')})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
