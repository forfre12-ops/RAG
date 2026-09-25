"""Collect explicitly supplied local snapshots; never connect to a source system.

Default reports omit text, management values and provider references. The optional
--packet-out exports sensitive source payloads ONLY to a new explicit file.
Exit 0: ready candidate/demo/schema; 3: extraction/evidence/policy review; 2: error.
None of these exits grants approval, authenticity, training or automation rights.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from pydantic import ValidationError  # noqa: E402
from koipa.evidence_collection import CollectionContext, CollectionInput, collect_evidence  # noqa: E402
from koipa.modules.m3_labeling.policy_engine import Policy, Rule  # noqa: E402
from koipa.policy_facts import FactContractError, require, text_digest  # noqa: E402
from koipa.policy_shadow import parse_shadow_policy  # noqa: E402
from check_policy_shadow import _read  # noqa: E402


def demo_inputs():
    """Fictional mechanics only: NOT a grade policy for real customer documents."""
    body = "가상 어댑터 시험 문서. 대외비라는 본문만으로 실제 ACL을 추정하지 않는다."
    binding = {"org_id": "fictional-org", "document_id": "fictional-document", "document_revision": "r1",
               "original_sha256": text_digest("fictional-original-bytes"), "document_sha256": text_digest(body)}
    context = CollectionContext(**binding, as_of="2026-09-15T03:00:00Z")
    extraction = {**binding, "text": body, "extraction_id": "fictional-extraction-1",
                  "extractor_version": "fictional-v1", "method": "plain",
                  "source_ref": "fictional-not-a-source-connection", "captured_at": "2026-09-15T01:00:00Z",
                  "completeness": "complete", "table_coverage": "not_applicable"}
    base = {"schema_version": "policy-evidence-collection-v1-draft", "material_role": "synthetic_policy_fixture",
            "extraction": extraction, "management": []}
    snapshot = {**binding, "snapshot_id": "fictional-snapshot-1", "provider_ref": "fictional-not-an-authenticated-system",
                "captured_at": "2026-09-15T02:00:00Z", "valid_until": "2026-09-16T00:00:00Z",
                "fields": {"access_scope": {"state": "observed", "value": "approved_only"}}}
    policy = Policy("fictional-org", "adapter-fixture-v1", "2026-09-15", ("TS", "S1", "S2", "S3"), (
        Rule("RESTRICTED_FIXTURE", "TS", 10, {"access_scope": {"value": "approved_only"}}, ("access_scope",)),
        Rule("BROAD_FIXTURE", "S2", 50, {"access_scope": {"value": "all_employees"}}, ("access_scope",)),
    ), "S3", "Fictional adapter mechanics; ACL alone is NOT an approved grading rule")
    cases = [("missing_management", copy.deepcopy(base))]
    bound = copy.deepcopy(base)
    bound["management"] = [copy.deepcopy(snapshot)]
    cases.append(("bound_management", bound))
    absent = copy.deepcopy(bound)
    absent["management"][0]["fields"]["security_marking"] = {"state": "proven_absent", "value": None}
    cases.append(("explicit_absence", absent))
    conflict = copy.deepcopy(bound)
    second = copy.deepcopy(snapshot)
    second["snapshot_id"] = "fictional-snapshot-2"
    second["fields"]["access_scope"]["value"] = "all_employees"
    conflict["management"].append(second)
    cases.append(("conflicting_snapshots", conflict))
    partial = copy.deepcopy(bound)
    partial["extraction"].update({"pages": 1, "total_pages": 3})
    cases.append(("incomplete_extraction", partial))
    return policy, context, cases


def _write_new(path: Path, value: dict) -> None:
    # Serialize before opening: malformed data must not leave an empty artifact.
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encoded)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--schema", action="store_true")
    for name in ("policy", "snapshot", "context", "out", "packet-out"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    snapshots = {}
    try:
        require(not (args.demo and args.schema), "choose_one_mode")
        require(not ((args.demo or args.schema) and any((args.policy, args.snapshot, args.context, args.packet_out))),
                "mixed_input_modes")
        outputs = [p.resolve() for p in (args.out, args.packet_out) if p is not None]
        require(len(set(outputs)) == len(outputs), "output_paths_overlap")
        require(all(not path.exists() for path in outputs), "output_exists")
        packet = None
        if args.schema:
            report = {"snapshot": CollectionInput.model_json_schema(), "context": CollectionContext.model_json_schema()}
            exit_code = 0
        elif args.demo:
            policy, context, cases = demo_inputs()
            results = [{"case_id": name, "result": collect_evidence(raw, context=context, policy=policy).report}
                       for name, raw in cases]
            report = {"schema_version": "policy-evidence-collection-demo-v1", "dataset_role": "policy_fixture",
                      "synthetic_cases": len(results), "real_documents": 0, "results": results,
                      "training_allowed": False, "model_evaluation_allowed": False,
                      "customer_accuracy_measured": False, "automation_allowed": False, "finalized": False}
            exit_code = 0
        else:
            require(all((args.policy, args.snapshot, args.context)), "three_input_files_required")
            policy = parse_shadow_policy(_read(args.policy, snapshots))
            raw = _read(args.snapshot, snapshots)
            context = CollectionContext.model_validate(_read(args.context, snapshots))
            result = collect_evidence(raw, context=context, policy=policy)
            report, packet = result.report, result.packet
            proposal = report["policy_proposal"]
            exit_code = 0 if proposal is not None and proposal["status"] == "candidate" else 3
        for path, digest in snapshots.items():
            require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, "input_changed_during_collection")
        if not args.schema:
            # Named roles, not potentially sensitive local filenames, in reports.
            report["input_files_sha256"] = {
                name: snapshots[str(path.resolve())] for name, path in (
                    ("policy", args.policy), ("snapshot", args.snapshot), ("context", args.context)) if path
            }
            report["implementation_sha256"] = {
                name: hashlib.sha256((POC / name).read_bytes()).hexdigest()
                for name in ("scripts/collect_policy_evidence.py", "scripts/check_policy_shadow.py",
                             "src/koipa/evidence_collection.py", "src/koipa/policy_facts.py",
                             "src/koipa/policy_shadow.py", "src/koipa/modules/m3_labeling/policy_engine.py")
            }
        # An explicit export contains raw evidence. A held extraction can never
        # export a packet, even when --packet-out is supplied.
        if args.packet_out and packet is not None:
            _write_new(args.packet_out, packet.model_dump())
        if args.out:
            _write_new(args.out, report)
        if args.schema:
            summary = report if not args.out else {"status": "SCHEMA_WRITTEN", "cross_field_checks_required": True}
        elif args.demo:
            summary = {"mode": "fictional_demo", "cases": [{
                "case_id": r["case_id"], "status": r["result"]["status"],
                "shadow_status": (r["result"]["policy_proposal"] or {}).get("status"),
            } for r in report["results"]], "real_documents": 0}
        else:
            summary = {"status": report["status"], "hold_reasons": report["hold_reasons"],
                       "shadow_status": (report["policy_proposal"] or {}).get("status"),
                       "packet_exported": args.packet_out is not None and packet is not None,
                       "packet_contains_source_payloads": args.packet_out is not None and packet is not None}
        summary["automation_allowed"] = False
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return exit_code
    except (FactContractError, ValidationError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "INVALID_COLLECTION_INPUT", "error_type": type(exc).__name__,
                          "error_code": str(exc) if isinstance(exc, FactContractError) else "invalid_file_or_schema",
                          "automation_allowed": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
