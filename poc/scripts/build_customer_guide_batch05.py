"""Append 32 crossed manuscripts to immutable 164; no release or serving changes."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

import build_customer_guide_batch04 as previous
from customer_guide_batch05 import CASES, build_new_cases
from prepare_customer_benchmark import _json, _jsonl, _new_file, _rows, token_audit
from koipa.customer_benchmark import FLAGS, GRADES, audit_external_pool, duplicate_audit, presented_text, strict_loads
from koipa.customer_guide_reference import POLICY, POLICY_SHA256, validate_reference
from koipa.policy_facts import require, text_digest, value_digest

PARENT_MANIFEST = "dcc8ea80ad4e3ff6fadf21a4debe20212ac015a2892ebd444cdee73917a07f41"
SCHEMA = "customer-guide-batch05-pack-v0.5"
SOURCES = ("scripts/customer_guide_batch05.py", "scripts/build_customer_guide_batch05.py")
DOMAINS = ("측정·실험", "소프트웨어·시스템", "영업·거래·조사", "운영·물류·시설")
REGISTERS = ("plain", "polite")


def arithmetic(docs, specs=None):
    """Reuse the decimal arithmetic evaluator, not its hard-coded old counts."""
    specs = CASES if specs is None else specs
    checks = previous.arithmetic(docs, specs=specs)["new_checks"]
    return {"new_checks": checks, "new_passed": len(checks),
            "scope": "Numeric expressions quoted from each new body, not policy answers or all prose.",
            "all_prose_semantically_certified": False}


def cross_audit(new, specs=None):
    specs = CASES if specs is None else specs
    by_key = {r["key"]: r for r in specs}
    require(len(new) == len(specs) == len(by_key) == 32, "batch05_cross_count_invalid")
    cells, forms, grades = Counter(), Counter(), Counter()
    metadata = []
    for record, answer, _ in new:
        key = record["family_id"].removeprefix("family-")
        spec = by_key[key]
        require(record["domain"] == spec["domain"] and spec["domain"] in DOMAINS,
                "batch05_domain_mismatch")
        require(spec["register"] in REGISTERS and spec["form"] in {"memo", "log", "qa", "procedure"},
                "batch05_style_invalid")
        grade = answer["reference_grade"]
        cells[spec["domain"], spec["register"], grade] += 1
        forms[spec["form"], grade] += 1
        grades[grade] += 1
        metadata.append({**FLAGS, "doc_id": record["input"]["doc_id"],
            "body_sha256": text_digest(record["input"]["text"]), "family_id": record["family_id"],
            "domain": spec["domain"], "register": spec["register"], "form": spec["form"],
            "independence_note": spec["independence_note"],
            "register_origin": "authored_assignment_not_automatic_style_certification",
            "semantic_independence_certified": False, "source": "customer_guide_batch05.py:"+key,
            "intended_side_after_explicit_acceptance": "development_training_candidate_only",
            "final_blind_evaluation_eligible": False})
    require(set(cells) == {(d, r, g) for d in DOMAINS for r in REGISTERS for g in GRADES}
            and all(n == 1 for n in cells.values()), "batch05_cross_balance_invalid")
    return {**FLAGS, "n": len(new), "grade_counts": {g: grades[g] for g in GRADES},
            "domain_register_grade_counts": {
                d: {r: {g: cells[d, r, g] for g in GRADES} for r in REGISTERS} for d in DOMAINS},
            "form_grade_counts": {f: {g: forms[f, g] for g in GRADES}
                                  for f in ("memo", "log", "qa", "procedure")},
            "metadata": sorted(metadata, key=lambda r: r["doc_id"]),
            "body_style_bias_resolved": False, "semantic_independence_certified": False,
            "controlled_before_after_comparison": False,
            "note": "Authored independent content, not counterfactual views; grade balance is not leakage certification."}


def core_payload():
    old_docs, old_answers, parent = previous.core_payload()
    old_records = _rows(parent["authoring/documents.jsonl"].encode())
    old_details = _rows(parent["answers/evidence.jsonl"].encode())
    new = build_new_cases()
    require(len(old_docs) == 164 and len(new) == len(CASES) == 32, "batch05_count_invalid")
    records = sorted(old_records+[r for r, _, _ in new], key=lambda r: r["input"]["doc_id"])
    answers = sorted(old_answers+[a for _, a, _ in new], key=lambda a: a["doc_id"])
    details = sorted(old_details+[e for _, _, e in new], key=lambda e: e["doc_id"])
    docs = validate_reference(records, answers, details)
    require(len(docs) == 196, "batch05_total_count_invalid")
    duplicates = duplicate_audit(docs)
    require(not any(duplicates[k] for k in ("exact_pairs", "number_only_pairs", "near_pairs", "known_fixture_body_matches")),
            "batch05_duplicate_blocked")
    math = arithmetic(docs)
    old_summary = strict_loads(parent["summary.json"])
    math.update(previous_passed=old_summary["arithmetic_checks"],
                total_listed_checks=old_summary["arithmetic_checks"]+math["new_passed"])
    cross = cross_audit(new)
    counts = Counter(a["reference_grade"] for a in answers)
    summary = {**FLAGS, "status": "conditional_reference_candidates_not_released", "pack_version": "0.5",
        "policy_sha256": POLICY_SHA256, "policy_version": POLICY["version"],
        "unique_bodies": len(docs), "new_bodies": len(new), "unchanged_previous_bodies": len(old_docs),
        "internally_fixed_conditional_answers": len(answers), "grade_counts": {g: counts[g] for g in GRADES},
        "new_grade_counts": cross["grade_counts"], "target_documents": 1000,
        "remaining_before_rejections": {g: 250-counts[g] for g in GRADES},
        "accepted_train": 0, "accepted_evaluation": 0, "body_only_grade_eligible": 0,
        "quote_bound_claims": sum(len(d.claims) for d in docs), "context_fact_bindings": 14*len(docs),
        "arithmetic_checks": math["total_listed_checks"], "new_arithmetic_checks": math["new_passed"],
        "new_domains": {d: 8 for d in DOMAINS}, "new_registers": {r: 16 for r in REGISTERS},
        "model_accuracy": None, "customer_protocol_accepted": False, "reviewer_signatures_created": 0,
        "final_blind_evaluation_eligible_new": 0, "semantic_independence_certified": False,
        "style_bias_resolved": False}
    by_detail = {e["doc_id"]: e for e in details}
    answer_lines = ["# 내부 조건부 참조 답안 196건", "",
        "본문+명시된 가상 맥락, 내부 기준0.1 아래 답안이다. 고객 GOLD/본문 단독 정답/배포 채택이 아니다.", "",
        "| 문서 | S/V/M | 곱 | 답 |", "|---|---|---:|---|"]
    for d in docs:
        decision = by_detail[d.input.doc_id]["decision"]
        levels = "/".join(str(decision["factors"][k]) for k in ("S", "V", "M"))
        answer_lines.append(f"| {d.input.text.splitlines()[0]} | {levels} | {decision['product']} | {decision['reference_grade']} |")
    for d, a in zip(docs, answers, strict=True):
        require(d.input.doc_id == a["doc_id"], "batch05_answer_order_mismatch")
        e = by_detail[d.input.doc_id]
        answer_lines.extend(["", "## "+d.input.text.splitlines()[0], "", "문서 ID: "+a["doc_id"], "",
            e["rationale"], "", f"조건부 답: {a['reference_grade']}. 본문 단독은 HOLD/등급 채점 제외.", "",
            *[f"- {g} 배제: {why}" for g, why in a["other_grade_exclusions"].items()]])
    binding = {"expected_parent_manifest_sha256": PARENT_MANIFEST, "parent_documents": len(old_docs),
               "records_sha256": value_digest(old_records), "answers_sha256": value_digest(old_answers),
               "details_sha256": value_digest(old_details), "policy_changed": False}
    payload = {"policy.json": _json(POLICY), "summary.json": _json(summary),
        "authoring/documents.jsonl": _jsonl(records), "authoring/batch05_metadata.jsonl": _jsonl(cross["metadata"]),
        "answers/answers.candidate.jsonl": _jsonl(answers), "answers/evidence.jsonl": _jsonl(details),
        "answers/REFERENCE_ANSWERS.md": "\n".join(answer_lines)+"\n",
        "inputs/body_context.jsonl": _jsonl([{**FLAGS, **d.input.model_dump(), "input_sha256": d.input_sha256} for d in docs]),
        "audit/duplicates.json": _json(duplicates), "audit/arithmetic.json": _json(math),
        "audit/cross_design.json": _json(cross), "audit/parent_binding.json": _json(binding),
        "audit/semantic_family_proposals.json": _json(previous.semantic_family_audit(docs)),
        "audit/body_only_counterexamples.json": _json(previous.previous.previous.previous.body_only_counterexamples(details)),
        "README.md": "# 합성 조건부 참조 후보 누적196건\n\n"
            "기존164건을 보존하고 4분야×2말투×4사례의 독립 원고32건을 추가했다. 숫자·말투만 바꾼 사본이 아니다.\n\n"
            "본문 단독 등급 채점 가능0, 학습/평가 채택0이다. 최종200개 블라인드 평가용이 아니다. "
            "인접 등급 배제는 내부 정책과 가상 맥락 아래에만 성립한다. 문체 편향/의미 독립성은 인증하지 않는다.\n"}
    # Keep inherited calculation evidence, not only its cumulative count.
    for name, content in parent.items():
        if name.startswith("audit/") and "arithmetic" in name:
            payload["audit/inherited/"+name.removeprefix("audit/")] = content
    payload.update({f"documents/{d.input.doc_id}.txt": d.input.text for d in docs})
    return docs, answers, payload


def source_hashes():
    return {**previous.source_hashes(), **{p: hashlib.sha256((POC/p).read_bytes()).hexdigest() for p in SOURCES}}


def prepare(out, *, parent_pack=None, corpus_root=None, tokenizer=None):
    out = Path(out)
    require(not out.exists(), "batch05_output_exists")
    if parent_pack is not None:
        previous.verify(Path(parent_pack))
        require(hashlib.sha256((Path(parent_pack)/"manifest.json").read_bytes()).hexdigest() == PARENT_MANIFEST,
                "batch05_parent_pack_mismatch")
    docs, _, payload = core_payload()
    tokens = token_audit(docs, tokenizer)
    if tokenizer is not None:
        require(all(v["fits_512_tokens"] for v in tokens["views"]), "batch05_token_budget_exceeded")
    external = audit_external_pool(docs, Path(corpus_root).rglob("*.jsonl")) if corpus_root is not None else None
    if external is not None:
        require(bool(external["files"]) and external["text_rows_checked"] > 0, "batch05_external_scan_empty")
        require(not external["matches"], "batch05_external_duplicate_blocked")
    payload["audit/tokenizer.json"] = _json(tokens)
    payload["audit/external_pool.json"] = _json(external)
    out.mkdir(parents=True, exist_ok=False)
    for name, content in payload.items():
        _new_file(out/name, content)
    _new_file(out/"manifest.json", _json({**FLAGS, "schema_version": SCHEMA, "policy_sha256": POLICY_SHA256,
        "dataset_role": "synthetic_conditional_reference_candidate",
        "parent_pack_verified_during_build": parent_pack is not None,
        "source_files_sha256": source_hashes(), "files": {name: text_digest(content) for name, content in payload.items()}}))
    return verify(out)


def verify(root):
    root = Path(root).resolve()
    raw = (root/"manifest.json").read_bytes()
    manifest = strict_loads(raw.decode("utf-8"))
    require(manifest["schema_version"] == SCHEMA and manifest["policy_sha256"] == POLICY_SHA256 and
            manifest["dataset_role"] == "synthetic_conditional_reference_candidate", "batch05_manifest_invalid")
    require(all(type(manifest[k]) is bool and manifest[k] is False for k in FLAGS) and
            type(manifest["parent_pack_verified_during_build"]) is bool, "batch05_permissions_invalid")
    require(value_digest(manifest["source_files_sha256"]) == value_digest(source_hashes()), "batch05_source_drift")
    files = manifest["files"]
    require(type(files) is dict and bool(files), "batch05_manifest_empty")
    data = {}
    for name, digest in files.items():
        require(type(name) is str and bool(name), "batch05_path_invalid")
        path = (root/name).resolve()
        require(not Path(name).is_absolute() and "\\" not in name and path.is_relative_to(root) and path != root,
                "batch05_path_escape")
        data[name] = path.read_bytes()
        require(hashlib.sha256(data[name]).hexdigest() == digest, "batch05_payload_hash_mismatch")
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == set(files) | {"manifest.json"},
            "batch05_unlisted_file")
    docs, _, expected = core_payload()
    require(set(files) == set(expected) | {"audit/tokenizer.json", "audit/external_pool.json"}, "batch05_file_list_mismatch")
    for name, content in expected.items():
        require(data[name] == content.encode("utf-8"), "batch05_replay_mismatch")
    tokens = strict_loads(data["audit/tokenizer.json"].decode("utf-8"))
    if tokens["status"] == "measured":
        require(set(tokens) == {"status", "tokenizer_path", "tokenizer_sha256", "views",
                "semantic_evidence_coverage_verified", "model_inference_performed", "active_serving_model_verified"},
                "batch05_token_fields_invalid")
        require(type(tokens["tokenizer_path"]) is str and bool(tokens["tokenizer_path"]) and
                type(tokens["tokenizer_sha256"]) is str and re.fullmatch(r"[a-f0-9]{64}", tokens["tokenizer_sha256"])
                and all(tokens[k] is False for k in ("semantic_evidence_coverage_verified",
                        "model_inference_performed", "active_serving_model_verified")), "batch05_token_claim_invalid")
        require(len(tokens["views"]) == 2*len(docs), "batch05_token_view_count")
        for d in docs:
            for profile in ("body_only", "body_context"):
                matching = [v for v in tokens["views"] if v["doc_id"] == d.input.doc_id and v["profile"] == profile]
                require(len(matching) == 1 and matching[0]["presented_sha256"] == text_digest(presented_text(d, profile)),
                        "batch05_token_input_mismatch")
                n = matching[0]["tokens_with_special"]
                require(type(n) is int and 1 <= n <= 512 and matching[0]["fits_512_tokens"] is True,
                        "batch05_token_budget_exceeded")
    else:
        require(tokens == {"status": "not_run", "model_inference_performed": False}, "batch05_token_status_invalid")
    external = strict_loads(data["audit/external_pool.json"].decode("utf-8"))
    if external is not None:
        require(type(external) is dict and bool(external["files"]) and
                type(external["text_rows_checked"]) is int and external["text_rows_checked"] > 0,
                "batch05_external_scan_empty")
        require(not external["matches"], "batch05_external_duplicate_blocked")
    require((root/"manifest.json").read_bytes() == raw, "batch05_pack_changed")
    return strict_loads(data["summary.json"].decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--parent-pack", type=Path, required=True)
    build.add_argument("--corpus-root", type=Path)
    build.add_argument("--tokenizer", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (prepare(args.out, parent_pack=args.parent_pack, corpus_root=args.corpus_root, tokenizer=args.tokenizer)
                  if args.command == "prepare" else verify(args.pack))
        print(json.dumps({k: result[k] for k in ("status", "unique_bodies", "grade_counts")}, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "customer_guide_batch05_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
