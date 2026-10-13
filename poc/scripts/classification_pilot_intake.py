"""Metadata-only pilot preflight. Does not read referenced documents or grant use."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from content_reference_contract import require
from evaluation_inputs import sha256
from prepare_content_reference import write_json

POC = Path(__file__).resolve().parents[1]
USES = {"reference_review", "label_only_ab_pool", "customer_eval_pool"}
ROLES = {"unassigned", "train", "development", "locked_gold_eval", "held_review", "calibration", "reference_candidate"}
ALWAYS_PROTECTED = {"locked_gold_eval", "held_review", "calibration", "reference_candidate"}


def digest(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def dated(value) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def template() -> dict:
    return {
        "intake_id": None, "tenant_ref": None, "document_ref": None, "document_version": None,
        "source_origin": "unknown", "as_of": None, "original_sha256": None, "text_sha256": None,
        "normalized_text_sha256": None, "family_id": None, "family_evidence_ref": None,
        "intended_use": "reference_review", "source_role": "unassigned",
        "authorization": {"status": "unknown", "reference": None, "sha256": None, "tenant_ref": None, "allowed_use": []},
        "policy": {"scope": "content_reference", "version": None, "sha256": None},
        "extraction": {"complete_claimed": None, "source_pages": None, "extracted_pages": None,
                       "expected_attachments": None, "included_attachments": None,
                       "verification_ref": None, "verification_sha256": None},
        "context": {"state": "unknown", "reference": None, "sha256": None, "provided_to_reviewer": None},
    }


def exclusion_template() -> dict:
    return {"schema_version": "pilot-exclusion-index-v1", "coverage_complete": False,
            "coverage_reference": None, "coverage_sha256": None, "entries": []}


def validate_index(index: dict) -> None:
    require(set(index) == set(exclusion_template()) and index["schema_version"] == "pilot-exclusion-index-v1",
            "Invalid exclusion index")
    require(type(index["coverage_complete"]) is bool and isinstance(index["entries"], list), "Invalid index coverage")
    if index["coverage_complete"]:
        require(nonempty(index["coverage_reference"]) and digest(index["coverage_sha256"]), "Coverage evidence missing")
    keys = {"document_ref", "original_sha256", "text_sha256", "normalized_text_sha256", "family_id", "role"}
    for entry in index["entries"]:
        require(isinstance(entry, dict) and set(entry) == keys and entry["role"] in ROLES, "Invalid index entry")
        require(any(nonempty(entry[k]) for k in keys - {"role"}), "Empty exclusion identity")
        for k in ("original_sha256", "text_sha256", "normalized_text_sha256"):
            require(entry[k] is None or digest(entry[k]), "Invalid exclusion hash")
        for k in ("document_ref", "family_id"):
            require(entry[k] is None or nonempty(entry[k]), "Invalid exclusion reference")


def preflight(rows: list[dict], index: dict) -> dict:
    require(isinstance(rows, list), "Intake must be an array")
    validate_index(index)
    ids, results = set(), []
    for row in rows:
        require(isinstance(row, dict) and set(row) == set(template()), "Unknown intake fields or raw body included")
        require(nonempty(row["intake_id"]) and row["intake_id"] not in ids, "Missing/duplicate intake ID")
        ids.add(row["intake_id"])
        require(row["intended_use"] in USES and row["source_role"] in ROLES, "Invalid use/role")
        require(row["source_origin"] in {"unknown", "synthetic", "customer_real", "public_real"}, "Invalid origin")
        for name in ("authorization", "policy", "extraction", "context"):
            require(isinstance(row[name], dict) and set(row[name]) == set(template()[name]), "Invalid nested intake schema")
        auth, policy, extraction, context = (row[k] for k in ("authorization", "policy", "extraction", "context"))
        require(auth["status"] in {"unknown", "provided", "denied"} and isinstance(auth["allowed_use"], list), "Invalid authorization")
        require(policy["scope"] in {"content_reference", "customer_management"}, "Invalid policy scope")
        require(context["state"] in {"unknown", "provided", "not_required", "synthetic_assumption"}, "Invalid context state")
        missing, excluded = [], []
        for field in ("tenant_ref", "document_ref", "document_version", "family_id", "family_evidence_ref"):
            if not nonempty(row[field]):
                missing.append(f"missing_{field}")
        for field in ("original_sha256", "text_sha256", "normalized_text_sha256"):
            if not digest(row[field]):
                missing.append(f"missing_{field}")
        if not dated(row["as_of"]):
            missing.append("missing_as_of_with_timezone")
        if row["source_origin"] == "synthetic":
            excluded.append("synthetic_not_real_document_pilot")
        elif row["source_origin"] == "unknown":
            missing.append("unknown_real_origin")
        if row["intended_use"] == "customer_eval_pool" and row["source_origin"] != "customer_real":
            excluded.append("customer_scope_requires_customer_real")
        if auth["status"] == "denied":
            excluded.append("authorization_denied")
        elif not (auth["status"] == "provided" and nonempty(auth["reference"]) and digest(auth["sha256"])
                  and nonempty(auth["tenant_ref"]) and auth["tenant_ref"] == row["tenant_ref"]
                  and "local_review" in auth["allowed_use"]):
            missing.append("missing_scoped_local_review_permission")
        if not (nonempty(policy["version"]) and digest(policy["sha256"])):
            missing.append("missing_policy_binding")
        if policy["scope"] == "customer_management":
            missing.append("customer_management_policy_verification_required")
        counts = [extraction[k] for k in ("source_pages", "extracted_pages", "expected_attachments", "included_attachments")]
        valid_counts = all(type(n) is int and n >= 0 for n in counts)
        if not (extraction["complete_claimed"] is True and valid_counts and counts[0] > 0
                and counts[0] == counts[1] and counts[2] == counts[3]
                and nonempty(extraction["verification_ref"]) and digest(extraction["verification_sha256"])):
            missing.append("missing_complete_extraction_evidence")
        if context["state"] == "synthetic_assumption":
            excluded.append("synthetic_context_not_real_evidence")
        elif not (context["state"] in {"provided", "not_required"} and nonempty(context["reference"])
                  and digest(context["sha256"]) and context["provided_to_reviewer"] is True):
            missing.append("missing_context_or_not_required_rationale")
        forbidden = set(ALWAYS_PROTECTED)
        if row["intended_use"] == "customer_eval_pool":
            forbidden |= {"train", "development"}
        if row["source_role"] in forbidden:
            excluded.append("protected_or_contaminated_source_role")
        if not index["coverage_complete"]:
            missing.append("incomplete_exclusion_index")
        matches = []
        for entry in index["entries"]:
            fields = [k for k in ("document_ref", "original_sha256", "text_sha256", "normalized_text_sha256", "family_id")
                      if nonempty(row[k]) and row[k] == entry[k]]
            if fields:
                matches.append({"role": entry["role"], "matched_fields": fields})
                if entry["role"] in forbidden:
                    excluded.append("protected_or_contaminated_overlap")
        results.append({"intake_id": row["intake_id"], "status": "excluded" if excluded else "needs_metadata" if missing else
                        "candidate_pending_source_verification", "exclusion_reasons": sorted(set(excluded)),
                        "missing_requirements": sorted(set(missing)), "overlap_matches": matches,
                        "selected": False, "source_verified": False, "permission_authenticated": False,
                        "gold_eligible": False, "training_allowed": False})
    # Duplicate entries in the incoming batch also need consolidation, even without an external index match.
    for field in ("document_ref", "original_sha256", "text_sha256", "normalized_text_sha256", "family_id"):
        counts = Counter(row[field] for row in rows if nonempty(row[field]))
        for row, result in zip(rows, results):
            if nonempty(row[field]) and counts[row[field]] > 1:
                result["missing_requirements"].append(f"batch_shared_{field}_requires_grouping")
                if result["status"] != "excluded":
                    result["status"] = "needs_metadata"
    return {"schema_version": "pilot-intake-preflight-v1", "input_count": len(rows),
            "status_counts": dict(Counter(r["status"] for r in results)), "rows": results,
            "selected_count": 0, "original_documents_read": 0, "source_authenticity_verified": False,
            "index_coverage_claimed": index["coverage_complete"], "index_coverage_authenticated": False,
            "state": "awaiting_real_document_intake" if not rows else "metadata_preflight_only"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake", required=True)
    parser.add_argument("--exclusions", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    require(not out.exists(), "Output exists; choose a new file")
    result = preflight(json.loads(Path(args.intake).read_text(encoding="utf-8")),
                       json.loads(Path(args.exclusions).read_text(encoding="utf-8")))
    result["source_files"] = [{"path": str(Path(p).resolve()), "sha256": sha256(Path(p))}
                              for p in (args.intake, args.exclusions, __file__)]
    write_json(out, result)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
