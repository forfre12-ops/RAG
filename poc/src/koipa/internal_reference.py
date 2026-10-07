"""Proof-carrying reference pilot for a CLOSED, fictional ledger language only.

No general security-grade oracle, customer policy approval, S/V/M score, signature,
model training or evaluation permission is implemented here.
"""
from __future__ import annotations

import copy
import re
from itertools import product
from typing import Literal

from pydantic import ValidationError

from koipa.internal_reference_oracle import sql_oracle
from koipa.policy_facts import ContractModel, Digest, NonEmpty, require, text_digest, value_digest, FactContractError

POLICY = {
    "policy_id": "internal-fixed-ledger-reference", "version": "0.1",
    "scope": "fictional_closed_ledger_only", "bulk_person_threshold": 1000,
    "rules": [
        {"id": "IR-TS-01", "grade": "TS", "condition": "linked_unique_people >= bulk_person_threshold"},
        {"id": "IR-S1-01", "grade": "S1", "condition": "0 < linked_unique_people < bulk_person_threshold"},
        {"id": "IR-S2-01", "grade": "S2", "condition": "linked_unique_people == 0 and release_authorized == false"},
        {"id": "IR-S3-01", "grade": "S3", "condition": "linked_unique_people == 0 and release_authorized == true"},
    ],
    "authority": "internal_controlled_reference_only", "customer_policy_approved": False,
    "identity_semantics": "Only joins through the supplied complete directory identify people in this fictional world",
    "exclusions": ["real_people", "other_identity_links", "other_content_types", "credentials", "core_reproduction_claims"],
}
POLICY_SHA256 = value_digest(POLICY)
GRADES = ("TS", "S1", "S2", "S3")
SECTIONS = {
    "[연결표]": ("연결키", "인물ID", "표시명"),
    "[지급표]": ("처리키", "연결키", "금액단위"),
    "[배정표]": ("작업키", "설비", "시간단위"),
}
TITLES = ("업무 묶음", "월간 자료", "처리 기록", "정산 부속", "업무 참조", "자료 목록", "현황 묶음", "작업 부속", "운영 기록", "자료 집계")
KEY = re.compile(r"[KPTW]-[0-9a-f]{16}\Z")
MAX_ROWS = 5000
FLAGS = {"dataset_role": "policy_fixture", "reference_authority": "internal_controlled_reference_only",
         "training_allowed": False, "model_evaluation_allowed": False, "gold_eligible": False,
         "customer_accuracy_measured": False, "customer_policy_approved": False, "human_signoff_created": False}


class ReferenceContext(ContractModel):
    origin: Literal["synthetic_assumption"]
    scope_complete: bool | None
    identity_scope: Literal["supplied_directory_only", "unknown"]
    release_authorized: bool | None


class ReferenceInput(ContractModel):
    schema_version: Literal["internal-fixed-ledger-input-v0.1"]
    doc_id: NonEmpty
    policy_sha256: Digest
    document_sha256: Digest
    text: NonEmpty
    context: ReferenceContext | None


def _parse(raw):
    require(value_digest(POLICY) == POLICY_SHA256, "fixed_reference_policy_runtime_drift")
    try:
        if isinstance(raw, ReferenceInput):
            raw = raw.model_dump(warnings=False)
        result = ReferenceInput.model_validate(copy.deepcopy(raw))
    except (ValidationError, TypeError, ValueError, AttributeError):
        raise FactContractError("invalid_fixed_reference_input") from None
    require(result.policy_sha256 == POLICY_SHA256, "fixed_reference_policy_mismatch")
    require(text_digest(result.text) == result.document_sha256, "fixed_reference_body_hash_mismatch")
    return result


def parse_document(text):
    """Consume every character of the controlled language; no ignored prose."""
    require(len(text) <= 3_000_000 and "\r" not in text and text.endswith("\n"), "reference_text_frame_invalid")
    lines = text.splitlines(keepends=True)
    require(len(lines) >= 10 and lines[0].rstrip("\n") in TITLES and lines[1] == "기록형식: LEDGER-01\n"
            and lines[-1] == "[끝]\n", "reference_text_frame_invalid")
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    tables = {}
    spans = {}
    index = 2
    while index < len(lines) - 1:
        section = lines[index].rstrip("\n")
        require(section in SECTIONS and section not in tables, "reference_unknown_or_duplicate_section")
        index += 1
        columns = lines[index].rstrip("\n").split("|")
        require(len(columns) == 3 and set(columns) == set(SECTIONS[section]), "reference_columns_invalid")
        index += 1
        rows, locations = [], []
        while index < len(lines) - 1 and not lines[index].startswith("["):
            cells = lines[index].rstrip("\n").split("|")
            require(len(cells) == 3 and all(cells), "reference_cells_invalid")
            row = dict(zip(columns, cells, strict=True))
            if section == "[연결표]":
                require(bool(KEY.fullmatch(row["연결키"])) and row["연결키"].startswith("K-")
                        and bool(KEY.fullmatch(row["인물ID"])) and row["인물ID"].startswith("P-")
                        and bool(re.fullmatch(r"가상인물-[0-9a-f]{8}", row["표시명"])), "reference_identity_invalid")
            elif section == "[지급표]":
                require(bool(KEY.fullmatch(row["처리키"])) and row["처리키"].startswith("T-")
                        and bool(KEY.fullmatch(row["연결키"])) and row["연결키"].startswith("K-")
                        and bool(re.fullmatch(r"[0-9]{10}", row["금액단위"])), "reference_payment_invalid")
            else:
                require(bool(KEY.fullmatch(row["작업키"])) and row["작업키"].startswith("W-")
                        and bool(re.fullmatch(r"설비-[0-9]{2}", row["설비"]))
                        and bool(re.fullmatch(r"[0-9]{4}", row["시간단위"])), "reference_allocation_invalid")
            rows.append(row)
            locations.append({"start": starts[index], "end": starts[index] + len(lines[index]) - 1,
                              "sha256": text_digest(lines[index][:-1])})
            index += 1
        require(len(rows) <= MAX_ROWS, "reference_too_many_rows")
        tables[section], spans[section] = rows, locations
    require(set(tables) == set(SECTIONS) and bool(tables["[배정표]"]), "reference_scope_incomplete")
    for section, key in (("[연결표]", "연결키"), ("[지급표]", "처리키"), ("[배정표]", "작업키")):
        require(len({r[key] for r in tables[section]}) == len(tables[section]), "reference_duplicate_record_key")
    people = {}
    for row in tables["[연결표]"]:
        require(row["인물ID"] not in people or people[row["인물ID"]] == row["표시명"], "reference_identity_conflict")
        people[row["인물ID"]] = row["표시명"]
    return tables, spans


def _possible(count, released):
    """Four explicit predicates, separate from the SQL CASE oracle."""
    answers = set()
    for release in ([False, True] if released is None else [released]):
        conditions = (count >= POLICY["bulk_person_threshold"], 0 < count < POLICY["bulk_person_threshold"],
                      count == 0 and not release, count == 0 and release)
        require(sum(conditions) == 1, "reference_rule_non_unique")
        answers.update(grade for grade, matches in zip(GRADES, conditions, strict=True) if matches)
    return sorted(answers)


def certify_reference(raw) -> dict:
    """No target label is accepted as input. Certificates are conditional on scope."""
    supplied = _parse(raw)
    tables, spans = parse_document(supplied.text)
    directory = {r["연결키"]: (r["인물ID"], i) for i, r in enumerate(tables["[연결표]"])}
    witnesses = {}
    for i, row in enumerate(tables["[지급표]"]):
        if row["연결키"] in directory:
            person, pos = directory[row["연결키"]]
            witnesses.setdefault(person, {"person_id_sha256": text_digest(person),
                                          "directory": spans["[연결표]"][pos], "payment": spans["[지급표]"][i]})
    count = len(witnesses)
    context = supplied.context
    released = context.release_authorized if context else None
    grades = _possible(count, released)
    oracle = sql_oracle(supplied.text, threshold=POLICY["bulk_person_threshold"], released=released)
    require(oracle == {"linked_unique_people": count, "possible_grades": grades}, "reference_oracle_disagreement")
    scope_ok = bool(context and context.scope_complete is True and context.identity_scope == "supplied_directory_only")
    # Out-of-scope worlds are not enumerated or declared safe by this finite oracle.
    fixed = scope_ok and len(grades) == 1
    grade = grades[0] if fixed else None
    reasons = [] if fixed else (["closed_world_context_required"] if not scope_ok else ["release_status_required"])
    exclusions = {}
    if fixed:
        for other in GRADES:
            if other == grade:
                continue
            exclusions[other] = ("below_bulk_threshold" if other == "TS" else "count_outside_individual_band" if other == "S1"
                                  else "identified_payment_exists" if count else "release_condition_not_met")
    return {**FLAGS, "schema_version": "internal-fixed-ledger-certificate-v0.1", "doc_id": supplied.doc_id,
            "input_sha256": value_digest(supplied.model_dump()), "document_sha256": supplied.document_sha256,
            "policy_sha256": POLICY_SHA256, "status": "fixed_under_internal_policy" if fixed else "hold",
            "reference_grade": grade, "possible_grades_within_declared_scope": grades,
            "scope_accepted": scope_ok, "reasons": reasons, "excluded_grades": exclusions,
            "applied_rule": "IR-" + grade + "-01" if grade else None,
            "facts": {"linked_unique_people": count, "directory_rows": len(directory),
                      "payment_rows": len(tables["[지급표]"]), "allocation_rows": len(tables["[배정표]"])},
            "scope_basis": {"context_sha256": value_digest(context.model_dump() if context else None),
                            "origin": "synthetic_assumption", "complete_document_parse": True,
                            "real_world_identity_verified": False},
            "joined_evidence": list(witnesses.values()), "independent_sql_agrees": True,
            "natural_language_semantics_proven": False}


def verify_certificate(raw, certificate):
    require(value_digest(certify_reference(raw)) == value_digest(certificate), "reference_certificate_mismatch")
    return True


def style_view(raw):
    """Only declared noncausal formatting. Does NOT claim to detect all leakage."""
    supplied = _parse(raw)
    tables, _ = parse_document(supplied.text)
    lines = supplied.text.splitlines()
    headers = [lines[0], lines[1]]
    for i, line in enumerate(lines):
        if line in SECTIONS:
            headers.extend([line, lines[i + 1], "rows=" + str(len(tables[line]))])
    return "\n".join(headers)


def audit_policy_table():
    """Independent contract boundary enumeration; no customer-grade claims."""
    rows = []
    for count, released in product((0, 1, 999, 1000, 1001), (False, True, None)):
        expected = ({"TS"} if count >= 1000 else {"S1"} if count else
                    {"S2", "S3"} if released is None else {"S3"} if released else {"S2"})
        require(set(_possible(count, released)) == expected, "reference_policy_boundary_mismatch")
        rows.append({"count": count, "released": released, "possible_grades": sorted(expected)})
    return rows
