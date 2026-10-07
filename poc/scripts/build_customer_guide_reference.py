"""Build/verify a separate guide-based conditional reference; no model training."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from customer_guide_cases import build_reference
from prepare_customer_benchmark import _json, _jsonl, _new_file, _rows, token_audit
from koipa.customer_benchmark import FLAGS, GRADES, audit_external_pool, duplicate_audit, strict_loads
from koipa.customer_guide_reference import POLICY, POLICY_SHA256, decide, formula_audit, validate_reference
from koipa.policy_facts import require, text_digest, value_digest

SOURCES = ("src/koipa/customer_guide_reference.py", "scripts/customer_guide_cases.py",
           "scripts/build_customer_guide_reference.py", "docs/CUSTOMER_GUIDE_REFERENCE_V0_1.md",
           "src/koipa/customer_benchmark.py", "scripts/customer_benchmark_drafts.py",
           "scripts/prepare_customer_benchmark.py", "src/koipa/policy_facts.py",
           "src/koipa/dataset_usage.py", "src/koipa/policy_fixture_registry.json")


def arithmetic_checks(docs):
    """Narrow source-bound arithmetic checks, not arbitrary claim certification."""
    by_name = {d.family_id.removeprefix("family-"): d for d in docs}
    checks = []

    def check(key, description, expression):
        d = by_name[key]
        evidence, actual, expected = expression(d.input.text)
        require(actual == expected, "guide_body_arithmetic_mismatch")
        checks.append({"doc_id": d.input.doc_id, "body_sha256": text_digest(d.input.text),
                       "check": description, "parsed_source_values": evidence,
                       "computed": actual, "stated": expected, "passed": True})

    def numbers(s):
        return [Decimal(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*(?:\.\d+)?", s)]

    def match(pattern, text):
        result = re.search(pattern, text)
        require(result is not None, "guide_body_arithmetic_pattern_missing")
        return result

    def control(t):
        base = numbers(match(r"기준 간격을 (\d+)mm", t)[1])[0]
        offset, gain = numbers(match(r"속도 명령은 ([\d.+e]+)로", t)[1])
        lo, hi = numbers(match(r"명령이 (\d+)보다 작으면 \d+, (\d+)보다 크면", t)[0])[::2]
        raw = numbers(match(r"간격 100mm에서는.*?나와야 한다", t)[0])
        actual = [str(max(lo, min(hi, offset + gain * (x - base))).normalize()) for x in raw[::2]]
        return [str(x) for x in raw], actual, [str(x.normalize()) for x in raw[1::2]]
    check("control-spec", "control law on the three stated test vectors", control)

    def means(t):
        rows = [numbers(match(name + r": ([\d, ]+)", t)[1]) for name in ("낮음", "보통", "높음")]
        stated = numbers(match(r"산술평균은 (.*?)다\.", t)[1])
        return [[str(x) for x in r] for r in rows], [str((sum(r) / len(r)).normalize()) for r in rows], [str(x.normalize()) for x in stated]
    check("sensor-observation", "arithmetic means of observed triples", means)

    def mixture(t):
        target = numbers(match(r"목표 질량은 ([\d,]+)g", t)[1])[0]
        values = numbers(match(r"물 ([\d,]+)g, 글리세린 ([\d,]+)g, 수용성 색소 ([\d,]+)g", t)[0])
        return [str(x) for x in values], str(sum(values)), str(target)
    check("mixture-record", "component mass sum", mixture)

    def quotes(t):
        qty = numbers(match(r"상자 (\d+)개의 견적", t)[1])[0]
        a, shipping = numbers(match(r"한빛상자는 개당 ([\d,]+)원에 운송비 ([\d,]+)원", t)[0])
        b = numbers(match(r"바른용기는 개당 ([\d,]+)원", t)[1])[0]
        targets = numbers(match(r"한빛상자의 합계는 (.*?)이다\.", t)[1])
        return [str(x) for x in (qty, a, shipping, b)], [str(qty*a+shipping), str(qty*b)], [str(x) for x in targets]
    check("supplier-terms", "landed totals at the requested quantity", quotes)

    def discount(t):
        total = numbers(match(r"기준 합계는 ([\d,]+)원", t)[1])[0]
        pct, result = numbers(match(r"합계에서 (\d+)%를 차감해 ([\d,]+)원", t)[0])
        return [str(total), str(pct)], str((total*(100-pct)/100).normalize()), str(result.normalize())
    check("renewal-pricing", "conditional prepayment discount (not payment selection)", discount)

    def heat(t):
        durations = [Decimal(x) for x in re.findall(r"도에서 (\d+)분 유지", t)]
        total = Decimal(match(r"유지 구간의 합은 (\d+)분", t)[1])
        return [str(x) for x in durations], str(sum(durations)), str(total)
    check("thermal-window", "holding duration excludes ramps", heat)

    def residuals(t):
        values = numbers(match(r"잔차는 (.*?)mm였다", t)[1])
        mean = numbers(match(r"평균은 (.*?)mm", t)[1])[0]
        return [str(x) for x in values], str(sum(values)/len(values)), str(mean)
    check("alignment-fit", "signed residual mean", residuals)

    def capacity(t):
        available = numbers(match(r"가용 시간은 ([\d,]+)시간", t)[1])[0]
        allocated = numbers(match(r"주문에 (\d+)시간, 시험 주문에 (\d+)시간", t)[0])
        rest = numbers(match(r"잔여 (\d+)시간", t)[1])[0]
        return [str(available), *[str(x) for x in allocated]], str(available-sum(allocated)), str(rest)
    check("capacity-allocation", "remaining capacity", capacity)

    def payment(t):
        old = numbers(match(r"계약 시 (.*?)를 지급하면", t)[1])
        new = numbers(match(r"제안안은 (.*?)로 한다", t)[1])
        return [[str(x) for x in old], [str(x) for x in new]], [str(sum(old)), str(sum(new))], ["100", "100"]
    check("financing-window", "both payment schedules sum to 100 percent", payment)

    def energy(t):
        minutes = Decimal(match(r"각각 (\d+)분 운전", t)[1])
        consumed = numbers(match(r"적산 전력은 순서대로 (.*?)였다", t)[1])
        power = numbers(match(r"평균 전력은 각각 (.*?)로 계산", t)[1])
        return [str(minutes), *[str(x) for x in consumed]], [str(x*60/minutes) for x in consumed], [str(x) for x in power]
    check("energy-load", "energy to average power using elapsed time", energy)

    def dispatch(t):
        a, b, c = numbers(match(r"차량 A는 (.*?)의 상차 시간이", t)[1])
        arrival = Decimal(match(r"시작 후 (\d+)분으로 늦어", t)[1])
        stated = Decimal(match(r"전체 완료 시점은 (\d+)분", t)[1])
        return [str(x) for x in (a, b, c, arrival)], str(max(b, max(a, arrival)+c)), str(stated)
    check("dispatch-planner", "delayed arrival changes makespan, not processing duration", dispatch)
    require(len(checks) == 11, "guide_arithmetic_coverage_changed")
    return {"checks": checks, "passed": len(checks), "scope": "listed arithmetic only; not all prose semantics or truth of fictional metadata"}


def body_only_counterexamples(details):
    probes = []
    for d in details:
        f = copy.deepcopy(d["premises"])
        original = d["decision"]["reference_grade"]
        if original in {"TS", "S1"}:
            f.update(all_staff_knows=True, business_need_only=False, individual_approval=False, access_enforced=False)
        else:
            f.update(public_exact_body=False, obtainable_without_holder=False, ordinary_access_difficult=True,
                     secrecy_manageable=True, all_staff_knows=False, business_need_only=True,
                     individual_approval=True, access_enforced=True, release_authorized=False)
        alternative = decide(f)
        require(alternative["reference_grade"] is not None and alternative["reference_grade"] != original, "guide_counterexample_failed")
        probes.append({"doc_id": d["doc_id"], "same_body_sha256": d["body_sha256"], "original_grade": original,
                       "alternative_grade": alternative["reference_grade"], "alternative_premises": f})
    return {"probes": probes, "changed_grade_same_body": len(probes), "new_documents": 0,
            "dataset_role": "policy_fixture_diagnostic_only", "body_only_grade_scoring_allowed": False}


def core_payload():
    records, answers, details = build_reference()
    docs = validate_reference(records, answers, details)
    duplicate = duplicate_audit(docs)
    require(not any(duplicate[k] for k in ("exact_pairs", "number_only_pairs", "near_pairs", "known_fixture_body_matches")), "guide_duplicate_blocked")
    arithmetic = arithmetic_checks(docs)
    counts = dict(Counter(a["reference_grade"] for a in answers))
    summary = {**FLAGS, "status": "internal_conditional_reference_not_customer_gold", "unique_bodies": len(docs),
               "new_bodies_this_batch": 8, "reused_prior_manuscripts": 12, "internally_fixed_conditional_answers": len(answers),
               "grade_counts": {g: counts.get(g, 0) for g in GRADES}, "target_total": 1000,
               "remaining_before_quality_rejections": {g: 250-counts.get(g, 0) for g in GRADES},
               "quote_bound_claims": sum(len(d.claims) for d in docs), "arithmetic_checks_passed": arithmetic["passed"],
               "formula_combinations_checked": 27, "context_fact_bindings": sum(len(d["context_evidence"]) for d in details),
               "body_only_eligible_grade_answers": 0, "customer_protocol_accepted": False,
               "accepted_training_documents": 0, "accepted_evaluation_documents": 0, "classification_accuracy": None,
               "policy_sha256": POLICY_SHA256, "facts_origin": "stipulated_fiction_not_verified_customer_data"}
    table = ["# 내부 조건부 참조 답안", "", "가상 조건과 내부 V 앵커 아래의 정답입니다. 고객사 골든 서명이 아닙니다.", "",
             "| 문서 | S | V | M | 곱 | 답 | 공개허가(가정) |", "|---|---:|---:|---:|---:|---|---|"]
    for doc, detail in zip(docs, details, strict=True):
        r = detail["decision"]
        table.append(f"| {doc.input.text.splitlines()[0]} | {r['factors']['S']} | {r['factors']['V']} | {r['factors']['M']} | {r['product']} | {r['reference_grade']} | {'있음' if r['release_authorized_in_scenario'] else '없음'} |")
    table.extend(["", "## 사례별 판단 근거", ""])
    for doc, detail in zip(docs, details, strict=True):
        answer = next(a for a in answers if a["doc_id"] == doc.input.doc_id)
        table.extend(["### " + doc.input.text.splitlines()[0], "", "문서 ID: " + doc.input.doc_id, "", detail["rationale"], "",
                      f"가상 사실과 정책 계산 결과: {detail['decision']['reference_grade']}. 본문만 제공하면 HOLD.", "",
                      *[f"- {grade} 배제: {reason}" for grade, reason in answer["other_grade_exclusions"].items()], ""])
    payload = {"policy.json": _json(POLICY), "summary.json": _json(summary), "authoring/documents.jsonl": _jsonl(records),
               "inputs/body_context.jsonl": _jsonl([{**FLAGS, **d.input.model_dump(), "input_sha256": d.input_sha256} for d in docs]),
               "answers/answers.candidate.jsonl": _jsonl(answers), "answers/evidence.jsonl": _jsonl(details),
               "answers/REFERENCE_ANSWERS.md": "\n".join(table) + "\n", "audit/duplicates.json": _json(duplicate),
               "audit/arithmetic.json": _json(arithmetic), "audit/formula.json": _json(formula_audit()),
               "audit/body_only_counterexamples.json": _json(body_only_counterexamples(details)),
               "README.md": "# 원 가이드 기반 합성 참조 0.1\n\n본문 20건(이전 12+신규 8)과 가상 맥락, 조건부 고정 답 20건입니다.\n\n"
               "목표는 학습 800/평가 200이지만 아직 배포/분할하지 않았습니다. 평가 결과나 실제 고객 등급이 아닙니다. "
               "본문만으로는 20건 모두 이 답을 강요할 수 없습니다. S3는 외부 공개 허가가 아닙니다.\n\n"
               "정답과 S/V/M은 answers에만 있습니다. 내부 V 앵커는 원 가이드의 수치가 아닙니다. "
               "기존 140건 및 원장/짧은 문서 fixture를 편입하지 않았습니다.\n"}
    payload.update({f"documents/{d.input.doc_id}.txt": d.input.text for d in docs})
    return docs, payload


def source_hashes():
    guide = POC.parent / POLICY["source"]["path"]
    require(hashlib.sha256(guide.read_bytes()).hexdigest() == POLICY["source"]["sha256"], "guide_original_source_changed")
    return {name: hashlib.sha256((POC/name).read_bytes()).hexdigest() for name in SOURCES}


def prepare(out, corpus_root=None, tokenizer=None):
    require(not out.exists(), "guide_output_exists")
    docs, payload = core_payload()
    sources = source_hashes()
    external = audit_external_pool(docs, corpus_root.rglob("*.jsonl")) if corpus_root else None
    tokens = token_audit(docs, tokenizer)
    payload["audit/external_pool.json"] = _json(external)
    payload["audit/tokenizer.json"] = _json(tokens)
    out.mkdir(parents=True, exist_ok=False)
    for name, text in payload.items():
        _new_file(out/name, text)
    _new_file(out/"manifest.json", _json({**FLAGS, "schema_version": "customer-guide-reference-pack-v0.1",
        "dataset_role": "synthetic_conditional_reference_candidate", "policy_sha256": POLICY_SHA256,
        "source_files_sha256": sources, "files": {name: text_digest(text) for name, text in payload.items()}}))
    return verify(out)


def verify(root):
    root = root.resolve()
    raw = (root/"manifest.json").read_bytes()
    m = strict_loads(raw.decode("utf-8"))
    require(m["schema_version"] == "customer-guide-reference-pack-v0.1" and m["policy_sha256"] == POLICY_SHA256 and
            m["dataset_role"] == "synthetic_conditional_reference_candidate", "guide_manifest_invalid")
    require(all(type(m[k]) is bool and m[k] is False for k in FLAGS), "guide_manifest_permission_invalid")
    require(value_digest(m["source_files_sha256"]) == value_digest(source_hashes()), "guide_source_drift")
    files = m["files"]
    require(type(files) is dict and bool(files), "guide_manifest_empty")
    data = {}
    for name, sha in files.items():
        path = (root/name).resolve()
        require(not Path(name).is_absolute() and "\\" not in name and path.is_relative_to(root) and path != root, "guide_path_escape")
        content = path.read_bytes()
        require(hashlib.sha256(content).hexdigest() == sha, "guide_file_hash_mismatch")
        data[name] = content
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == set(files) | {"manifest.json"}, "guide_unlisted_file")
    _, replay = core_payload()
    require(set(files) == set(replay) | {"audit/external_pool.json", "audit/tokenizer.json"}, "guide_file_list_mismatch")
    for name, expected in replay.items():
        require(data[name] == expected.encode("utf-8"), "guide_replay_mismatch")
    docs = validate_reference(_rows(data["authoring/documents.jsonl"]), _rows(data["answers/answers.candidate.jsonl"]), _rows(data["answers/evidence.jsonl"]))
    # Optional measurements are input-bound historical observations. Verification
    # does not open arbitrary filesystem paths from untrusted report contents.
    tokens = strict_loads(data["audit/tokenizer.json"].decode("utf-8"))
    if tokens["status"] == "measured":
        from koipa.customer_benchmark import presented_text
        require(len(tokens["views"]) == 2*len(docs), "guide_token_view_count")
        for doc in docs:
            for profile in ("body_only", "body_context"):
                matched = [v for v in tokens["views"] if v["doc_id"] == doc.input.doc_id and v["profile"] == profile]
                require(len(matched) == 1 and matched[0]["presented_sha256"] == text_digest(presented_text(doc, profile)), "guide_token_input_mismatch")
    else:
        require(tokens["status"] == "not_run", "guide_token_status_invalid")
    require((root/"manifest.json").read_bytes() == raw, "guide_pack_changed_during_read")
    return strict_loads(data["summary.json"].decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare")
    build.add_argument("--out", required=True, type=Path)
    build.add_argument("--corpus-root", type=Path)
    build.add_argument("--tokenizer", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--pack", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.out, args.corpus_root, args.tokenizer) if args.command == "prepare" else verify(args.pack)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "guide_reference_command_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
