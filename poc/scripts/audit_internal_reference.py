"""Read-only finite-reference diagnostics; writes a NEW evidence file only.

Style-only classifier is a diagnostic probe, not the production model. No model
accuracy, real anonymization or general natural-language validity is asserted.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import subprocess
from collections import Counter
from pathlib import Path

from build_internal_reference import POC, _json, _loads, verify_pack
from koipa.dataset_usage import body_fingerprint, fixture_registry
from koipa.internal_reference import FLAGS, certify_reference, style_view
from koipa.policy_facts import require
from measure_ngram_shortcuts import measure


def audit(pack, *, seeds=3):
    validation = verify_pack(pack)
    inputs = [_loads(line)["input"] for line in (pack / "inputs.jsonl").read_text(encoding="utf-8").splitlines()]
    answers = {_loads(line)["certificate"]["doc_id"]: _loads(line)
               for line in (pack / "answers.jsonl").read_text(encoding="utf-8").splitlines()}
    controls = Counter()
    style_rows = []
    registry_hashes = {fp for r in fixture_registry()["records"] for fp in r["body_fingerprints"]}
    registered_overlap = []
    for raw in inputs:
        original = answers[raw["doc_id"]]["certificate"]
        view = copy.deepcopy(raw)
        view["context"] = None
        require(certify_reference(view)["status"] == "hold", "body_only_did_not_hold")
        controls["body_only_hold"] += 1
        view = copy.deepcopy(raw)
        view["context"]["release_authorized"] = not view["context"]["release_authorized"]
        flipped = certify_reference(view)
        if original["reference_grade"] in {"TS", "S1"}:
            require(flipped["reference_grade"] == original["reference_grade"], "high_grade_changed_on_release")
            controls["high_grade_release_invariance"] += 1
        else:
            require({flipped["reference_grade"], original["reference_grade"]} == {"S2", "S3"}, "low_grade_counterfactual_failed")
            controls["s2_s3_release_counterfactual"] += 1
            view["context"]["release_authorized"] = None
            require(certify_reference(view)["status"] == "hold", "unknown_release_did_not_hold")
            controls["unknown_release_hold"] += 1
        style_rows.append({"text": style_view(raw), "label": original["reference_grade"],
                           "family_id": answers[raw["doc_id"]]["family_id"]})
        if body_fingerprint(raw["text"]) in registry_hashes:
            registered_overlap.append(raw["doc_id"])
    require(not registered_overlap, "registered_fixture_overlap")
    probe = measure(style_rows, seeds=seeds, folds=5)
    # Prefix visibility is NOT tokenization and does NOT model serving chunk aggregation.
    prefix = Counter()
    for raw in inputs:
        grade = answers[raw["doc_id"]]["certificate"]["reference_grade"]
        prefix[(raw["text"][:1536], grade)] += 1
    lengths = [len(r["text"]) for r in inputs]
    return {**FLAGS, "schema_version": "internal-fixed-reference-audit-v0.1", "validation": validation,
            "pack_manifest_sha256": hashlib.sha256((pack / "manifest.json").read_bytes()).hexdigest(),
            "controls": dict(controls), "style_only_probe": probe,
            "style_probe_scope": "title, table order, columns, row counts only; no row values or context",
            "full_content_shortcut_probe_run": False, "characters": {"min": min(lengths), "max": max(lengths)},
            "model_input_compatibility": "not_verified; full-document cross-table join and context are required",
            "prefix_1536_diagnostic_only": {"unique_grade_prefix_pairs": len(prefix), "serving_simulation": False},
            "registered_fixture_overlap": {"registry_records": len(fixture_registry()["records"]),
                "registry_sha256": hashlib.sha256((POC / "src/koipa/policy_fixture_registry.json").read_bytes()).hexdigest(),
                "comparison": "whitespace-normalized exact body", "matches": registered_overlap},
            "all_training_pool_overlap_measured": False,
            "python": platform.python_version(),
            "git_head": subprocess.check_output(["git", "-C", str(POC), "rev-parse", "HEAD"], text=True).strip(),
            "source_sha256": {p: hashlib.sha256((POC / p).read_bytes()).hexdigest() for p in
                ("scripts/audit_internal_reference.py", "scripts/measure_ngram_shortcuts.py", "src/koipa/dataset_usage.py")}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        require(not args.out.exists(), "output_exists")
        pack = args.pack.resolve()
        require(not args.out.resolve().is_relative_to(pack), "audit_output_must_not_modify_pack")
        result = audit(pack, seeds=args.seeds)
        verify_pack(pack)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_json(result))
        print(json.dumps({"status": "internal_reference_audited", "controls": result["controls"],
            "style_family_cv": result["style_only_probe"]["family_cv"]["mean"],
            "customer_accuracy_measured": False}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "invalid", "code": "internal_reference_audit_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
