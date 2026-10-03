"""Offline review packets, disagreement checks and non-authoritative resolutions.

No signatures, GOLD promotion, original relabeling, training or identity service.
Review results are supplied by users; the tool never fills them from AI answers.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import classification_pilot_intake as intake
import prepare_content_reference_revision as revision
from content_reference_contract import GRADES, RULE_GRADES, RULES, require, text_hash
from evaluation_inputs import read_rows, sha256
from prepare_content_reference import write_json, write_jsonl
from prepare_content_reference_review import record_sha

POC = Path(__file__).resolve().parents[1]
SOURCE = POC / "reports/CONTENT_REFERENCE_REVISION_20260915/material_v1_1"
SOURCE_SHA = "97727233dcd7bcd0a37de681508dfb092a211ac64203ba7988d78f19ac059012"
PROTOCOL = POC / "docs/CLASSIFICATION_REVIEW_AND_PILOT_PROTOCOL_V1.md"
SLOTS = ("R1", "R2")
REASONS = {"rule_interpretation", "evidence_insufficient", "scope_incomplete", "label_mistake", "context_conflict", "policy_gap"}
RULE_TO_GRADE = {RULES[k]: RULE_GRADES[k] for k in RULES}
NEXT_ACTION = {
    "awaiting_reviews": "배정된 각 검수자의 입력 우선 제출을 기다린다.",
    "independence_check_required": "담당 조정자가 계정/AI 보조/사전 열람 이력을 확인하고 필요하면 새 검수자를 배정한다.",
    "decision_disagreement": "조정자가 증거 부족·원문 범위·규칙 해석·라벨 오류를 분리하고 필요한 원문/정책 결정을 요청한다.",
    "basis_disagreement": "동일 결론을 지지한 규칙/사유의 차이를 확인하고 수용 근거를 조정 제안으로 기록한다.",
    "agreement_pending_adjudication": "근거·판본·정책을 재확인하고 신원/승인 검증을 거친다. 합의만으로 정답 확정하지 않는다.",
}
SAFETY = {"final_grade": None, "approval_status": "unapproved", "gold_eligible": False,
          "training_allowed": False, "identity_authenticated": False, "independence_verified": False}


def account_id(value) -> str:
    """Canonical account identifier syntax only, never proof of human identity."""
    require(isinstance(value, str), "Account UUID required")
    try:
        identifier = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Account UUID required") from exc
    require(identifier.int != 0, "Nil account is not an identity")
    return str(identifier)


def blank_result() -> dict:
    return {"status": None, "grade": None, "rule_ids": [], "evidence": [], "reason_codes": [],
            "rationale": None, "not_higher_reason": None, "not_lower_reason": None, "evidence_requests": []}


def submission_template(case: dict, slot: str) -> dict:
    return {"case_id": case["case_id"], "binding": case["binding"], "reviewer_slot": slot,
            "state": "pending", "reviewer_id": None, "actor_kind": None, "reviewed_at": None,
            "prior_answers_seen": None, "decision": blank_result()}


def packet_cases() -> list:
    require(sha256(SOURCE / "manifest.json") == SOURCE_SHA, "Source revision manifest changed")
    revision.check_saved_pack(SOURCE)
    rows = read_rows(SOURCE / "inputs.jsonl")
    return [{"case_id": "RV-" + text_hash(row["doc_id"] + SOURCE_SHA)[:16], "source_doc_id": row["doc_id"],
             "text": row["text"], "context": row["context"],
             "binding": {"input_sha256": text_hash(revision.model_input(row, revision.VIEWS[1])),
                         "text_sha256": row["text_sha256"], "context_sha256": row["context_sha256"],
                         "policy_version": revision.POLICY_ID, "policy_sha256": row["policy_sha256"],
                         "view": revision.VIEWS[1]}} for row in rows]


def decision_check(decision: dict, case: dict) -> None:
    require(isinstance(decision, dict) and set(decision) == set(blank_result()), "Invalid decision schema or authority field")
    status, grade, rules = (decision[k] for k in ("status", "grade", "rule_ids"))
    require(status in {"recommended", "needs_evidence", "needs_policy_review"}, "Invalid decision status")
    require(isinstance(rules, list) and bool(rules) and all(isinstance(r, str) for r in rules)
            and len(rules) == len(set(rules)), "Missing/duplicate rules")
    reasons = decision["reason_codes"]
    require(isinstance(reasons, list) and all(isinstance(r, str) and r in REASONS for r in reasons)
            and len(reasons) == len(set(reasons)), "Invalid reason codes")
    requests = decision["evidence_requests"]
    require(isinstance(requests, list) and all(intake.nonempty(r) for r in requests), "Invalid evidence requests")
    if status == "recommended":
        require(grade in GRADES and all(RULE_TO_GRADE.get(rule) == grade for rule in rules), "Grade/rule mismatch")
        require(not ({"context_conflict", "policy_gap", "evidence_insufficient", "scope_incomplete"} & set(reasons)),
                "Unresolved evidence or policy gap cannot be a recommendation")
    else:
        require(grade is None, "Review status requires null grade")
        require(rules == ["CP-HOLD-01" if status == "needs_evidence" else "CP-HOLD-02"], "Review rule mismatch")
        require(bool(requests), "Review needs a concrete evidence/decision request")
        require(bool(set(reasons) & ({"evidence_insufficient", "scope_incomplete"} if status == "needs_evidence" else
                                    {"context_conflict", "policy_gap"})), "Review reason missing")
    for key in ("rationale", "not_higher_reason", "not_lower_reason"):
        require(intake.nonempty(decision[key]), "Decision rationale/boundary missing")
    evidence = decision["evidence"]
    require(isinstance(evidence, list) and bool(evidence), "Decision evidence missing")
    for e in evidence:
        require(isinstance(e, dict), "Invalid evidence record")
        if e.get("kind") == "body":
            require(set(e) == {"kind", "start", "end", "quote"}, "Body evidence schema mismatch")
            require(type(e["start"]) is int and type(e["end"]) is int and
                    0 <= e["start"] < e["end"] <= len(case["text"]) and
                    case["text"][e["start"]:e["end"]] == e["quote"], "Invalid body evidence span")
        elif e.get("kind") == "context":
            require(set(e) == {"kind", "pointer", "value_sha256"}, "Context evidence schema mismatch")
            allowed = {f"/facts/{key}": value for key, value in case["context"]["facts"].items()}
            allowed |= {f"/{key}": case["context"][key] for key in ("as_of", "scope", "document_version")}
            require(e["pointer"] in allowed and e["value_sha256"] == record_sha(allowed[e["pointer"]]), "Invalid context evidence")
        else:
            raise ValueError("Evidence must be body or supplied context, not AI explanation")


def check_submissions(rows: list, cases: list, slot: str) -> dict:
    require(slot in SLOTS and len(rows) == len(cases), "Submission coverage mismatch")
    case_map = {case["case_id"]: case for case in cases}
    require(all(isinstance(r, dict) and isinstance(r.get("case_id"), str) for r in rows), "Missing submission case ID")
    by_id = {r["case_id"]: r for r in rows}
    require(len(by_id) == len(case_map) and by_id.keys() == case_map.keys(), "Duplicate or unassigned case")
    identities = set()
    for key, row in by_id.items():
        case = case_map[key]
        expected = submission_template(case, slot)
        require(set(row) == set(expected) and row["binding"] == expected["binding"] and row["reviewer_slot"] == slot,
                "Submission fields/input/policy/slot mismatch")
        if row["state"] == "pending":
            require(row == expected, "Pending template contains a hidden decision/identity")
            continue
        require(row["state"] == "submitted", "Invalid submission state")
        identities.add(account_id(row["reviewer_id"]))
        require(row["actor_kind"] in {"human_declared", "ai_assisted_declared"}, "Missing actor disclosure")
        require(intake.dated(row["reviewed_at"]) and type(row["prior_answers_seen"]) is bool, "Time/exposure declaration missing")
        require(datetime.fromisoformat(row["reviewed_at"]) <= datetime.now(timezone.utc), "Submission time is in future")
        decision_check(row["decision"], case)
    require(len(identities) <= 1, "One reviewer slot cannot silently mix accounts")
    return by_id


def compare(cases: list, left_rows: list, right_rows: list) -> tuple[dict, list]:
    left = check_submissions(left_rows, cases, "R1")
    right = check_submissions(right_rows, cases, "R2")
    result = []
    for case in cases:
        key = case["case_id"]
        a, b = left[key], right[key]
        completed = a["state"] == b["state"] == "submitted"
        clean = (completed and account_id(a["reviewer_id"]) != account_id(b["reviewer_id"])
                 and a["actor_kind"] == b["actor_kind"] == "human_declared"
                 and a["prior_answers_seen"] is b["prior_answers_seen"] is False)
        da, db = a["decision"], b["decision"]
        same = completed and (da["status"], da["grade"]) == (db["status"], db["grade"])
        basis = same and (set(da["rule_ids"]), set(da["reason_codes"])) == (set(db["rule_ids"]), set(db["reason_codes"]))
        status = ("awaiting_reviews" if not completed else "independence_check_required" if not clean else
                  "decision_disagreement" if not same else "basis_disagreement" if not basis else "agreement_pending_adjudication")
        evidence_diff = completed and {record_sha(e) for e in da["evidence"]} != {record_sha(e) for e in db["evidence"]}
        grades = {da["grade"], db["grade"]}
        result.append(SAFETY | {"case_id": key, "binding": case["binding"], "queue_status": status,
                      "next_action": NEXT_ACTION[status],
                      "submission_hashes": {"R1": record_sha(a), "R2": record_sha(b)},
                      "both_submitted": completed, "self_declared_independence_conditions_met": clean,
                      "decision_agreement": same if completed else None, "basis_agreement": basis if completed else None,
                      "evidence_locations_differ": evidence_diff if completed else None,
                      "high_vs_public_disagreement": completed and "S3" in grades and bool(grades & {"TS", "S1"}),
                      "submitted_decisions": {"R1": {k: da[k] for k in ("status", "grade", "rule_ids", "reason_codes")},
                                              "R2": {k: db[k] for k in ("status", "grade", "rule_ids", "reason_codes")}}})
    paired = sum(r["both_submitted"] for r in result)
    clean_n = sum(r["self_declared_independence_conditions_met"] for r in result)
    agreement = sum(r["self_declared_independence_conditions_met"] and r["decision_agreement"] for r in result)
    summary = {"assigned_cases": len(cases), "submitted_by_slot": {s: sum(r["state"] == "submitted" for r in source)
                for s, source in (("R1", left_rows), ("R2", right_rows))}, "paired_submissions": paired,
               "self_declared_independent_pairs": clean_n, "decision_agreement_count": agreement,
               "decision_agreement_rate": agreement / clean_n if clean_n else None,
               "queue_counts": dict(Counter(r["queue_status"] for r in result)),
               "high_vs_public_disagreements": sum(r["high_vs_public_disagreement"] for r in result),
               "identity_authenticated": False, "independence_verified": False,
               "model_accuracy_measured": False, "gold_eligible": False, "training_allowed": False}
    return summary, result


def resolution_template(row: dict) -> dict:
    return {"case_id": row["case_id"], "binding": row["binding"], "submission_hashes": row["submission_hashes"],
            "state": "pending", "adjudicator_id": None, "recorded_at": None, "cause_codes": [],
            "resolution_rationale": None, "decision": blank_result()}


def check_resolutions(rows: list, comparisons: list, cases: list, left: list, right: list) -> list:
    require(len(rows) == len(cases), "Resolution coverage mismatch")
    result, seen = [], set()
    case_map = {c["case_id"]: c for c in cases}
    compared = {r["case_id"]: r for r in comparisons}
    reviewers = [{r["case_id"]: r for r in group} for group in (left, right)]
    for row in rows:
        key = row["case_id"]
        require(key in compared and key not in seen, "Duplicate/unassigned resolution")
        seen.add(key)
        expected = resolution_template(compared[key])
        require(set(row) == set(expected) and all(row[k] == expected[k] for k in ("binding", "submission_hashes")),
                "Stale/unbound resolution")
        if row["state"] == "pending":
            require(row == expected, "Pending resolution contains a hidden proposal")
        else:
            require(row["state"] == "proposal_recorded" and compared[key]["both_submitted"], "Resolution requires both submissions")
            require(compared[key]["self_declared_independence_conditions_met"], "Independence issue must be resolved outside this tool")
            actor = account_id(row["adjudicator_id"])
            require(all(actor != account_id(group[key]["reviewer_id"]) for group in reviewers), "Adjudicator must differ from reviewers")
            require(intake.dated(row["recorded_at"]) and datetime.fromisoformat(row["recorded_at"]) <= datetime.now(timezone.utc),
                    "Invalid resolution timestamp")
            require(all(datetime.fromisoformat(row["recorded_at"]) >= datetime.fromisoformat(group[key]["reviewed_at"])
                        for group in reviewers), "Resolution predates submitted reviews")
            require(isinstance(row["cause_codes"], list) and bool(row["cause_codes"])
                    and all(r in REASONS | {"agreement_checked"} for r in row["cause_codes"]), "Resolution cause missing")
            if compared[key]["queue_status"] in {"decision_disagreement", "basis_disagreement"}:
                require(bool(set(row["cause_codes"]) & REASONS), "Disagreement cannot be resolved as agreement only")
            require(intake.nonempty(row["resolution_rationale"]), "Resolution justification missing")
            decision_check(row["decision"], case_map[key])
        result.append(SAFETY | {"case_id": key, "state": row["state"], "resolution_record_sha256": record_sha(row),
                               "proposal_grade": row["decision"]["grade"], "proposal_status": row["decision"]["status"]})
    return result


def freeze_manifest(out: Path, source_files: list[Path], extra: dict) -> None:
    write_json(out / "manifest.json", extra | {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_files": [{"path": str(p.resolve()), "sha256": sha256(p)} for p in source_files],
        "files": [{"path": p.relative_to(out).as_posix(), "sha256": sha256(p)} for p in sorted(out.rglob("*")) if p.is_file()]})


def prepare(out: Path) -> dict:
    out = out.resolve()
    require(not out.exists(), "Output exists; choose a new directory")
    for source in (SOURCE, revision.PACK, revision.REVIEW):
        require(not out.is_relative_to(source.resolve()), "Cannot write inside frozen source")
    cases = packet_cases()
    require(len(cases) == 40 and len({c["case_id"] for c in cases}) == 40, "Expected unique forty cases")
    out.mkdir(parents=True, exist_ok=False)
    forms = {}
    for slot in SLOTS:
        target = out / "reviewers" / slot
        target.mkdir(parents=True)
        inputs = [{k: v for k, v in c.items() if k != "source_doc_id"} for c in cases]
        forms[slot] = [submission_template(c, slot) for c in cases]
        write_jsonl(target / "inputs.jsonl", inputs)
        write_jsonl(target / "forms.template.jsonl", forms[slot])
        revision.write_text(target / "POLICY.md", revision.POLICY.read_text(encoding="utf-8"))
        revision.write_text(target / "README.md", "# 검수자 입력집\n\n"
                            "허구 세계의 보호 필요를 판단하는 미승인 기준 검토다. 실제 고객사 관리등급 시험이 아니다.\n\n"
                            "POLICY.md와 inputs.jsonl만 먼저 읽고 forms.template.jsonl을 패킷 밖 새 파일로 복사해 작성한다.\n"
                            "상대 답/AI 후보 답 사전 열람과 AI 보조 사용은 숨기지 않고 양식에 기록한다.\n"
                            "본문/맥락 근거 위치, 상하위 배제 이유, 보류 시 필요한 증거를 남긴다.\n"
                            "슬롯은 실제 신원이 아니다. 배정된 실제 계정 UUID는 제출 시 기록한다. 승인/서명/GOLD를 생성하지 않는다.\n")
    coordinator = out / "coordinator"
    coordinator.mkdir()
    write_jsonl(coordinator / "case_map.jsonl", [{"case_id": c["case_id"], "source_doc_id": c["source_doc_id"]} for c in cases])
    summary, comparisons = compare(cases, forms["R1"], forms["R2"])
    write_json(coordinator / "readiness.json", summary)
    write_jsonl(coordinator / "initial_queue.jsonl", comparisons)
    write_jsonl(coordinator / "resolution.template.jsonl", [resolution_template(r) for r in comparisons])
    write_json(coordinator / "roles.template.json", {
        "policy_owner": None, "source_owner": None, "R1": None, "R2": None, "adjudicator": None,
        "identity_verification_reference": None, "assignment_status": "unassigned"})
    pilot = out / "pilot"
    pilot.mkdir()
    write_json(pilot / "intake.template.json", intake.template())
    write_json(pilot / "intake.json", [])
    write_json(pilot / "exclusions.template.json", intake.exclusion_template())
    write_json(pilot / "readiness.json", intake.preflight([], intake.exclusion_template()))
    revision.write_text(out / "PROTOCOL.md", PROTOCOL.read_text(encoding="utf-8"))
    revision.write_text(out / "README.md", "# 검수·실제 문서 접수 준비\n\n"
                        "검수자별 입력/빈 양식40건씩 준비. 실제 사람 제출0건, 실제 문서 접수0건.\n\n"
                        "reviewers/R1, reviewers/R2만 해당 담당자에게 전달한다. 파일 분리는 ACL/블라인드 인증이 아니다.\n"
                        "템플릿을 수정하지 말고 패킷 밖 새 제출 파일을 만든다. PROTOCOL.md에 필드와 대조 절차가 있다.\n"
                        "조정 제안도 승인/GOLD/학습 허가가 아니다. 원본·후보·정책·운영 DB는 변경하지 않는다.\n")
    freeze_manifest(out, [Path(__file__), Path(intake.__file__), PROTOCOL, revision.POLICY,
                          SOURCE / "manifest.json", Path(revision.__file__)],
                    {"schema_version": "classification-review-packet-v1", "source_manifest_sha256": SOURCE_SHA,
                     "approval_status": "unapproved", "training_allowed": False})
    load_packet(out)
    return summary | {"real_document_intake": 0}


def load_packet(pack: Path) -> list:
    pack = pack.resolve()
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["schema_version"] == "classification-review-packet-v1" and manifest["source_manifest_sha256"] == SOURCE_SHA,
            "Unexpected review packet")
    require(manifest["approval_status"] == "unapproved" and manifest["training_allowed"] is False, "Packet authority drift")
    for item in manifest["source_files"]:
        path = Path(item["path"]).resolve()
        require(path.is_relative_to(POC.resolve()) and sha256(path) == item["sha256"], "Packet source drift")
    require(len({i["path"] for i in manifest["files"]}) == len(manifest["files"]), "Duplicate packet file")
    for item in manifest["files"]:
        path = (pack / item["path"]).resolve()
        require(path.is_relative_to(pack) and sha256(path) == item["sha256"], "Packet file drift")
    actual = {p.relative_to(pack).as_posix() for p in pack.rglob("*") if p.is_file() and p != pack / "manifest.json"}
    require(actual == {i["path"] for i in manifest["files"]}, "Unmanifested packet file")
    cases = packet_cases()
    safe = [{k: v for k, v in c.items() if k != "source_doc_id"} for c in cases]
    for slot in SLOTS:
        require(read_rows(pack / "reviewers" / slot / "inputs.jsonl") == safe, "Reviewer input drift or answer leakage")
        require(read_rows(pack / "reviewers" / slot / "forms.template.jsonl") == [submission_template(c, slot) for c in cases],
                "Templates are immutable; submit outside packet")
    return cases


def analyze(pack: Path, left_file: Path, right_file: Path, out: Path, resolutions: Path | None = None) -> dict:
    require(not out.exists(), "Output exists; choose a new directory")
    for protected in (pack, SOURCE, revision.PACK, revision.REVIEW):
        require(not out.resolve().is_relative_to(protected.resolve()), "Do not write inside frozen packet/source")
    cases = load_packet(pack)
    files = [left_file, right_file] + ([resolutions] if resolutions else [])
    hashes = {p: sha256(p) for p in files}
    left, right = read_rows(left_file), read_rows(right_file)
    summary, comparisons = compare(cases, left, right)
    resolution_rows = read_rows(resolutions) if resolutions else [resolution_template(r) for r in comparisons]
    assessed = check_resolutions(resolution_rows, comparisons, cases, left, right)
    summary |= {"resolution_proposals": sum(r["state"] == "proposal_recorded" for r in assessed), "finalized_grades": 0}
    require(all(sha256(p) == h for p, h in hashes.items()), "Submission changed during analysis")
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "summary.json", summary)
    write_jsonl(out / "comparison.jsonl", comparisons)
    write_jsonl(out / "resolution_template.jsonl", [resolution_template(r) for r in comparisons])
    write_jsonl(out / "resolution_assessment.jsonl", assessed)
    revision.write_text(out / "SUMMARY.md", "# 검수 대조 결과\n\n" +
                        "사람 신원·정답 진위·승인 권한은 확인하지 않았다. 합의/조정 제안도 GOLD가 아니다.\n\n```json\n" +
                        json.dumps(summary, ensure_ascii=False, indent=2) + "\n```\n")
    freeze_manifest(out, [Path(__file__), Path(intake.__file__), PROTOCOL, pack / "manifest.json", *files],
                    {"schema_version": "classification-review-analysis-v1", "approval_status": "unapproved", "training_allowed": False})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--out", required=True)
    run = sub.add_parser("compare")
    for name in ("pack", "left", "right", "out"):
        run.add_argument(f"--{name}", required=True)
    run.add_argument("--resolutions")
    args = parser.parse_args()
    result = prepare(Path(args.out)) if args.command == "prepare" else analyze(
        Path(args.pack), Path(args.left), Path(args.right), Path(args.out), Path(args.resolutions) if args.resolutions else None)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
