"""Append 48 manuscripts to the immutable 116; policy 0.1 remains unchanged.

No training release, customer GOLD promotion or serving change is implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

import build_customer_guide_batch03 as previous
from customer_guide_batch04 import CASES, build_new_cases
from measure_customer_guide_shortcuts import excess_warning, project_rows
from measure_ngram_shortcuts import measure
from prepare_customer_benchmark import _json, _jsonl, _new_file, _rows, token_audit
from koipa.customer_benchmark import FLAGS, GRADES, audit_external_pool, duplicate_audit, presented_text, strict_loads
from koipa.customer_guide_reference import POLICY, POLICY_SHA256, validate_reference
from koipa.policy_facts import require, text_digest, value_digest

PARENT_MANIFEST = "23936f6dcfb274f88eed0da23e9032cc3b703631876165313b819b177a84dfaf"
SCHEMA = "customer-guide-batch04-pack-v0.4"
SOURCES = ("scripts/customer_guide_batch04.py", "scripts/build_customer_guide_batch04.py")

# Content-based author proposals, not certified lineage or production split rules.
# Kept outside frozen document metadata and outside every model input view.
SEMANTIC_GROUPS = [
    {"name": "two-arm-field-trial", "reason": "대상군을 겹치지 않게 나누고 제안·교육 순서별 중간 결과를 비교하는 공통 설계",
     "keys": ["channel-interview", "stockout-panel", "course-effect",
              "offer-order", "trial-onboarding", "service-bundle", "renewal-routing"]},
    {"name": "union-of-two-lists", "reason": "두 목록의 공통 식별자를 한 번 제외하고 사건 수·횟수와 고유 개수를 구별하는 구조",
     "keys": ["translation-merge", "attendance-count", "shift-overlap", "delivery-dedup"]},
    {"name": "photo-identity-lineage", "reason": "원본 사진·이름·재촬영의 식별 대응을 보존하고 이름 변경을 새 촬영과 구별하는 업무",
     "keys": ["photo-naming", "photo-sequence"]},
    {"name": "common-space-relocation", "reason": "공용 공간·물품의 위치/수량을 대조하고 이동과 갱신·이용 가능 상태를 구별하는 업무",
     "keys": ["desk-allocation", "office-move", "pantry-stock", "common-locker", "common-board"]},
]


def semantic_family_audit(docs, proposals=None):
    proposals = SEMANTIC_GROUPS if proposals is None else proposals
    require(bool(proposals), "batch04_semantic_groups_empty")
    by_key = {d.family_id.removeprefix("family-"): d for d in docs}
    parents = {d.input.doc_id: d.input.doc_id for d in docs}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def join(a, b):
        a, b = root(a), root(b)
        parents[max(a, b)] = min(a, b)

    # Preserve all preexisting connected components; a proposal can only merge.
    original = duplicate_audit(docs)["groups"]
    for ids in original.values():
        for doc_id in ids[1:]:
            join(ids[0], doc_id)
    names, entries = set(), []
    for group in proposals:
        require(group["name"] not in names and bool(group["reason"]), "batch04_semantic_group_invalid")
        names.add(group["name"])
        keys = group["keys"]
        require(len(keys) >= 2 and len(set(keys)) == len(keys) and set(keys) <= set(by_key), "batch04_semantic_members_invalid")
        members = [{"key": key, "doc_id": by_key[key].input.doc_id,
                    "body_sha256": text_digest(by_key[key].input.text)} for key in keys]
        for member in members[1:]:
            join(members[0]["doc_id"], member["doc_id"])
        entries.append({"name": group["name"], "reason": group["reason"], "members": members})
    mapping = {doc_id: root(doc_id) for doc_id in sorted(parents)}
    return {**FLAGS, "status": "authored_grouping_sensitivity_proposal", "new_documents": 0,
            "proposals": entries, "doc_to_group": mapping, "original_components": len(original),
            "proposed_components": len(set(mapping.values())),
            "proposal_member_documents": len({m["doc_id"] for g in entries for m in g["members"]}),
            "frozen_document_metadata_changed": False, "split_rule_approved": False,
            "semantic_independence_certified": False,
            "scope": "Four selected reasoning/workflow patterns only; unlisted manuscripts may still be related."}


def arithmetic(docs, specs=None):
    specs = CASES if specs is None else specs
    require(bool(specs), "batch04_arithmetic_empty")
    by_key = {d.family_id.removeprefix("family-"): d for d in docs}
    rows = []
    for spec in specs:
        d = by_key[spec["key"]]
        match = re.search(spec["pattern"], d.input.text, re.S)
        require(match is not None, "batch04_arithmetic_source_missing")
        values = list(map(Decimal, match.groups()))
        args, expected = values[:-1], values[-1]
        op = spec["operation"]
        if op in ("difference", "subtract_rest"):
            actual = args[0] - sum(args[1:])
        elif op == "increase":
            actual = args[1] - args[0]
        elif op == "sum":
            actual = sum(args)
        elif op == "union":
            actual = args[0] + args[1] - args[2]
        elif op == "multiply_add":
            actual = args[0]*args[1] + args[2]
        elif op == "percent":
            require(args[0] != 0, "batch04_arithmetic_zero_denominator")
            actual = args[1] / args[0] * 100
        else:
            raise ValueError("batch04_unknown_operation")
        require(actual == expected, "batch04_arithmetic_mismatch")
        rows.append({"doc_id": d.input.doc_id, "body_sha256": text_digest(d.input.text), "operation": op,
                     "start": match.start(), "end": match.end(), "quote": match.group(),
                     "quote_sha256": text_digest(match.group()), "operands": [str(v) for v in args],
                     "calculated": str(actual), "stated": str(expected), "passed": True})
    return {"new_checks": rows, "new_passed": len(rows), "previous_passed": 63,
            "total_listed_checks": 63+len(rows), "all_prose_semantically_certified": False}


def format_audit(new):
    # Discourse markers are a narrow format check, not semantic certification.
    by_key = {r["key"]: r for r in CASES}
    patterns = {
        "memo": r"(?:검토|정리|준비|수정|확인|그림|비교|집계|변경|관찰|대조)[^\n]*:",
        "notice": r"적용|대상|변경 내용|체험 내용|전시 내용|활동 내용",
        "procedure": r"1\..+2\..+3\.",
    }
    for record, _, _ in new:
        form = by_key[record["family_id"].removeprefix("family-")]["form"]
        require(re.search(patterns[form], record["input"]["text"], re.S) is not None, "batch04_genre_marker_missing")
    old = previous.previous.build_new_cases()+previous.build_new_cases()
    old_specs = previous.previous.CASES+previous.CASES
    helper = previous.previous.form_audit
    cumulative = helper(old+new, specs=old_specs+CASES)
    return {"new_batch": helper(new, specs=CASES), "previous_annotated": helper(old, specs=old_specs),
            "annotated_cumulative": cumulative, "cumulative_unannotated_documents": 20,
            "new_genre_marker_checks": len(new), "template_independence_certified": False,
            "all_annotated_formats_span_four_grades": all(set(v) == set(GRADES) for v in cumulative["format_grade_counts"].values()),
            "controlled_before_after_comparison": False, "format_bias_resolved": False,
            "note": "Grade coverage across formats is not semantic independence. Population and class balance changed."}


def core_payload():
    old_docs, old_answers, parent = previous.core_payload()
    records = _rows(parent["authoring/documents.jsonl"].encode())
    details = _rows(parent["answers/evidence.jsonl"].encode())
    new = build_new_cases()
    require(len(new) == 48 and len(CASES) == 48, "batch04_source_count_invalid")
    records = sorted(records+[r for r, _, _ in new], key=lambda r: r["input"]["doc_id"])
    answers = sorted(old_answers+[a for _, a, _ in new], key=lambda a: a["doc_id"])
    details = sorted(details+[e for _, _, e in new], key=lambda e: e["doc_id"])
    docs = validate_reference(records, answers, details)
    require(len(docs) == 164, "batch04_total_count_invalid")
    dup = duplicate_audit(docs)
    require(not any(dup[k] for k in ("exact_pairs", "number_only_pairs", "near_pairs", "known_fixture_body_matches")), "batch04_duplicate_blocked")
    math, forms = arithmetic(docs), format_audit(new)
    semantic = semantic_family_audit(docs)
    counts = Counter(a["reference_grade"] for a in answers)
    summary = {**FLAGS, "status": "conditional_reference_candidates_not_released", "policy_sha256": POLICY_SHA256,
        "policy_version": POLICY["version"], "pack_version": "0.4", "unique_bodies": len(docs), "new_bodies": len(new),
        "unchanged_previous_bodies": len(old_docs), "internally_fixed_conditional_answers": len(answers),
        "grade_counts": {g: counts[g] for g in GRADES}, "target_documents": 1000,
        "remaining_before_rejections": {g: 250-counts[g] for g in GRADES}, "accepted_train": 0, "accepted_evaluation": 0,
        "body_only_grade_eligible": 0, "quote_bound_claims": sum(len(d.claims) for d in docs),
        "context_fact_bindings": 14*len(docs), "arithmetic_checks": math["total_listed_checks"],
        "new_domains": dict(Counter(r["domain"] for r, _, _ in new)), "model_accuracy": None,
        "customer_protocol_accepted": False, "reviewer_signatures_created": 0}
    by_detail = {e["doc_id"]: e for e in details}
    answer_lines = ["# 내부 조건부 참조 답안 164건", "",
                    "본문+가상 맥락, 내부 기준0.1 아래의 답안입니다. 고객사 GOLD/본문 단독 정답이 아닙니다.", "",
                    "| 문서 | S/V/M | 곱 | 답 |", "|---|---|---:|---|"]
    by_doc = {d.input.doc_id: d for d in docs}
    for d in docs:
        decision = by_detail[d.input.doc_id]["decision"]
        factors = "/".join(str(decision["factors"][k]) for k in ("S", "V", "M"))
        answer_lines.append(f"| {d.input.text.splitlines()[0]} | {factors} | {decision['product']} | {decision['reference_grade']} |")
    for a in answers:
        e, d = by_detail[a["doc_id"]], by_doc[a["doc_id"]]
        answer_lines.extend(["", "## "+d.input.text.splitlines()[0], "", "문서 ID: "+a["doc_id"], "", e["rationale"], "",
                             f"조건부 답: {a['reference_grade']}. 본문만 입력하면 등급 채점 제외.", "",
                             *[f"- {g} 배제: {reason}" for g, reason in a["other_grade_exclusions"].items()]])
    payload = {"policy.json": _json(POLICY), "summary.json": _json(summary), "authoring/documents.jsonl": _jsonl(records),
        "answers/answers.candidate.jsonl": _jsonl(answers), "answers/evidence.jsonl": _jsonl(details),
        "answers/REFERENCE_ANSWERS.md": "\n".join(answer_lines)+"\n",
        "inputs/body_context.jsonl": _jsonl([{**FLAGS, **d.input.model_dump(), "input_sha256": d.input_sha256} for d in docs]),
        "audit/duplicates.json": _json(dup), "audit/arithmetic.json": _json(math),
        "audit/previous_arithmetic.json": parent["audit/arithmetic.json"],
        "audit/batch02_arithmetic.json": parent["audit/previous_arithmetic.json"],
        "audit/original_arithmetic.json": parent["audit/original_arithmetic.json"],
        "audit/form_association.json": _json(forms), "audit/hold_probes.json": _json(previous.previous.hold_probes([e for _, _, e in new])),
        "audit/semantic_family_proposals.json": _json(semantic),
        "audit/formula.json": parent["audit/formula.json"],
        "audit/body_only_counterexamples.json": _json(previous.previous.previous.body_only_counterexamples(details)),
        "audit/parent_binding.json": _json({"expected_parent_manifest_sha256": PARENT_MANIFEST, "parent_documents": 116,
            "records_sha256": value_digest(_rows(parent["authoring/documents.jsonl"].encode())),
            "answers_sha256": value_digest(old_answers),
            "details_sha256": value_digest(_rows(parent["answers/evidence.jsonl"].encode())), "policy_changed": False}),
        "README.md": "# 합성 참조 후보 누적164건\n\n"+
            "기존116건을 보존하고 메모·안내·절차48건을 추가했다. 정답은 본문+명시된 가상 맥락과 내부 기준0.1 아래 조건부다.\n\n"
            "본문 단독 등급 채점 가능0, 학습/평가 채택0이다. 1,000건 완성/800·200 분리/고객사 GOLD가 아니다. "
            "산술 검산은 해당 문장의 계산만 확인하며 모든 내용의 현실 진위를 인증하지 않는다. 형식 편향은 해결 완료가 아니다.\n"}
    payload.update({f"documents/{d.input.doc_id}.txt": d.input.text for d in docs})
    return docs, answers, payload


def source_hashes():
    return {**previous.source_hashes(), **{p: hashlib.sha256((POC/p).read_bytes()).hexdigest() for p in SOURCES}}


def prepare(out, *, parent_pack=None, corpus_root=None, tokenizer=None):
    require(not out.exists(), "batch04_output_exists")
    if parent_pack is not None:
        previous.verify(parent_pack)
        require(hashlib.sha256((parent_pack/"manifest.json").read_bytes()).hexdigest() == PARENT_MANIFEST, "batch04_parent_pack_mismatch")
    docs, _, payload = core_payload()
    # Invalid tokenizer/oversized input fails before a potentially lengthy corpus scan.
    tokens = token_audit(docs, tokenizer)
    if tokenizer:
        require(all(v["fits_512_tokens"] for v in tokens["views"]), "batch04_token_budget_exceeded")
    external = audit_external_pool(docs, corpus_root.rglob("*.jsonl")) if corpus_root else None
    if external is not None:
        require(bool(external["files"]) and external["text_rows_checked"] > 0, "batch04_external_scan_empty")
        require(not external["matches"], "batch04_external_duplicate_blocked")
    payload["audit/external_pool.json"], payload["audit/tokenizer.json"] = _json(external), _json(tokens)
    out.mkdir(parents=True, exist_ok=False)
    for name, content in payload.items():
        _new_file(out/name, content)
    _new_file(out/"manifest.json", _json({**FLAGS, "schema_version": SCHEMA,
        "dataset_role": "synthetic_conditional_reference_candidate", "policy_sha256": POLICY_SHA256,
        "parent_pack_verified_during_build": parent_pack is not None, "source_files_sha256": source_hashes(),
        "files": {name: text_digest(content) for name, content in payload.items()}}))
    return verify(out)


def verify(root):
    root = root.resolve()
    raw = (root/"manifest.json").read_bytes()
    manifest = strict_loads(raw.decode("utf-8"))
    require(manifest["schema_version"] == SCHEMA and manifest["policy_sha256"] == POLICY_SHA256 and
            manifest["dataset_role"] == "synthetic_conditional_reference_candidate", "batch04_manifest_invalid")
    require(all(type(manifest[k]) is bool and manifest[k] is False for k in FLAGS) and
            type(manifest["parent_pack_verified_during_build"]) is bool, "batch04_permissions_invalid")
    require(value_digest(manifest["source_files_sha256"]) == value_digest(source_hashes()), "batch04_source_drift")
    files = manifest["files"]
    require(type(files) is dict and bool(files), "batch04_manifest_empty")
    data = {}
    for name, sha in files.items():
        path = (root/name).resolve()
        require(not Path(name).is_absolute() and "\\" not in name and path.is_relative_to(root) and path != root, "batch04_path_escape")
        data[name] = path.read_bytes()
        require(hashlib.sha256(data[name]).hexdigest() == sha, "batch04_payload_hash_mismatch")
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == set(files) | {"manifest.json"}, "batch04_unlisted_file")
    docs, _, expected = core_payload()
    require(set(files) == set(expected) | {"audit/tokenizer.json", "audit/external_pool.json"}, "batch04_file_list_mismatch")
    for name, content in expected.items():
        require(data[name] == content.encode("utf-8"), "batch04_replay_mismatch")
    tokens = strict_loads(data["audit/tokenizer.json"].decode("utf-8"))
    if tokens["status"] == "measured":
        require(len(tokens["views"]) == 2*len(docs), "batch04_token_view_count")
        for d in docs:
            for profile in ("body_only", "body_context"):
                views = [v for v in tokens["views"] if v["doc_id"] == d.input.doc_id and v["profile"] == profile]
                require(len(views) == 1 and views[0]["presented_sha256"] == text_digest(presented_text(d, profile)), "batch04_token_input_mismatch")
                n = views[0]["tokens_with_special"]
                require(type(n) is int and 1 <= n <= 512 and views[0]["fits_512_tokens"] is True, "batch04_token_budget_exceeded")
    else:
        require(tokens["status"] == "not_run", "batch04_token_status_invalid")
    external = strict_loads(data["audit/external_pool.json"].decode("utf-8"))
    if external is not None:
        require(not external["matches"], "batch04_external_duplicate_blocked")
    require((root/"manifest.json").read_bytes() == raw, "batch04_pack_changed")
    return strict_loads(data["summary.json"].decode("utf-8"))


def audit(root, out, *, seeds=5):
    root, out = root.resolve(), out.resolve()
    require(not out.exists() and not out.is_relative_to(root), "batch04_audit_output_invalid")
    require(type(seeds) is int and 1 <= seeds <= 100, "batch04_audit_seeds_invalid")
    verify(root)
    manifest_hash = hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest()
    records = _rows((root/"authoring/documents.jsonl").read_bytes())
    answers = _rows((root/"answers/answers.candidate.jsonl").read_bytes())
    details = _rows((root/"answers/evidence.jsonl").read_bytes())
    docs = validate_reference(records, answers, details)
    result = {**FLAGS, "status": "diagnostic_only_not_quality_certification", "n": len(docs), "profiles": {},
        "pack_manifest_sha256": manifest_hash, "source_sha256": source_hashes(),
        "body_only_reference_labels_identifiable": False,
        "warning": "Input association, not customer model accuracy. Context is a policy input; genre balance is not no-leakage proof."}
    for profile in ("body_only", "context_only", "body_context", "title_only"):
        rows = project_rows(docs, answers, "body_only" if profile == "title_only" else profile)
        if profile == "title_only":
            rows = [{**r, "text": r["text"].splitlines()[0]} for r in rows]
        observed = measure(rows, seeds=seeds)
        observed["presented_sha256"] = {r["doc_id"]: text_digest(r["text"]) for r in rows}
        observed["warning_excess_over_permutation"] = excess_warning(observed)
        result["profiles"][profile] = observed
    semantic = semantic_family_audit(docs)
    rows = project_rows(docs, answers, "body_only")
    grouped = [{**r, "family_id": semantic["doc_to_group"][r["doc_id"]]} for r in rows]
    sensitivity = measure(grouped, seeds=seeds)
    sensitivity["presented_sha256"] = {r["doc_id"]: text_digest(r["text"]) for r in grouped}
    sensitivity["grouping_sha256"] = value_digest(semantic)
    sensitivity["warning_excess_over_permutation"] = excess_warning(sensitivity)
    result["semantic_group_sensitivity"] = sensitivity
    verify(root)
    require(hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest() == manifest_hash, "batch04_audit_pack_changed")
    _new_file(out, _json(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--parent-pack", type=Path)
    build.add_argument("--corpus-root", type=Path)
    build.add_argument("--tokenizer", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--pack", type=Path, required=True)
    probe = commands.add_parser("audit")
    probe.add_argument("--pack", type=Path, required=True)
    probe.add_argument("--out", type=Path, required=True)
    probe.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.out, parent_pack=args.parent_pack, corpus_root=args.corpus_root, tokenizer=args.tokenizer)
        elif args.command == "verify":
            result = verify(args.pack)
        else:
            result = audit(args.pack, args.out, seeds=args.seeds)
        print(json.dumps({k: result[k] for k in ("status", "unique_bodies", "grade_counts", "n") if k in result}, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "customer_guide_batch04_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
