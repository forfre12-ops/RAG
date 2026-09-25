"""Diagnostic alias-removal views only; authoritative 260 parents stay unchanged."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC/"src"))

import build_customer_guide_batch06 as parent
from customer_guide_alias_repair_v1 import (
    PARENT_AUTHORING_SOURCE_SHA256, REVIEW_VERSION, REVIEWED_PARENTS, rebind_claims, transform,
)
from prepare_customer_benchmark import _json, _jsonl, _new_file, _rows, token_audit
from koipa.customer_benchmark import FLAGS, GRADES, duplicate_audit, presented_text, strict_loads
from koipa.customer_guide_reference import POLICY, POLICY_SHA256, validate_reference
from koipa.policy_facts import require, text_digest, value_digest

PARENT_MANIFEST = "7e8ea7552e272f5391f1d95113176f955f8828352d38f45cfc24b979eb3c8f95"
SCHEMA = "customer-guide-alias-repair-pack-v1"
SOURCES = ("scripts/customer_guide_alias_repair_v1.py", "scripts/build_customer_guide_alias_repair_v1.py")


def core_payload():
    require(hashlib.sha256((POC/"scripts/customer_guide_batch06.py").read_bytes()).hexdigest()
            == PARENT_AUTHORING_SOURCE_SHA256, "alias_parent_source_changed")
    old_docs, old_answers, parent_payload = parent.core_payload()
    old_records = _rows(parent_payload["authoring/documents.jsonl"].encode())
    old_details = _rows(parent_payload["answers/evidence.jsonl"].encode())
    answers_by_id = {r["doc_id"]: r for r in old_answers}
    details_by_id = {r["doc_id"]: r for r in old_details}
    panel = [r for r in old_records if r["family_id"] in REVIEWED_PARENTS]
    require(len(old_docs) == 260 and len(panel) == len(REVIEWED_PARENTS) == 64, "alias_parent_count_invalid")
    records, answers, details, lineage, proof = [], [], [], [], []
    for original in old_records:
        parent_id = original["input"]["doc_id"]
        original_answer, original_detail = answers_by_id[parent_id], details_by_id[parent_id]
        record, answer, detail = original, original_answer, original_detail
        if original["family_id"] in REVIEWED_PARENTS:
            review = REVIEWED_PARENTS[original["family_id"]]
            require(parent_id == review["parent_doc_id"], "alias_review_parent_id_mismatch")
            variant = transform(original["family_id"], original["input"]["text"])
            if variant["changed"]:
                record, answer, detail = copy.deepcopy(original), copy.deepcopy(original_answer), copy.deepcopy(original_detail)
                record["claims"] = rebind_claims(original["claims"], original["input"]["text"], variant)
                record["input"]["text"] = variant["text"]
                record["input"]["doc_id"] = "doc-"+value_digest({"text": variant["text"], "context": record["input"]["context"]})[:24]
                record["input_sha256"] = value_digest(record["input"])
                answer.update(doc_id=record["input"]["doc_id"], input_sha256=record["input_sha256"])
                detail.update(doc_id=record["input"]["doc_id"], input_sha256=record["input_sha256"],
                              body_sha256=variant["body_sha256"])
                # parent_draft_id is the unchanged original authored source, not a new draft claim.
            lineage.append({**FLAGS, "parent_doc_id": parent_id, "child_doc_id": record["input"]["doc_id"],
                "parent_input_sha256": original["input_sha256"], "child_input_sha256": record["input_sha256"],
                "parent_body_sha256": text_digest(original["input"]["text"]), "child_body_sha256": text_digest(record["input"]["text"]),
                "parent_answer_sha256": value_digest(original_answer), "child_answer_sha256": value_digest(answer),
                "parent_evidence_sha256": value_digest(original_detail), "child_evidence_sha256": value_digest(detail),
                "policy_sha256": POLICY_SHA256, "context_sha256": value_digest(original["input"]["context"]),
                "family_id": original["family_id"], "scenario_id": original["scenario_id"],
                "template_family_id": original["template_family_id"], "changed": variant["changed"],
                "repair_kind": "identifier_alias_repair", "new_document_count": 0, "edits": variant["edits"]})
            proof.append({"parent_doc_id": parent_id, "child_doc_id": record["input"]["doc_id"],
                "review_version": REVIEW_VERSION, "segments": variant["unchanged_segments"],
                "numeric_sequence_sha256": value_digest(re.findall(r"\d+(?:\.\d+)?", original["input"]["text"])),
                "title_sha256": text_digest(original["input"]["text"].splitlines()[0]),
                "whole_semantic_equivalence_certified": False})
        records.append(record)
        answers.append(answer)
        details.append(detail)
    records.sort(key=lambda r: r["input"]["doc_id"])
    answers.sort(key=lambda r: r["doc_id"])
    details.sort(key=lambda r: r["doc_id"])
    lineage.sort(key=lambda r: r["parent_doc_id"])
    docs = validate_reference(records, answers, details)
    require(len(docs) == len(answers) == 260 and len(lineage) == 64, "alias_active_count_invalid")
    require(sum(r["changed"] for r in lineage) == 16 and sum(len(r["edits"]) for r in lineage) == 18,
            "alias_review_coverage_changed")
    duplicates = duplicate_audit(docs)
    require(not any(duplicates[k] for k in ("exact_pairs", "number_only_pairs", "near_pairs", "known_fixture_body_matches")),
            "alias_active_duplicate_blocked")
    arithmetic = parent.arithmetic(docs)
    require(arithmetic["new_passed"] == 64, "alias_arithmetic_count_invalid")
    counts = Counter(a["reference_grade"] for a in answers)
    require(counts == Counter(a["reference_grade"] for a in old_answers), "alias_reference_counts_changed")
    summary = {**FLAGS, "status": "diagnostic_alias_views_not_adopted", "pack_version": "alias-repair-v1",
        "dataset_role": "diagnostic_view_only", "diagnostic_view_only": True,
        "target_binding_not_certified": True, "adoption_allowed": False, "authoritative_parent_replaced": False,
        "authoring_quality_status": "AUTHORING_QUALITY_HOLD_TARGET_BINDING", "source_hold_automatically_cleared": False,
        "policy_sha256": POLICY_SHA256, "policy_version": POLICY["version"], "unique_bodies": len(docs),
        "new_bodies": 0, "new_benchmark_documents": 0, "revised_parent_documents": 64,
        "changed_views": 16, "unchanged_panel_documents": 48, "outside_panel_preserved": 196,
        "unchanged_previous_bodies": 244, "rebound_diagnostic_ids": 16, "edited_alias_spans": 18,
        "internally_fixed_conditional_answers": len(answers), "grade_counts": {g: counts[g] for g in GRADES},
        "target_documents": 1000, "remaining_before_rejections": {g: 250-counts[g] for g in GRADES},
        "accepted_train": 0, "accepted_evaluation": 0, "body_only_grade_eligible": 0,
        "final_blind_evaluation_eligible_new": 0, "same_parent_fold_required": True,
        "quote_bound_claims": sum(len(d.claims) for d in docs), "context_fact_bindings": 14*len(docs),
        "repaired_panel_arithmetic_checks": 64, "arithmetic_checks": strict_loads(parent_payload["summary.json"])["arithmetic_checks"],
        "preserved_single_latin_unit_spans": sum(len(r["preserved_single_latin"]) for r in REVIEWED_PARENTS.values()),
        "whole_semantic_equivalence_certified": False, "semantic_independence_certified": False,
        "style_bias_resolved": False, "model_accuracy": None, "reviewer_signatures_created": 0}
    parent_binding = {"expected_parent_manifest_sha256": PARENT_MANIFEST, "parent_documents": len(old_docs),
        "parent_panel_ids": sorted(r["input"]["doc_id"] for r in panel),
        "records_sha256": value_digest(old_records), "answers_sha256": value_digest(old_answers),
        "details_sha256": value_digest(old_details), "policy_changed": False}
    comparison = ["# 원본64와 별칭 제거 진단뷰", "", "신규 문서0, 변경16·동일48. 저장된 조건부 답안·맥락·정책 필드는 유지한다.",
        "대상 식별값 제거에 따른 적용 범위 일반화 위험이 있어 의미 동등성·대상 결합을 인증하지 못했다.",
        "원본260개가 정본이며 이260개는 미채택 진단뷰다. 정본 교체·학습 채택·품질 합격이 아니다."]
    new_by_id = {r["input"]["doc_id"]: r for r in records}
    parent_by_id = {r["input"]["doc_id"]: r for r in panel}
    for row in lineage:
        source, target = parent_by_id[row["parent_doc_id"]], new_by_id[row["child_doc_id"]]
        comparison.extend(["", "## "+source["input"]["text"].splitlines()[0], "",
            "원본 ID: "+row["parent_doc_id"], "교정 ID: "+row["child_doc_id"],
            "변경 여부: "+str(row["changed"]), "", "### 원문", "", source["input"]["text"]])
        if row["changed"]:
            comparison.extend(["", "### 교정뷰", "", target["input"]["text"]])
    payload = {"summary.json": _json(summary), "policy.json": _json(POLICY),
        "authoring/documents.jsonl": _jsonl(records), "answers/answers.candidate.jsonl": _jsonl(answers),
        "answers/evidence.jsonl": _jsonl(details),
        "inputs/body_context.jsonl": _jsonl([{**FLAGS, **d.input.model_dump(), "input_sha256": d.input_sha256} for d in docs]),
        "parents/documents.jsonl": _jsonl(panel),
        "parents/answers.candidate.jsonl": _jsonl([answers_by_id[r["input"]["doc_id"]] for r in panel]),
        "parents/evidence.jsonl": _jsonl([details_by_id[r["input"]["doc_id"]] for r in panel]),
        "audit/lineage.jsonl": _jsonl(lineage), "audit/reviewed_aliases.json": _json(REVIEWED_PARENTS),
        "audit/unchanged_segments.jsonl": _jsonl(sorted(proof, key=lambda r: r["parent_doc_id"])),
        "audit/arithmetic.json": _json(arithmetic), "audit/duplicates.json": _json(duplicates),
        "audit/parent_binding.json": _json(parent_binding), "COMPARISON.md": "\n".join(comparison)+"\n",
        "README.md": "# 별칭 제거 진단뷰 전용\n\n원본260개가 정본이며 대체하지 않는다. "
            "64개 부모에서 변경16개·동일48개, 나머지196개를 합친260개는 diagnostic_view_only다. 신규문서0개다.\n\n"
            "기질 배치 등 대상 식별값 제거가 적용 범위를 넓힐 수 있어 target_binding_not_certified=true다. "
            "adoption_allowed=false, authoritative_parent_replaced=false를 유지한다. "
            "저장된 라벨/맥락 일치는 의미 동등성·정답 타당성 인증이 아니다. 학습/평가 허용·고객GOLD·품질합격이 아니다.\n"}
    payload.update({f"documents/{d.input.doc_id}.txt": d.input.text for d in docs})
    return docs, answers, payload


def source_hashes():
    return {**parent.source_hashes(), **{p: hashlib.sha256((POC/p).read_bytes()).hexdigest() for p in SOURCES}}


def _verify_parent(root):
    parent.verify(root)
    require(hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest() == PARENT_MANIFEST,
            "alias_parent_manifest_mismatch")


def _output_boundary(out, parent_pack, tokenizer):
    require(not out.exists(), "alias_output_exists")
    require(not any((p/"manifest.json").is_file() for p in out.parents), "alias_output_inside_frozen_pack")
    if parent_pack is not None:
        require(not out.is_relative_to(parent_pack), "alias_output_inside_parent")
    if tokenizer is not None:
        require(not out.is_relative_to(tokenizer.parent), "alias_output_inside_tokenizer_directory")


def prepare(out, *, parent_pack=None, tokenizer=None):
    out = Path(out).resolve()
    parent_pack = Path(parent_pack).resolve() if parent_pack is not None else None
    tokenizer = Path(tokenizer).resolve() if tokenizer is not None else None
    _output_boundary(out, parent_pack, tokenizer)
    sources_at_start = source_hashes()
    if parent_pack is not None:
        _verify_parent(parent_pack)
    docs, _, payload = core_payload()
    tokens = token_audit(docs, tokenizer)
    if tokenizer is not None:
        require(all(r["fits_512_tokens"] for r in tokens["views"]), "alias_token_budget_exceeded")
    payload["audit/tokenizer.json"] = _json(tokens)
    require(source_hashes() == sources_at_start, "alias_source_changed_during_build")
    _output_boundary(out, parent_pack, tokenizer)
    if parent_pack is not None:
        _verify_parent(parent_pack)
    out.mkdir(parents=True, exist_ok=False)
    for name, content in payload.items():
        _new_file(out/name, content)
    if parent_pack is not None:
        _verify_parent(parent_pack)
    require(source_hashes() == sources_at_start, "alias_source_changed_during_build")
    _new_file(out/"manifest.json", _json({**FLAGS, "schema_version": SCHEMA,
        "dataset_role": "diagnostic_alias_repair_views_only", "policy_sha256": POLICY_SHA256,
        "target_binding_not_certified": True, "adoption_allowed": False, "authoritative_parent_replaced": False,
        "parent_manifest_sha256": PARENT_MANIFEST, "parent_verified_during_build": parent_pack is not None,
        "source_files_sha256": sources_at_start, "files": {p: text_digest(c) for p, c in payload.items()}}))
    result = verify(out)
    if parent_pack is not None:
        _verify_parent(parent_pack)
    require(source_hashes() == sources_at_start, "alias_source_changed_during_build")
    return result


def verify(root):
    root = Path(root).resolve()
    raw = (root/"manifest.json").read_bytes()
    manifest = strict_loads(raw.decode("utf-8"))
    require(manifest["schema_version"] == SCHEMA and manifest["policy_sha256"] == POLICY_SHA256 and
            manifest["parent_manifest_sha256"] == PARENT_MANIFEST and
            manifest["dataset_role"] == "diagnostic_alias_repair_views_only" and
            manifest["target_binding_not_certified"] is True and manifest["adoption_allowed"] is False and
            manifest["authoritative_parent_replaced"] is False, "alias_manifest_invalid")
    require(all(type(manifest[k]) is bool and manifest[k] is False for k in FLAGS) and
            type(manifest["parent_verified_during_build"]) is bool, "alias_permission_invalid")
    require(manifest["source_files_sha256"] == source_hashes(), "alias_source_drift")
    files = manifest["files"]
    require(type(files) is dict and bool(files), "alias_manifest_empty")
    data = {}
    for name, digest in files.items():
        require(type(name) is str and bool(name), "alias_path_invalid")
        path = (root/name).resolve()
        require(not Path(name).is_absolute() and "\\" not in name and path.is_relative_to(root) and path != root,
                "alias_path_escape")
        data[name] = path.read_bytes()
        require(hashlib.sha256(data[name]).hexdigest() == digest, "alias_payload_hash_mismatch")
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == set(files)|{"manifest.json"},
            "alias_unlisted_file")
    docs, _, expected = core_payload()
    require(set(files) == set(expected)|{"audit/tokenizer.json"}, "alias_file_list_mismatch")
    for name, content in expected.items():
        require(data[name] == content.encode("utf-8"), "alias_replay_mismatch")
    tokens = strict_loads(data["audit/tokenizer.json"].decode("utf-8"))
    if tokens["status"] == "measured":
        require(set(tokens) == {"status", "tokenizer_path", "tokenizer_sha256", "views",
                "semantic_evidence_coverage_verified", "model_inference_performed", "active_serving_model_verified"},
                "alias_token_fields_invalid")
        require(type(tokens["tokenizer_path"]) is str and bool(tokens["tokenizer_path"]) and
                type(tokens["tokenizer_sha256"]) is str and re.fullmatch(r"[a-f0-9]{64}", tokens["tokenizer_sha256"])
                and all(tokens[k] is False for k in ("semantic_evidence_coverage_verified",
                    "model_inference_performed", "active_serving_model_verified")), "alias_token_claim_invalid")
        wanted = Counter((d.input.doc_id, profile, text_digest(presented_text(d, profile)))
                         for d in docs for profile in ("body_only", "body_context"))
        actual = Counter((r["doc_id"], r["profile"], r["presented_sha256"]) for r in tokens["views"])
        require(wanted == actual, "alias_token_input_mismatch")
        require(all(type(r["tokens_with_special"]) is int and 1 <= r["tokens_with_special"] <= 512
                    and r["fits_512_tokens"] is True for r in tokens["views"]), "alias_token_budget_exceeded")
    else:
        require(tokens == {"status": "not_run", "model_inference_performed": False}, "alias_token_status_invalid")
    require((root/"manifest.json").read_bytes() == raw, "alias_pack_changed")
    return strict_loads(data["summary.json"].decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--parent-pack", type=Path, required=True)
    build.add_argument("--tokenizer", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = (prepare(args.out, parent_pack=args.parent_pack, tokenizer=args.tokenizer)
                  if args.command == "prepare" else verify(args.pack))
        print(json.dumps({k: report[k] for k in ("status", "unique_bodies", "changed_views", "new_benchmark_documents")}, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "customer_guide_alias_repair_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
