"""Shortcut diagnostic for the new guide pack format, never customer accuracy.

Unlike old --pool adapters this reads the verified new format explicitly. Zero
rows, unsupported packs and missing answers fail; no eligibility flags change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import build_customer_guide_reference as pack
from measure_ngram_shortcuts import measure
from koipa.customer_benchmark import FLAGS, duplicate_audit, presented_text
from koipa.customer_guide_reference import validate_reference
from koipa.policy_facts import require, text_digest


def excess_warning(observed):
    return any(observed[k].get("excess_pp", 0) > 20 + 1e-9 for k in ("stratified_cv", "family_cv"))


def project_rows(docs, answers, profile):
    require(profile in {"body_only", "context_only", "body_context"}, "guide_probe_profile_invalid")
    require(bool(docs) and len(docs) == len(answers), "guide_probe_zero_or_missing_answers")
    by_id = {a["doc_id"]: a["reference_grade"] for a in answers}
    require(len(by_id) == len(docs) and set(by_id) == {d.input.doc_id for d in docs}, "guide_probe_answer_ids_invalid")
    groups = {doc_id: group for group, ids in duplicate_audit(docs)["groups"].items() for doc_id in ids}
    result = []
    for d in docs:
        text = (json.dumps([c.model_dump() for c in d.input.context], ensure_ascii=False, sort_keys=True)
                if profile == "context_only" else presented_text(d, profile))
        result.append({"doc_id": d.input.doc_id, "text": text, "label": by_id[d.input.doc_id], "family_id": groups[d.input.doc_id]})
    return result


def audit(root, out, *, seeds=5):
    root, out = root.resolve(), out.resolve()
    require(not out.exists() and not out.is_relative_to(root), "guide_probe_output_invalid")
    require(type(seeds) is int and 1 <= seeds <= 100, "guide_probe_seeds_invalid")
    pack.verify(root)
    manifest_sha = hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest()
    records = pack._rows((root/"authoring/documents.jsonl").read_bytes())
    answers = pack._rows((root/"answers/answers.candidate.jsonl").read_bytes())
    details = pack._rows((root/"answers/evidence.jsonl").read_bytes())
    docs = validate_reference(records, answers, details)
    result = {**FLAGS, "status": "diagnostic_only_small_sample_not_qualified", "documents": len(docs),
              "source_pack_manifest_sha256": manifest_sha,
              "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                (Path(__file__), Path(__file__).with_name("measure_ngram_shortcuts.py"))},
              "profiles": {}, "body_only_labels_identifiable": False,
              "warning": "20 stipulated cases cannot certify no leakage or customer accuracy. Context is a legitimate policy premise; body-only labels are not identifiable.",
              "threshold": {"excess_pp": 20, "float_tolerance_pp": 1e-9, "authority": "diagnostic_warning_not_acceptance_rule"}}
    for profile in ("body_only", "context_only", "body_context"):
        rows = project_rows(docs, answers, profile)
        observed = measure(rows, seeds=seeds)
        observed["presented_sha256"] = {r["doc_id"]: text_digest(r["text"]) for r in rows}
        observed["warning_excess_over_permutation"] = excess_warning(observed)
        result["profiles"][profile] = observed
    pack.verify(root)
    require(hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest() == manifest_sha, "guide_probe_pack_changed")
    pack._new_file(out, pack._json(result))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pack", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--seeds", type=int, default=5)
    args = p.parse_args(argv)
    try:
        r = audit(args.pack, args.out, seeds=args.seeds)
        print(json.dumps({"status": r["status"], "documents": r["documents"],
                         "profiles": {k: {s: v[s] for s in ("stratified_cv", "family_cv")} for k, v in r["profiles"].items()}}, ensure_ascii=False))
        return 0  # Measurement completed, NOT a data quality pass.
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "guide_shortcut_diagnostic_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
