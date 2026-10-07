"""Offline v2 manifest/submission checks. No uploads, identity, approvals or GOLD.

Exit 3: structurally valid draft still requiring review; 2: invalid; 0: demo/schema.
--template-out writes only a blank metadata worksheet, never expected answers.
All fictional demo inputs are policy fixtures, not quality evaluation data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from koipa.policy_facts import FactContractError, FactPacket, require, text_digest, value_digest  # noqa: E402
from koipa.policy_shadow import parse_shadow_policy, policy_digest  # noqa: E402
from koipa.review_v2 import (  # noqa: E402
    SAFE_FLAGS, ReviewContext, ReviewManifest, SubmissionBatch, blank_submissions,
    inspect_manifest, manifest_digest, validate_submissions,
)
from check_policy_shadow import _read  # noqa: E402
from collect_policy_evidence import _write_new  # noqa: E402


def demo_inputs():
    """Same rule ID, different injected grades. No real reviewer or real permit."""
    demos = []
    for grade in ("S1", "S2"):
        org = "fictional-org-" + grade
        policy = {"org_id": org, "policy_version": "fictional-draft-" + grade,
                  "effective_date": "2026-09-15", "grade_order": ["TS", "S1", "S2", "S3"],
                  "rules": [{"id": "DECLARED", "grade": grade, "priority": 10,
                             "when": {"access_scope": {"value": "approved_only"}},
                             "requires_evidence": ["access_scope"]},
                            {"id": "OTHER", "grade": "S3", "priority": 90,
                             "when": {"access_scope": {"value": "all_employees"}}}],
                  "default_grade": "S3", "note": "Fictional mechanics only, not customer policy"}
        policy_sha = policy_digest(parse_shadow_policy(policy))
        scope = {"org_id": org, "document_id": "fictional-document", "document_sha256": text_digest("fictional-text")}
        payload = {"access_scope": "approved_only"}
        source_sha = value_digest(payload)
        packet = FactPacket.model_validate({"schema_version": "policy-facts-v1-draft",
            "material_role": "synthetic_policy_fixture", **scope, "policy_version": policy["policy_version"],
            "policy_sha256": policy_sha, "sources": [{**scope, "source_id": "fictional-metadata", "kind": "system_metadata",
                "payload": payload, "payload_sha256": source_sha, "captured_at": "2026-09-15T00:00:00Z",
                "source_ref": "fictional-not-authenticated"}], "facts": [{"fact": "access_scope", "state": "observed",
                "value": "approved_only", "origin": "system_metadata", "evidence": [{"source_id": "fictional-metadata",
                    "source_sha256": source_sha, "locator": {"kind": "json_pointer", "pointer": "/access_scope"},
                    "value_sha256": value_digest("approved_only")}]}]}).model_dump()
        manifest = ReviewManifest.model_validate({"schema_version": "real-document-review-input-v2-draft",
            "job_id": "fictional-job", "org_id": org, "created_at": "2026-09-15T02:00:00Z",
            "purpose": "policy_review", "policy_id": "fictional-policy-" + grade,
            "policy": policy, "policy_sha256": policy_sha, "authorization": {"org_id": org},
            "cases": [{"case_id": "fictional-case", "document": {**scope, "document_revision": "r1",
                "original_sha256": text_digest("fictional-original"), "as_of": "2026-09-15T01:00:00Z"},
                "family_id": "fictional-family", "source_origin": "synthetic", "input_view": "evidence_only",
                "extraction_state": "complete", "packet": packet, "packet_sha256": value_digest(packet),
                "presented_source_ids": ["fictional-metadata"]}],
            "assignments": [{"slot": "independent-A", "case_ids": ["fictional-case"],
                             "assigned_at": "2026-09-15T02:00:00Z"}]}).model_dump()
        context = ReviewContext(org_id=org, job_id=manifest["job_id"], manifest_sha256=manifest_digest(manifest),
                                as_of="2026-09-15T03:00:00Z")
        demos.append((manifest, context))
    return demos


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--slot")
    for name in ("manifest", "context", "submissions", "out", "template-out"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    files = {}
    try:
        require(not (args.demo and args.schema), "review_choose_one_mode")
        require(not ((args.demo or args.schema) and any((args.manifest, args.context, args.submissions,
                                                        args.template_out, args.slot))), "review_mixed_modes")
        require(bool(args.template_out) == bool(args.slot), "review_template_slot_required")
        require(not (args.template_out and args.submissions), "review_template_submission_modes_exclusive")
        outputs = [p.resolve() for p in (args.out, args.template_out) if p is not None]
        inputs = [p.resolve() for p in (args.manifest, args.context, args.submissions) if p is not None]
        require(len(set(outputs)) == len(outputs) and not set(outputs) & set(inputs), "review_output_overlap")
        require(all(not p.exists() for p in outputs), "output_exists")
        template = None
        if args.schema:
            report = {"manifest": ReviewManifest.model_json_schema(), "context": ReviewContext.model_json_schema(),
                      "submissions": SubmissionBatch.model_json_schema(), "cross_field_checks_required": True}
        elif args.demo:
            report = {**SAFE_FLAGS, "mode": "synthetic_review_binding_demo", "dataset_role": "policy_fixture",
                      "real_documents": 0, "human_submissions": 0,
                      "results": [inspect_manifest(m, context=c) for m, c in demo_inputs()]}
        else:
            require(args.manifest is not None and args.context is not None, "review_manifest_context_required")
            manifest, context = _read(args.manifest, files), _read(args.context, files)
            report = (validate_submissions(manifest, _read(args.submissions, files), context=context)
                      if args.submissions else inspect_manifest(manifest, context=context))
            if args.template_out:
                template = blank_submissions(manifest, context=context, slot=args.slot)
        for path, digest in files.items():
            require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, "review_input_changed_during_check")
        if not args.schema:
            report["input_files_sha256"] = {name: files[str(path.resolve())] for name, path in (
                ("manifest", args.manifest), ("context", args.context), ("submissions", args.submissions)) if path}
            report["implementation_sha256"] = {name: hashlib.sha256((POC / name).read_bytes()).hexdigest() for name in (
                "src/koipa/review_v2.py", "src/koipa/policy_facts.py", "src/koipa/policy_shadow.py",
                "src/koipa/evidence_collection.py", "src/koipa/modules/m3_labeling/policy_engine.py",
                "scripts/check_document_review.py", "scripts/check_policy_shadow.py", "scripts/collect_policy_evidence.py")}
        if template is not None:
            _write_new(args.template_out, template)
        if args.out:
            _write_new(args.out, report)
        print(json.dumps(report if not args.schema or not args.out else {"status": "SCHEMA_WRITTEN"},
                         ensure_ascii=False, indent=2))
        return 0 if args.demo or args.schema else 3
    except (FactContractError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({**SAFE_FLAGS, "status": "INVALID_REVIEW_INPUT", "error_code": str(exc)
                          if isinstance(exc, FactContractError) else "invalid_review_file_or_schema"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
