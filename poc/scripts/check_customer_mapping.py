"""Offline versioned vocabulary mapping previews, never policy facts or approval.

Use --mapping/--snapshot/--context, --verify-preview, --demo or --schema.
Default diagnostics omit raw values; --preview-out explicitly stores sensitive
source payloads and is NOT a FactPacket/ManagementSnapshot export.
Exit 3: valid review-only preview; 0: demo/schema/replay verification; 2: error.
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
from koipa.mapping_proposal import (  # noqa: E402
    MappingContext, MappingPackage, RawManagementSnapshot, mapping_digest, preview_mapping, verify_preview,
)
from koipa.policy_facts import FactContractError, require, text_digest, value_digest  # noqa: E402
from check_policy_shadow import _read  # noqa: E402
from collect_policy_evidence import _write_new  # noqa: E402


def demo_inputs():
    """All customer names, references and approval declarations are fictional."""
    binding = {"org_id": "fictional-org", "document_id": "fictional-doc", "document_revision": "r1",
               "original_sha256": text_digest("fictional-original"), "document_sha256": text_digest("fictional-text")}
    payload = {"rules": [{"fact": "security_marking", "raw_value": "가상-대외비", "canonical_value": "confidential"},
                         {"fact": "access_scope", "raw_value": "가상-부서한", "canonical_value": "department"}]}
    source = {"source_id": "fictional-table", "org_id": binding["org_id"], "source_ref": "fictional-not-a-real-mapping",
              "captured_at": "2026-09-15T00:00:00Z", "payload": payload, "payload_sha256": value_digest(payload)}
    package = {"schema_version": "customer-vocabulary-mapping-v1-draft", "org_id": binding["org_id"],
               "mapping_id": "fictional-mapping", "version": "draft-1", "effective_at": "2026-09-15T00:00:00Z",
               "declared_status": "draft", "sources": [source], "rules": []}
    for index, record in enumerate(payload["rules"]):
        package["rules"].append({"rule_id": f"fictional-rule-{index}", **record, "evidence": [{
            "source_id": source["source_id"], "source_sha256": source["payload_sha256"],
            "locator": {"kind": "json_pointer", "pointer": f"/rules/{index}"}, "value_sha256": value_digest(record)}]})
    raw_payload = {"marking": "가상-대외비", "scope": "가상-부서한"}
    snapshot = {**binding, "snapshot_id": "fictional-snapshot", "provider_ref": "fictional-not-an-authenticated-provider",
                "origin": "system_export", "captured_at": "2026-09-15T01:00:00Z", "valid_until": "2026-09-16T00:00:00Z",
                "payload": raw_payload, "payload_sha256": value_digest(raw_payload), "fields": {
                    "security_marking": {"state": "observed", "pointer": "/marking"},
                    "access_scope": {"state": "observed", "pointer": "/scope"}}}
    cases = []
    for name in ("exact_mapping", "unknown_term", "explicit_absence", "conflicting_rules", "future_mapping", "approval_claim"):
        pack, snap = copy.deepcopy(package), copy.deepcopy(snapshot)
        if name == "unknown_term":
            snap["payload"]["marking"] = "가상-미등록"
        elif name == "explicit_absence":
            snap["payload"]["marking"] = None
            snap["fields"]["security_marking"]["state"] = "proven_absent"
        elif name == "conflicting_rules":
            record = {"fact": "security_marking", "raw_value": "가상-대외비", "canonical_value": "secret"}
            pack["sources"][0]["payload"]["rules"].append(record)
            pack["rules"].append({"rule_id": "fictional-conflict", **record, "evidence": [{
                "source_id": source["source_id"], "source_sha256": "0" * 64,
                "locator": {"kind": "json_pointer", "pointer": "/rules/2"}, "value_sha256": value_digest(record)}]})
            sha = value_digest(pack["sources"][0]["payload"])
            pack["sources"][0]["payload_sha256"] = sha
            for rule in pack["rules"]:
                rule["evidence"][0]["source_sha256"] = sha
        elif name == "future_mapping":
            pack["effective_at"] = "2026-09-16T00:00:00Z"
        elif name == "approval_claim":
            pack.update(declared_status="approved", approval_record_ref="fictional-not-a-human-approval")
        snap["payload_sha256"] = value_digest(snap["payload"])
        context = MappingContext(document={**binding, "as_of": "2026-09-15T03:00:00Z"},
                                 mapping_id=pack["mapping_id"], mapping_version=pack["version"], mapping_sha256=mapping_digest(pack))
        cases.append((name, pack, snap, context))
    return cases


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--schema", action="store_true")
    for name in ("mapping", "snapshot", "context", "verify-preview", "out", "preview-out"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    files = {}
    try:
        special = int(args.demo) + int(args.schema) + int(args.verify_preview is not None)
        require(special <= 1, "choose_one_mapping_mode")
        require(not (special and any((args.mapping, args.snapshot, args.context, args.preview_out))), "mixed_mapping_modes")
        outputs = [p.resolve() for p in (args.out, args.preview_out) if p is not None]
        require(len(outputs) == len(set(outputs)), "mapping_outputs_overlap")
        require(all(not path.exists() for path in outputs), "output_exists")
        artifact = None
        if args.schema:
            report = {"mapping": MappingPackage.model_json_schema(), "snapshot": RawManagementSnapshot.model_json_schema(),
                      "context": MappingContext.model_json_schema()}
            exit_code = 0
        elif args.demo:
            report = {"mode": "synthetic_mapping_demo", "dataset_role": "policy_fixture", "real_documents": 0,
                      "training_allowed": False, "model_evaluation_allowed": False, "automation_allowed": False,
                      "results": [{"case_id": name, "result": preview_mapping(pack, snap, context=context).report}
                                  for name, pack, snap, context in demo_inputs()]}
            exit_code = 0
        elif args.verify_preview:
            report = verify_preview(_read(args.verify_preview, files))
            exit_code = 0
        else:
            require(all((args.mapping, args.snapshot, args.context)), "three_mapping_inputs_required")
            result = preview_mapping(_read(args.mapping, files), _read(args.snapshot, files),
                                     context=_read(args.context, files))
            report, artifact = result.report, result.artifact
            exit_code = 3  # Even declared approvals are only unverified input here.
        for path, digest in files.items():
            require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, "mapping_input_changed_during_check")
        if not args.schema:
            report["input_files_sha256"] = {name: files[str(path.resolve())] for name, path in (
                ("mapping", args.mapping), ("snapshot", args.snapshot), ("context", args.context),
                ("preview", args.verify_preview)) if path}
            report["implementation_sha256"] = {
                name: hashlib.sha256((POC / name).read_bytes()).hexdigest()
                for name in ("src/koipa/mapping_proposal.py", "src/koipa/policy_facts.py", "src/koipa/evidence_collection.py",
                             "scripts/check_customer_mapping.py", "scripts/check_policy_shadow.py", "scripts/collect_policy_evidence.py")}
        if args.preview_out and artifact is not None:
            _write_new(args.preview_out, artifact)
        if args.out:
            _write_new(args.out, report)
        if args.schema:
            summary = report if not args.out else {"status": "SCHEMA_WRITTEN", "cross_field_checks_required": True}
        elif args.demo:
            summary = {"mode": report["mode"], "real_documents": 0,
                       "cases": [{"case_id": r["case_id"], "fields": r["result"]["fields"]} for r in report["results"]]}
        else:
            summary = {key: report[key] for key in ("status", "fields", "approval_authenticity_verified", "fact_export_allowed")}
            summary["replay_verified"] = bool(report.get("replay_verified"))
            summary["sensitive_preview_exported"] = args.preview_out is not None
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return exit_code
    except (FactContractError, ValidationError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "INVALID_MAPPING_INPUT", "error_code": str(exc)
                          if isinstance(exc, FactContractError) else "invalid_mapping_file_or_schema",
                          "fact_export_allowed": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
