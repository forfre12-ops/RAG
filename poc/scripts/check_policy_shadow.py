"""Offline evidence-policy shadow checker. No network, model, approval or serving writes.

--demo runs five explicitly fictional policy fixtures, not accuracy evaluation.
Otherwise --policy, --packet and --context are all required. Exit 0 means a
candidate/expected demo diagnostic, 3 means review, 2 means invalid input/output.
No exit code grants policy approval or automation permission.
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
from koipa.modules.m3_labeling.policy_engine import Policy, Rule  # noqa: E402
from koipa.policy_facts import (  # noqa: E402
    FactContext, FactContractError, FactPacket, require, text_digest, value_digest,
)
from koipa.policy_shadow import evaluate_shadow, parse_shadow_policy, policy_digest  # noqa: E402


def demo_inputs():
    """All evidence origins below are FICTIONAL; no real person/review is asserted."""
    context = FactContext(org_id="demo-org", document_id="demo-document",
                          document_sha256=text_digest("명시적 가상 정책 시험 문서"),
                          as_of="2026-09-15T03:00:00Z")
    policy = Policy("demo-org", "fictional-policy-v1", "2026-09-15", ("TS", "S1", "S2", "S3"), (
        Rule("HIGH", "TS", 10, {"public_disclosed": {"value": False},
                                "access_scope": {"value": "approved_only"}}, ("access_scope",)),
        Rule("LOW", "S2", 50, {"public_disclosed": {"value": False}}),
        Rule("PUBLIC", "S3", 90, {"public_disclosed": {"value": True}}),
    ), "S3", "Fictional mechanics only; not a customer policy")
    base = {"schema_version": "policy-facts-v1-draft", "material_role": "synthetic_policy_fixture",
            "org_id": context.org_id, "document_id": context.document_id,
            "document_sha256": context.document_sha256, "policy_version": policy.version,
            "policy_sha256": policy_digest(policy), "sources": [], "facts": []}
    cases = []
    for name, claims in (
        ("missing_all", []),
        ("missing_management", [("public_disclosed", False)]),
        ("bound_private", [("public_disclosed", False), ("access_scope", "approved_only")]),
        ("bound_public", [("public_disclosed", True)]),
        ("conflicting_public_claims", [("public_disclosed", True), ("public_disclosed", False)]),
    ):
        packet = copy.deepcopy(base)
        for index, (fact, value) in enumerate(claims):
            origin = "system_metadata" if fact == "access_scope" else "human_review"
            payload, sid = {fact: value}, f"fictional-{index}"
            sha = value_digest(payload)
            packet["sources"].append({"source_id": sid, "kind": origin, "payload": payload,
                "org_id": context.org_id, "document_id": context.document_id,
                "document_sha256": context.document_sha256, "payload_sha256": sha,
                "captured_at": "2026-09-15T02:00:00Z", "source_ref": "fictional-not-a-real-review"})
            packet["facts"].append({"fact": fact, "value": value, "origin": origin,
                "state": "proven_absent" if value is False else "observed",
                "evidence": [{"source_id": sid, "source_sha256": sha,
                              "locator": {"kind": "json_pointer", "pointer": "/" + fact},
                              "value_sha256": value_digest(value)}]})
        cases.append((name, packet))
    return policy, context, cases


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_value):
    raise FactContractError("nonfinite_json_constant")


def _read(path: Path, snapshots: dict) -> dict:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    key = str(path.resolve())
    require(key not in snapshots or snapshots[key] == digest, "input_changed_during_read")
    snapshots[key] = digest
    return json.loads(payload.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--schema", action="store_true")
    for name in ("policy", "packet", "context", "out"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    snapshots = {}
    try:
        require(not (args.demo and args.schema), "choose_one_mode")
        require(not ((args.demo or args.schema) and any((args.policy, args.packet, args.context))), "mixed_input_modes")
        if args.out:
            require(not args.out.exists(), "output_exists")
        if args.schema:
            report, exit_code = FactPacket.model_json_schema(), 0
        elif args.demo:
            policy, context, cases = demo_inputs()
            results = [{"case_id": name, "result": evaluate_shadow(policy, packet, context=context)}
                       for name, packet in cases]
            report = {"schema_version": "policy-shadow-demo-v1", "dataset_role": "policy_fixture",
                      "results": results, "synthetic_cases": len(results), "real_documents": 0,
                      "customer_accuracy_measured": False, "training_allowed": False,
                      "model_evaluation_allowed": False, "policy_approval_granted": False}
            exit_code = 0
        else:
            require(all((args.policy, args.packet, args.context)), "three_input_files_required")
            policy = parse_shadow_policy(_read(args.policy, snapshots))
            packet = _read(args.packet, snapshots)
            context = FactContext.model_validate(_read(args.context, snapshots))
            report = evaluate_shadow(policy, packet, context=context)
            exit_code = 0 if report["status"] == "candidate" else 3
        for path, digest in snapshots.items():
            require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, "input_changed_during_check")
        if not args.schema:
            report["input_files_sha256"] = snapshots
            report["implementation_sha256"] = {
                name: hashlib.sha256((POC / name).read_bytes()).hexdigest()
                for name in ("scripts/check_policy_shadow.py", "src/koipa/policy_facts.py",
                             "src/koipa/policy_shadow.py", "src/koipa/modules/m3_labeling/policy_engine.py")
            }
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        if args.demo:
            summary = {"mode": "fictional_demo", "cases": [
                {"case_id": r["case_id"], "status": r["result"]["status"], "grade": r["result"]["grade"]}
                for r in report["results"]], "policy_approval_granted": False}
        elif args.schema:
            summary = report if not args.out else {"status": "SCHEMA_WRITTEN", "cross_field_checks_required": True}
        else:
            summary = {k: report[k] for k in ("status", "grade", "would_require_review", "automation_allowed")}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return exit_code
    except (FactContractError, ValidationError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "INVALID_SHADOW_INPUT", "error_type": type(exc).__name__,
                          "error_code": str(exc) if isinstance(exc, FactContractError) else "invalid_file_or_schema",
                          "policy_approval_granted": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
