"""Append sixty authored cases; preserve policy 0.1 and the original twenty.

The pack version changes, not the policy. No split, training or GOLD promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

import build_customer_guide_reference as previous
from customer_guide_batch02 import CASES, build_new_cases
from measure_customer_guide_shortcuts import excess_warning, project_rows
from measure_ngram_shortcuts import measure
from prepare_customer_benchmark import _json, _jsonl, _new_file, _rows, token_audit
from koipa.customer_benchmark import FLAGS, GRADES, audit_external_pool, duplicate_audit, presented_text, strict_loads
from koipa.customer_guide_reference import POLICY, POLICY_SHA256, decide, validate_reference
from koipa.policy_facts import require, text_digest, value_digest

SOURCES = ("scripts/customer_guide_batch02.py", "scripts/build_customer_guide_batch02.py",
           "scripts/measure_customer_guide_shortcuts.py", "scripts/measure_ngram_shortcuts.py")
PARENT_MANIFEST = "d1e8072450f854b6c069061b7d1976ad83fdbf177e3a6cecd3a31a6bed2eebca"

# Each pattern reads operands AND the stated result from the actual manuscript.
# These are narrow arithmetic assertions, not certification of all prose.
CHECKS = [
    ("sample-inventory", r"입고 (\d+)개에서 시험 사용 (\d+)개와 폐기 (\d+)개를 빼면 잔량은 (\d+)개", "subtract_rest"),
    ("cache-expiry", r"항목을 (\d+)초에 저장하고 유효 시간을 (\d+)초로 설정했다\. 만료 시각은 (\d+)초", "sum"),
    ("supplier-yield", r"입고 (\d+)kg 중 사용 가능량은 (\d+)kg이었다\. 공급원 가의 사용 가능 비율은 (\d+)%", "yield_percent"),
    ("freight-break", r"두 번으로 나누면 회당 (\d+)원이다\. 두 번 배송의 운송비 합계는 (\d+)원", "times_two"),
    ("margin-sensitivity", r"가는 (\d+)개에 개당 (\d+)원, 나는 (\d+)개에 개당 (\d+)원으로 놓았다\. 두 품목의 기여액 합계는 (\d+)원", "mul_sum"),
    ("cash-timing", r"선금은 (\d+)만원, 잔금은 (\d+)만원이다\..*?전체 용역대금은 (\d+)만원", "sum"),
    ("receiving-shortfall", r"요청 (\d+)개에 실물 (\d+)개가 도착해 부족 수량은 (\d+)개", "subtract_rest"),
    ("gauge-offset", r"기준 길이는 ([\d.]+)mm이고 표시값은 ([\d.]+)mm였다\. 표시 오차는 기준보다 ([\d.]+)mm", "reverse_difference"),
    ("downtime-window", r"총 중단 (\d+)분에서 대기 (\d+)분을 빼면 실제 작업은 (\d+)분", "subtract_rest"),
    ("seat-allocation", r"좌석 (\d+)개 중 진행자용 (\d+)개와 장비 확인용 (\d+)개를 먼저 비웠다\. 참가자에게 배정할 수 있는 좌석은 (\d+)개", "subtract_rest"),
    ("attendance-count", r"오전 (\d+)명, 오후 (\d+)명 중 (\d+)명은 두 시간 모두 참석했습니다\. 고유 참가자 수는 (\d+)명", "union_two"),
    ("service-load-map", r"A군은 (\d+)건에 건당 (\d+)분, B군은 (\d+)건에 건당 (\d+)분으로 놓았다\. 두 제품군의 처리 시간 합계는 (\d+)분", "mul_sum"),
    ("service-quote", r"회당 작업비 (\d+)원이며 이동비 (\d+)원은 전체 일정에 한 번만 더합니다\. 두 회 작업비와 이동비를 합한 금액은 (\d+)원", "two_plus_one"),
    ("milestone-slack", r"준비 (\d+)일, 측정 (\d+)일, 결과 정리 (\d+)일을 순차로 진행한다\. 기본 일정 합계는 (\d+)일", "sum"),
    ("import-reconcile", r"목록에는 (\d+)행이 있었다\. 형식 오류 (\d+)행과 중복 식별자 (\d+)행을 별도 목록으로 보냈다\. 두 제외 목록이 겹치지 않을 때 채택 행은 (\d+)행", "subtract_rest"),
    ("room-observation", r"시작 전 (\d+)도였고 40분 뒤에는 (\d+)도로 표시됐다\. 이 두 판독 사이에는 (\d+)도의 차이", "reverse_difference"),
]


def arithmetic(docs):
    by_key = {d.family_id.removeprefix("family-"): d for d in docs}
    rows = []
    for key, pattern, operation in CHECKS:
        d = by_key[key]
        found = re.search(pattern, d.input.text, re.S)
        require(found is not None, "batch02_arithmetic_source_missing")
        values = list(map(Decimal, found.groups()))
        args, expected = values[:-1], values[-1]
        if operation == "subtract_rest":
            actual = args[0] - sum(args[1:])
        elif operation == "sum":
            actual = sum(args)
        elif operation == "yield_percent":
            require(args[0] != 0, "batch02_arithmetic_zero_denominator")
            actual = args[1] / args[0] * 100
        elif operation == "times_two":
            actual = args[0] * 2
        elif operation == "mul_sum":
            actual = args[0]*args[1] + args[2]*args[3]
        elif operation == "reverse_difference":
            actual = args[1] - args[0]
        elif operation == "union_two":
            actual = args[0] + args[1] - args[2]
        elif operation == "two_plus_one":
            actual = args[0]*2 + args[1]
        else:
            raise ValueError("batch02_unknown_operation")
        require(actual == expected, "batch02_arithmetic_mismatch")
        quote = found.group()
        rows.append({"doc_id": d.input.doc_id, "body_sha256": text_digest(d.input.text), "operation": operation,
                     "start": found.start(), "end": found.end(), "quote": quote, "quote_sha256": text_digest(quote),
                     "operands": [str(v) for v in args], "calculated": str(actual), "stated": str(expected), "passed": True})
    return {"new_checks": rows, "new_passed": len(rows), "previous_passed": 11,
            "total_listed_checks": 11+len(rows), "all_prose_semantically_certified": False}


def form_audit(new_cases, specs=CASES):
    by_key = {r["key"]: r for r in specs}
    groups = defaultdict(Counter)
    for record, answer, _ in new_cases:
        key = record["family_id"].removeprefix("family-")
        groups[by_key[key]["form"]][answer["reference_grade"]] += 1
    n = len(new_cases)
    require(n > 0, "batch02_form_audit_empty")
    hit = sum(max(v.values()) for v in groups.values())
    grades = Counter(a["reference_grade"] for _, a, _ in new_cases)
    return {"n": n, "format_grade_counts": dict(groups), "in_sample_majority_hits": hit,
            "in_sample_format_majority": hit/n, "majority_baseline": max(grades.values())/n,
            "interpretation": "Author-supplied format metadata association on the same rows, NOT held-out CV or model accuracy.",
            "formats_not_template_lineage_certification": True}


def hold_probes(details):
    # Diagnostics are not additional documents or usable four-grade answers.
    f = dict(details[0]["premises"])
    specs = [({"cost_krw": 2000000, "person_hours": 60}, "value_outside_clear_anchors"),
             ({"cost_krw": None}, "required_evidence_unknown"),
             ({"investment_scope_exact": False}, "investment_not_attributable"),
             ({"access_enforced": None}, "required_evidence_unknown")]
    results = []
    for changes, reason in specs:
        case = {**f, **changes}
        result = decide(case)
        require(result["status"] == "HOLD" and reason in result["reasons"], "batch02_hold_probe_failed")
        results.append({"premises": case, "expected_reason": reason, "decision": result})
    return {**FLAGS, "dataset_role": "policy_fixture_diagnostic_only", "new_document_count": 0, "cases": results}


def core_payload():
    previous_docs, parent = previous.core_payload()
    records = _rows(parent["authoring/documents.jsonl"].encode())
    answers = _rows(parent["answers/answers.candidate.jsonl"].encode())
    details = _rows(parent["answers/evidence.jsonl"].encode())
    new = build_new_cases()
    require(len(new) == 60 and len({r["key"] for r in CASES}) == 60, "batch02_source_count_invalid")
    records += [r for r, _, _ in new]
    answers += [a for _, a, _ in new]
    details += [e for _, _, e in new]
    # No grade-block or source-order cue in exported inputs. IDs depend only on input.
    records.sort(key=lambda r: r["input"]["doc_id"])
    answers.sort(key=lambda a: a["doc_id"])
    details.sort(key=lambda e: e["doc_id"])
    docs = validate_reference(records, answers, details)
    require(len(docs) == 80, "batch02_total_count_invalid")
    dup = duplicate_audit(docs)
    require(not any(dup[k] for k in ("exact_pairs", "number_only_pairs", "near_pairs", "known_fixture_body_matches")), "batch02_duplicate_blocked")
    math = arithmetic(docs)
    counts = Counter(a["reference_grade"] for a in answers)
    summary = {**FLAGS, "status": "conditional_reference_candidates_not_released", "policy_sha256": POLICY_SHA256,
               "pack_version": "0.2", "policy_version": POLICY["version"], "unique_bodies": len(docs),
               "new_bodies": len(new), "unchanged_previous_bodies": len(previous_docs),
               "internally_fixed_conditional_answers": len(answers), "grade_counts": {g: counts[g] for g in GRADES},
               "remaining_before_rejections": {g: 250-counts[g] for g in GRADES}, "target_documents": 1000,
               "body_only_grade_eligible": 0, "accepted_train": 0, "accepted_evaluation": 0,
               "quote_bound_claims": sum(len(d.claims) for d in docs), "context_fact_bindings": 14*len(docs),
               "arithmetic_checks": math["total_listed_checks"], "new_domains": dict(Counter(r["domain"] for r, _, _ in new)),
               "model_accuracy": None, "customer_protocol_accepted": False, "reviewer_signatures_created": 0}
    form = form_audit(new)
    guide = ["# 누적 80건 내부 조건부 참조 답안", "", "가상 맥락과 내부 기준0.1에 한정한 답입니다. 고객사 골든200 동결이 아닙니다.", "",
             "| 문서 | S/V/M | 곱 | 답 |", "|---|---|---:|---|"]
    by_detail, by_answer = {e["doc_id"]: e for e in details}, {a["doc_id"]: a for a in answers}
    for d in docs:
        e = by_detail[d.input.doc_id]["decision"]
        factors = "/".join(str(e["factors"][k]) for k in ("S", "V", "M"))
        guide.append(f"| {d.input.text.splitlines()[0]} | {factors} | {e['product']} | {e['reference_grade']} |")
    for d in docs:
        e, a = by_detail[d.input.doc_id], by_answer[d.input.doc_id]
        guide.extend(["", "## " + d.input.text.splitlines()[0], "", "문서 ID: " + d.input.doc_id, "", e["rationale"], "",
                      f"답: {a['reference_grade']}. 본문만 입력하면 등급 채점 제외.", "",
                      *[f"- {g} 배제: {r}" for g, r in a["other_grade_exclusions"].items()]])
    payload = {"policy.json": _json(POLICY), "summary.json": _json(summary), "authoring/documents.jsonl": _jsonl(records),
               "answers/answers.candidate.jsonl": _jsonl(answers), "answers/evidence.jsonl": _jsonl(details),
               "answers/REFERENCE_ANSWERS.md": "\n".join(guide) + "\n",
               "inputs/body_context.jsonl": _jsonl([{**FLAGS, **d.input.model_dump(), "input_sha256": d.input_sha256} for d in docs]),
               "audit/duplicates.json": _json(dup), "audit/arithmetic.json": _json(math),
               "audit/previous_arithmetic.json": parent["audit/arithmetic.json"], "audit/form_association.json": _json(form),
               "audit/hold_probes.json": _json(hold_probes([e for _, _, e in new])),
               "audit/formula.json": parent["audit/formula.json"],
               "audit/body_only_counterexamples.json": _json(previous.body_only_counterexamples(details)),
               "audit/parent_binding.json": _json({"expected_parent_manifest_sha256": PARENT_MANIFEST,
                   "records_sha256": value_digest(_rows(parent["authoring/documents.jsonl"].encode())),
                   "answers_sha256": value_digest(_rows(parent["answers/answers.candidate.jsonl"].encode())),
                   "parent_documents": 20, "policy_changed": False}),
               "README.md": "# 합성 참조 후보 누적80건\n\n이전20건을 보존하고 새60건을 추가했습니다. 정답은 본문+명시된 가상 맥락 및 내부 기준0.1 아래 조건부로 고정했습니다.\n\n"
               "아직1,000건 완성/800학습·200평가 분리/고객사 GOLD 동결이 아닙니다. 본문 단독 모델 정확도를 주장하지 않습니다. "
               "원문 또는 관리 맥락 없는 실제 문서의 정답이 아닙니다. 보류 진단을 신규 문서 수에 넣지 않습니다.\n"}
    payload.update({f"documents/{d.input.doc_id}.txt": d.input.text for d in docs})
    return docs, answers, payload


def source_hashes():
    return {**previous.source_hashes(), **{p: hashlib.sha256((POC/p).read_bytes()).hexdigest() for p in SOURCES}}


def prepare(out, *, corpus_root=None, tokenizer=None, parent_pack=None):
    require(not out.exists(), "batch02_output_exists")
    if parent_pack is not None:
        previous.verify(parent_pack)
        require(hashlib.sha256((parent_pack/"manifest.json").read_bytes()).hexdigest() == PARENT_MANIFEST, "batch02_parent_pack_mismatch")
    docs, _, payload = core_payload()
    external = audit_external_pool(docs, corpus_root.rglob("*.jsonl")) if corpus_root else None
    tokens = token_audit(docs, tokenizer)
    if tokenizer:
        require(all(v["fits_512_tokens"] for v in tokens["views"]), "batch02_token_budget_exceeded")
    payload["audit/external_pool.json"] = _json(external)
    payload["audit/tokenizer.json"] = _json(tokens)
    sources = source_hashes()
    out.mkdir(parents=True, exist_ok=False)
    for name, content in payload.items():
        _new_file(out/name, content)
    _new_file(out/"manifest.json", _json({**FLAGS, "schema_version": "customer-guide-batch02-pack-v0.2",
        "dataset_role": "synthetic_conditional_reference_candidate", "policy_sha256": POLICY_SHA256,
        "parent_pack_verified_during_build": parent_pack is not None, "source_files_sha256": sources,
        "files": {p: text_digest(c) for p, c in payload.items()}}))
    return verify(out)


def verify(root):
    root = root.resolve()
    raw = (root/"manifest.json").read_bytes()
    manifest = strict_loads(raw.decode("utf-8"))
    require(manifest["schema_version"] == "customer-guide-batch02-pack-v0.2" and manifest["policy_sha256"] == POLICY_SHA256 and
            manifest["dataset_role"] == "synthetic_conditional_reference_candidate", "batch02_manifest_invalid")
    require(all(type(manifest[k]) is bool and manifest[k] is False for k in FLAGS) and
            type(manifest["parent_pack_verified_during_build"]) is bool, "batch02_permissions_invalid")
    require(value_digest(manifest["source_files_sha256"]) == value_digest(source_hashes()), "batch02_source_drift")
    files = manifest["files"]
    require(type(files) is dict and bool(files), "batch02_manifest_empty")
    data = {}
    for name, sha in files.items():
        path = (root/name).resolve()
        require(not Path(name).is_absolute() and "\\" not in name and path.is_relative_to(root) and path != root, "batch02_path_escape")
        data[name] = path.read_bytes()
        require(hashlib.sha256(data[name]).hexdigest() == sha, "batch02_payload_hash_mismatch")
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == set(files) | {"manifest.json"}, "batch02_unlisted_file")
    docs, _, expected = core_payload()
    require(set(files) == set(expected) | {"audit/external_pool.json", "audit/tokenizer.json"}, "batch02_file_list_mismatch")
    for name, content in expected.items():
        require(data[name] == content.encode("utf-8"), "batch02_replay_mismatch")
    tokens = strict_loads(data["audit/tokenizer.json"].decode("utf-8"))
    if tokens["status"] == "measured":
        require(len(tokens["views"]) == 2*len(docs), "batch02_token_view_count")
        for d in docs:
            for profile in ("body_only", "body_context"):
                matching = [v for v in tokens["views"] if v["doc_id"] == d.input.doc_id and v["profile"] == profile]
                require(len(matching) == 1 and matching[0]["presented_sha256"] == text_digest(presented_text(d, profile)), "batch02_token_input_mismatch")
    else:
        require(tokens["status"] == "not_run", "batch02_token_status_invalid")
    require((root/"manifest.json").read_bytes() == raw, "batch02_pack_changed")
    return strict_loads(data["summary.json"].decode("utf-8"))


def audit(root, out, *, seeds=5):
    root, out = root.resolve(), out.resolve()
    require(not out.exists() and not out.is_relative_to(root), "batch02_audit_output_invalid")
    require(type(seeds) is int and 1 <= seeds <= 100, "batch02_audit_seeds_invalid")
    verify(root)
    manifest_hash = hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest()
    docs = validate_reference(_rows((root/"authoring/documents.jsonl").read_bytes()),
        _rows((root/"answers/answers.candidate.jsonl").read_bytes()), _rows((root/"answers/evidence.jsonl").read_bytes()))
    answers = _rows((root/"answers/answers.candidate.jsonl").read_bytes())
    result = {**FLAGS, "status": "diagnostic_only_not_quality_certification", "n": len(docs),
              "pack_manifest_sha256": manifest_hash, "source_sha256": source_hashes(), "profiles": {},
              "body_only_reference_labels_identifiable": False,
              "warning": "Input-view association, not customer model accuracy; authored format association remains a separate concern."}
    for profile in ("body_only", "context_only", "body_context", "title_only"):
        rows = project_rows(docs, answers, "body_only" if profile == "title_only" else profile)
        if profile == "title_only":
            rows = [{**r, "text": r["text"].splitlines()[0]} for r in rows]
        observed = measure(rows, seeds=seeds)
        observed["presented_sha256"] = {r["doc_id"]: text_digest(r["text"]) for r in rows}
        observed["warning_excess_over_permutation"] = excess_warning(observed)
        result["profiles"][profile] = observed
    verify(root)
    require(hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest() == manifest_hash, "batch02_audit_pack_changed")
    _new_file(out, _json(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--corpus-root", type=Path)
    build.add_argument("--tokenizer", type=Path)
    build.add_argument("--parent-pack", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--pack", type=Path, required=True)
    probe = commands.add_parser("audit")
    probe.add_argument("--pack", type=Path, required=True)
    probe.add_argument("--out", type=Path, required=True)
    probe.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.out, corpus_root=args.corpus_root, tokenizer=args.tokenizer, parent_pack=args.parent_pack)
        elif args.command == "verify":
            result = verify(args.pack)
        else:
            result = audit(args.pack, args.out, seeds=args.seeds)
        print(json.dumps({k: result[k] for k in ("status", "unique_bodies", "grade_counts", "n") if k in result}, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "customer_guide_batch02_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
