"""Report missing decisions. Completeness is never identity/approval verification."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "docs/classification_decisions.pending.json"
REQUIRED = {f"D{i:02}" for i in range(1, 9)}


def audit(contract: dict) -> dict:
    if contract.get("schema_version") != "classification-decisions-v1":
        raise ValueError("Unknown decision schema")
    rows = contract.get("decisions", [])
    if len(rows) != 8 or {r.get("id") for r in rows} != REQUIRED:
        raise ValueError("Missing/duplicate decision IDs")
    if contract.get("execution_authorized") is not False:
        raise ValueError("This collection document cannot grant execution authority")
    pending = []
    for row in rows:
        if row.get("status") not in {"pending", "recorded_for_verification"}:
            raise ValueError("This tool does not authenticate approval or signatures")
        missing = [k for k in ("value", "owner_id", "evidence_ref") if not row.get(k)]
        if row["status"] == "pending" or missing:
            pending.append({"id": row["id"], "topic": row.get("topic"), "missing": missing,
                            "owner_role": row.get("owner_role")})
    return {"status": "HOLD_OWNER_DECISIONS" if pending else "RECORDED_NOT_AUTHENTICATED",
            "pending_count": len(pending), "pending": pending,
            "execution_authorized_by_this_check": False, "approval_authenticity_verified": False,
            "model_training_executed": False, "customer_accuracy_measured": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args(argv)
    try:
        raw = args.contract.read_bytes()
        result = audit(json.loads(raw))
        result["contract_sha256"] = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "INVALID_DECISION_RECORD", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["pending_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
