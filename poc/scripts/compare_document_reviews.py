"""Offline comparison and adjudication-proposal checks. No final grades or GOLD.

Exit 3: valid review-only result; 2: invalid input/output; 0: demo/schema only.
Reports are coordinator-only and must not be fed back before blind submission.
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

from koipa.policy_facts import FactContractError, require  # noqa: E402
from koipa.review_pair_v2 import (  # noqa: E402
    AdjudicationBatch, PairBinding, blank_adjudications, compare_submissions, validate_adjudications,
)
from koipa.review_v2 import SAFE_FLAGS, ReviewContext, blank_submissions, manifest_digest  # noqa: E402
from check_document_review import demo_inputs as manifest_demos  # noqa: E402
from check_policy_shadow import _read  # noqa: E402
from collect_policy_evidence import _write_new  # noqa: E402


def demo_inputs():
    demos = []
    for manifest, context in manifest_demos():
        other = copy.deepcopy(manifest["assignments"][0])
        other["slot"] = "independent-B"
        manifest["assignments"].append(other)
        context = context.model_copy(update={"manifest_sha256": manifest_digest(manifest)})
        left, right = [blank_submissions(manifest, context=context, slot=a["slot"]) for a in manifest["assignments"]]
        demos.append((manifest, context, left, right))
    return demos


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--schema", action="store_true")
    for name in ("manifest", "context", "left", "right", "adjudications", "out", "template-out"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    snapshots = {}
    try:
        input_args = [(name, getattr(args, name)) for name in ("manifest", "context", "left", "right", "adjudications")]
        require(not (args.demo and args.schema), "review_pair_choose_one_mode")
        require(not ((args.demo or args.schema) and (any(p for _, p in input_args) or args.template_out)),
                "review_pair_mixed_modes")
        require(not (args.template_out and args.adjudications), "review_pair_template_submission_modes_exclusive")
        inputs = [p.resolve() for _, p in input_args if p is not None]
        outputs = [p.resolve() for p in (args.out, args.template_out) if p is not None]
        require(len(set(outputs)) == len(outputs) and not set(inputs) & set(outputs), "review_pair_output_overlap")
        require(all(not p.exists() for p in outputs), "output_exists")
        template = None
        if args.schema:
            report = {"pair_binding": PairBinding.model_json_schema(), "adjudications": AdjudicationBatch.model_json_schema(),
                      "context": ReviewContext.model_json_schema(), "cross_field_checks_required": True}
        elif args.demo:
            report = {**SAFE_FLAGS, "mode": "synthetic_pair_binding_demo", "dataset_role": "policy_fixture",
                      "real_documents": 0, "human_submissions": 0, "adjudications": 0,
                      "results": [compare_submissions(m, a, b, context=c) for m, c, a, b in demo_inputs()]}
        else:
            require(all((args.manifest, args.context, args.left, args.right)), "review_pair_four_inputs_required")
            manifest, context, left, right = [_read(p, snapshots) for p in (args.manifest, args.context, args.left, args.right)]
            report = (validate_adjudications(manifest, left, right, _read(args.adjudications, snapshots), context=context)
                      if args.adjudications else compare_submissions(manifest, left, right, context=context))
            if args.template_out:
                template = blank_adjudications(manifest, left, right, context=context)
        for path, digest in snapshots.items():
            require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, "review_pair_input_changed_during_check")
        if not args.schema:
            report["input_files_sha256"] = {name: snapshots[str(path.resolve())] for name, path in input_args if path}
            report["implementation_sha256"] = {name: hashlib.sha256((POC / name).read_bytes()).hexdigest() for name in (
                "src/koipa/review_pair_v2.py", "src/koipa/review_v2.py", "src/koipa/policy_facts.py",
                "src/koipa/policy_shadow.py", "src/koipa/evidence_collection.py", "src/koipa/modules/m3_labeling/policy_engine.py",
                "scripts/compare_document_reviews.py", "scripts/check_document_review.py", "scripts/check_policy_shadow.py",
                "scripts/collect_policy_evidence.py")}
        if template is not None:
            _write_new(args.template_out, template)
        if args.out:
            _write_new(args.out, report)
        print(json.dumps(report if not args.schema or not args.out else {"status": "SCHEMA_WRITTEN"}, ensure_ascii=False, indent=2))
        return 0 if args.demo or args.schema else 3
    except (FactContractError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({**SAFE_FLAGS, "status": "INVALID_REVIEW_PAIR_INPUT", "error_code": str(exc)
                          if isinstance(exc, FactContractError) else "invalid_review_pair_file_or_schema"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
