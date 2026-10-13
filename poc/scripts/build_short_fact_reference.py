"""New immutable text-fact references and conditional policy sidecars. No ML.

Reads the prior input-fit pack; never rewrites it or imports its proposed labels
as grading features. Prior hypotheses are compared only AFTER recalculation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from build_internal_reference import _json, _jsonl, _loads
from koipa.policy_facts import require, text_digest, value_digest
from koipa.short_body_facts import FLAGS
from koipa.short_reference_policy import (POLICY, POLICY_SHA256, SyntheticContext, apply_reference_policy,
                                         certify_body, decide, features, sql_decision)

SOURCES = ("src/koipa/short_body_facts.py", "src/koipa/short_body_reference.py", "src/koipa/short_reference_policy.py",
           "src/koipa/policy_facts.py", "scripts/build_short_fact_reference.py",
           "docs/SHORT_BODY_FACT_REFERENCE_V1.md")


def read_pack(path):
    root = path.resolve()
    require(root.is_dir(), "fact_pack_directory_required")
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = _loads(manifest_bytes.decode("utf-8"))
    require(manifest.get("schema_version") in {"reference-input-fit-pack-v0.2", "short-fact-reference-pack-v1"}, "fact_pack_schema_invalid")
    require(value_digest({k: manifest[k] for k in FLAGS}) == value_digest(FLAGS), "fact_pack_flags_invalid")
    files = manifest.get("files")
    require(isinstance(files, dict) and bool(files), "fact_pack_files_empty")
    data = {}
    for name, digest in files.items():
        target = (root / name).resolve()
        require(target.is_relative_to(root) and target != root and not Path(name).is_absolute() and "\\" not in name,
                "fact_pack_path_escape")
        raw = target.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == digest, "fact_pack_file_hash_mismatch")
        data[name] = raw
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    require(actual == set(files) | {"manifest.json"}, "fact_pack_unlisted_file")
    require((root / "manifest.json").read_bytes() == manifest_bytes, "fact_pack_changed_during_read")
    for name, raw in data.items():
        require((root / name).read_bytes() == raw, "fact_pack_changed_during_read")
    return manifest, data


def rows(data):
    return [_loads(line) for line in data.decode("utf-8").splitlines()]


def derive(inputs, old_annotations):
    require(len(inputs) == len(old_annotations) == 21, "fact_reference_requires_21_inputs")
    old = {r["doc_id"]: r for r in old_annotations}
    require(len(old) == len({r["input"]["doc_id"] for r in inputs}) == 21, "fact_reference_duplicate_id")
    certs, results, groups, compared = {}, [], defaultdict(list), []
    for row in inputs:
        require(set(row) == set(FLAGS) | {"input"}, "fact_reference_input_wrapper_invalid")
        require(value_digest({k: row[k] for k in FLAGS}) == value_digest(FLAGS), "fact_reference_input_flags_invalid")
        raw = row["input"]
        require(set(raw) == {"doc_id", "text", "document_sha256", "context"}, "fact_reference_input_fields_invalid")
        require(raw["document_sha256"] == text_digest(raw["text"]) and raw["doc_id"] in old and
                value_digest(raw) == old[raw["doc_id"]]["input_sha256"], "fact_reference_input_binding_invalid")
        cert = certify_body(raw["text"])
        require(cert["status"] == "fixed_text_facts", "fact_reference_body_not_fixed")
        digest = raw["document_sha256"]
        require(digest not in certs or value_digest(certs[digest]) == value_digest(cert), "fact_reference_context_changed_body_facts")
        certs[digest] = cert
        # The body+context evaluator never receives old grade, ID, family or annotations.
        answer = apply_reference_policy(raw["text"], raw["context"])
        results.append({**FLAGS, "doc_id": raw["doc_id"], "input_sha256": value_digest(raw), "answer": answer})
        groups[digest].append({"doc_id": raw["doc_id"], "body_certificate_sha256": value_digest(cert),
                               "status": answer["status"], "grade": answer["reference_grade"]})
        compared.append({"doc_id": raw["doc_id"], "old_hypothesis": old[raw["doc_id"]]["proposed_grade"],
                         "recalculated": answer["reference_grade"],
                         "grade_matches": old[raw["doc_id"]]["proposed_grade"] == answer["reference_grade"],
                         "status_matches": (old[raw["doc_id"]]["proposed_status"] == "hold") == (answer["status"] == "hold")})
    require(len(certs) == 9, "fact_reference_requires_nine_bodies")
    coverage = []
    # Exhaustive trivalent context checks for EACH certified supplied body.
    for digest, cert in certs.items():
        f = features(cert)
        for scope, private, released, core, other in product((False, True, None), repeat=5):
            c = SyntheticContext(origin="synthetic_assumption", scope_complete=scope, private_current_revision=private,
                release_authorized_for_this_revision=released, current_core_asset=core, other_high_risk_material=other,
                world="fictional; no real persons, access secrets or customer assets")
            grade, rule, reason = decide(f, c)
            require(grade == sql_decision(f, c), "fact_reference_exhaustive_policy_disagreement")
            coverage.append({"body_sha256": digest, "context": c.model_dump(), "grade": grade, "rule": rule, "reason": reason})
    facts = [f for cert in certs.values() for f in cert["facts"]]
    states = Counter(f["state"] for f in facts)
    interpretations = Counter(f["interpretation"] for f in facts)
    counts = Counter(r["answer"]["reference_grade"] or "HOLD" for r in results)
    return {"body_certificates": list(certs.values()), "policy_answers": results, "comparison": compared, "context_table": coverage,
            "summary": {**FLAGS, "unique_bodies": 9, "facts": len(facts), "fact_states": dict(states),
                "fact_interpretations": dict(interpretations), "evidence_links": sum(len(f["evidence"]) for f in facts),
                "context_cases": 21, "conditional_grade_counts": dict(counts), "policy_version": POLICY["version"],
                "context_invariance_groups": dict(groups), "policy_cross_checks": len(coverage),
                "old_hypothesis_grade_matches": sum(r["grade_matches"] for r in compared),
                "old_hypothesis_status_matches": sum(r["status_matches"] for r in compared),
                "customer_grade_accuracy_measured": False, "different_policy_packs_must_not_be_pooled": True}}


def build(source, out):
    require(not out.exists() and not out.resolve().is_relative_to(source.resolve()), "fact_reference_output_invalid")
    source_manifest_bytes = (source / "manifest.json").read_bytes()
    manifest, source_data = read_pack(source)
    require(manifest["schema_version"] == "reference-input-fit-pack-v0.2", "fact_reference_source_schema_invalid")
    inputs, annotations = rows(source_data["inputs.draft.jsonl"]), rows(source_data["annotations.draft.jsonl"])
    computed = derive(inputs, annotations)
    sources = {name: hashlib.sha256((POC / name).read_bytes()).hexdigest() for name in SOURCES}
    payloads = {"inputs.jsonl": _jsonl(inputs), "prior_hypotheses.jsonl": _jsonl(annotations), "policy.json": _json(POLICY),
        "body_facts.fixed.jsonl": _jsonl(computed["body_certificates"]), "policy_answers.fixed.jsonl": _jsonl(computed["policy_answers"]),
        "prior_comparison.jsonl": _jsonl(computed["comparison"]), "context_cross_check.jsonl": _jsonl(computed["context_table"]),
        "summary.json": _json(computed["summary"]),
        "README.md": "# 본문 사실 정답 v1 / 조건부 정책 v0.3\n\n본문9종의30사실과21맥락(등급18/HOLD3).\n"
        "정답은 이 등록된 본문·가상 맥락·내부 정책에만 유효합니다. 고객 GOLD/학습/모델 성능 분모 사용 금지.\n"
        "기존 v0.1 원장40건과 정책이 다릅니다. 58개 독립 정답으로 합산하지 마세요.\n"}
    for row in inputs:
        raw = row["input"]
        payloads[f"documents/{raw['document_sha256']}.md"] = raw["text"]
    require(read_pack(source)[1] == source_data, "fact_reference_source_changed")
    require((source / "manifest.json").read_bytes() == source_manifest_bytes, "fact_reference_source_manifest_changed")
    out.mkdir(parents=True, exist_ok=False)
    for name, value in payloads.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
    output_manifest = {**FLAGS, "schema_version": "short-fact-reference-pack-v1", "policy_sha256": POLICY_SHA256,
        "source_pack_manifest_sha256": hashlib.sha256(source_manifest_bytes).hexdigest(),
        "source_files_sha256": sources, "files": {n: text_digest(t) for n, t in payloads.items()}}
    with (out / "manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json(output_manifest))
    return verify(out)


def verify(path):
    manifest, data = read_pack(path)
    require(manifest["schema_version"] == "short-fact-reference-pack-v1" and manifest["policy_sha256"] == POLICY_SHA256, "fact_reference_manifest_invalid")
    require(set(manifest["source_files_sha256"]) == set(SOURCES), "fact_reference_source_list_invalid")
    for name, digest in manifest["source_files_sha256"].items():
        require(hashlib.sha256((POC / name).read_bytes()).hexdigest() == digest, "fact_reference_source_drift")
    require(value_digest(_loads(data["policy.json"].decode("utf-8"))) == value_digest(POLICY), "fact_reference_policy_file_invalid")
    inputs = rows(data["inputs.jsonl"])
    expected_files = {"inputs.jsonl", "prior_hypotheses.jsonl", "policy.json", "body_facts.fixed.jsonl",
                      "policy_answers.fixed.jsonl", "prior_comparison.jsonl", "context_cross_check.jsonl",
                      "summary.json", "README.md"} | {f"documents/{r['input']['document_sha256']}.md" for r in inputs}
    require(set(data) == expected_files, "fact_reference_payload_list_invalid")
    computed = derive(inputs, rows(data["prior_hypotheses.jsonl"]))
    for name, key in (("body_facts.fixed.jsonl", "body_certificates"), ("policy_answers.fixed.jsonl", "policy_answers"),
                      ("prior_comparison.jsonl", "comparison"), ("context_cross_check.jsonl", "context_table")):
        require(value_digest(rows(data[name])) == value_digest(computed[key]), "fact_reference_replay_mismatch")
    require(value_digest(_loads(data["summary.json"].decode("utf-8"))) == value_digest(computed["summary"]), "fact_reference_summary_mismatch")
    for row in inputs:
        raw = row["input"]
        require(data[f"documents/{raw['document_sha256']}.md"].decode("utf-8") == raw["text"], "fact_reference_document_mismatch")
    return computed["summary"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    try:
        require(bool(args.verify) != bool(args.source and args.out) and not (args.verify and (args.source or args.out)), "fact_reference_choose_build_or_verify")
        summary = verify(args.verify) if args.verify else build(args.source, args.out)
        print(json.dumps({k: summary[k] for k in ("unique_bodies", "facts", "conditional_grade_counts", "policy_cross_checks")}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"status": "invalid", "code": "short_fact_reference_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
