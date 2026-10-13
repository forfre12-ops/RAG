"""Independent checker tests; generator comparisons are tests, not the oracle."""
from __future__ import annotations

import copy
import itertools
import json
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(POC / "scripts"))

import audit_customer_reference_v1 as cli
import build_customer_guide_batch04 as author
from koipa import customer_guide_reference as guide
from koipa import customer_reference_audit_v1 as audit
from koipa.policy_facts import FactContractError, text_digest, value_digest


def facts_for(s=2, v=2, m=2):
    return dict(zip(audit.REQUIRED, (
        s == 0, s != 2, s != 0,
        (0, 1_000_000, 30_000_000)[v], (0, 40, 480)[v], v != 0, True,
        m != 0, m == 0, m != 0, m == 2, m != 0,
    ), strict=True), release_authorized=False, other_risk_present=False)


@pytest.fixture(scope="module")
def corpus():
    _, answers, payload = author.core_payload()
    return ([json.loads(row) for row in payload["authoring/documents.jsonl"].splitlines()], answers,
            [json.loads(row) for row in payload["answers/evidence.jsonl"].splitlines()])


@pytest.mark.parametrize("levels", itertools.product(range(3), repeat=3))
def test_all_27_literal_rows(levels):
    expected = audit.TRUTH_TABLE[levels]
    got = audit.expected_decision(facts_for(*levels))
    assert got["reference_grade"] == expected
    assert tuple(got["factors"].values()) == levels
    assert len(audit.TRUTH_TABLE) == 27
    assert got == guide.decide(facts_for(*levels))


@pytest.mark.parametrize("field", audit.REQUIRED)
def test_unknown_even_with_known_zero_holds(field):
    facts = facts_for(0, 0, 0)
    facts[field] = None
    got = audit.expected_decision(facts)
    assert got["status"] == "HOLD"
    assert got["reference_grade"] is None
    assert got["factors"] == dict.fromkeys(("S", "V", "M"))
    assert got["missing_evidence"] == [field]
    assert got == guide.decide(facts)


@pytest.mark.parametrize("cost,hours,utility,scope,expected_v", [
    (0, 0, False, True, 0), (0, 0, True, True, None),
    (1, 0, True, True, 1), (0, 1, True, True, 1),
    (1_000_000, 40, True, True, 1), (1_000_001, 40, True, True, None),
    (1_000_000, 41, True, True, None), (29_999_999, 479, True, True, None),
    (30_000_000, 0, True, True, 2), (0, 480, True, True, 2),
    (30_000_000, 1, True, True, 2), (1, 480, True, True, 2),
    (0, 1, False, True, None), (1, 0, False, True, None),
    (30_000_000, 480, True, False, None), (0, 0, False, False, None),
])
def test_local_value_boundaries(cost, hours, utility, scope, expected_v):
    facts = facts_for()
    facts.update(cost_krw=cost, person_hours=hours, economic_utility=utility, investment_scope_exact=scope)
    got = audit.expected_decision(facts)
    assert got["factors"]["V"] == expected_v
    assert got == guide.decide(facts)


@pytest.mark.parametrize("values", itertools.product((False, True), repeat=3))
def test_all_secrecy_boolean_combinations(values):
    facts = facts_for()
    for (key, _, _), value in zip(audit.GROUP_FIELDS["reader_scope"], values, strict=True):
        facts[key] = value
    got = audit.expected_decision(facts)
    assert got["factors"]["S"] == audit.S_TABLE.get(values)
    assert got == guide.decide(facts)


@pytest.mark.parametrize("values", itertools.product((False, True), repeat=5))
def test_all_management_boolean_combinations(values):
    facts = facts_for()
    for (key, _, _), value in zip(audit.GROUP_FIELDS["management_controls"], values, strict=True):
        facts[key] = value
    got = audit.expected_decision(facts)
    assert got["factors"]["M"] == audit.M_TABLE.get(values)
    assert got == guide.decide(facts)


@pytest.mark.parametrize("released,risk", itertools.product((False, True, None), repeat=2))
def test_disclosure_separate_from_grade(released, risk):
    facts = facts_for(0, 0, 0)
    facts.update(release_authorized=released, other_risk_present=risk)
    got = audit.expected_decision(facts)
    assert got["reference_grade"] == "S3"
    assert got["separate_disclosure_review_required"] is (released is not True or risk is not False)
    assert got["disclosure_permission_granted_by_classifier"] is False


@pytest.mark.parametrize("field,value", [
    ("cost_krw", True), ("person_hours", 0.5), ("economic_utility", 1),
    ("cost_krw", -1), ("person_hours", 10**12 + 1), ("public_exact_body", "아니오"),
])
def test_fact_type_and_range_rejection(field, value):
    facts = facts_for()
    facts[field] = value
    with pytest.raises(FactContractError):
        audit.expected_decision(facts)


def test_parser_uses_visible_values():
    facts = facts_for(1, 1, 1)
    assert audit.parse_visible_context(guide.encode_context(facts)) == facts


@pytest.mark.parametrize("mutate", [
    lambda c: c.reverse(),
    lambda c: c[0].update(origin="observed"),
    lambda c: c[0].update(name="impact_description"),
    lambda c: c[0].update(value=c[0]["value"].replace("아니오", "0")),
    lambda c: c[1].update(value=c[1]["value"].replace("30000000", "030000000")),
    lambda c: c[1].update(value=c[1]["value"].replace("30000000", "30000000.0")),
    lambda c: c[0].update(extra=True),
    lambda c: c[3].update(value=0),
    lambda c: c.pop(),
])
def test_noncanonical_context_rejected(mutate):
    context = guide.encode_context(facts_for())
    mutate(context)
    with pytest.raises(FactContractError):
        audit.parse_visible_context(context)


def test_existing_164_without_generator_decision_calls(corpus, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("author engine called by independent audit")
    for name in ("decide", "_factor_levels", "grade_from_levels", "decode_context", "validate_reference"):
        monkeypatch.setattr(guide, name, forbidden)
    before = value_digest(corpus)
    result = audit.audit_reference(*corpus)
    assert result["cases"] == result["passed"] == 164
    assert result["grade_counts"] == {"TS": 30, "S1": 48, "S2": 41, "S3": 45}
    assert result["independent_real_world_truth_authority"] is False
    assert value_digest(corpus) == before


@pytest.mark.parametrize("target,mutate,pattern", [
    ("answer", lambda a: a.update(reference_grade="TS", other_grade_exclusions={g: "changed answer rejection probe" for g in ("S1", "S2", "S3")}), "answer_mismatch"),
    ("answer", lambda a: a.update(policy_sha256="f" * 64), "mixed_policies|policy_mismatch"),
    ("detail", lambda e: e["decision"]["factors"].update(S=0), "decision_mismatch"),
    ("detail", lambda e: e["decision"].update(product=999), "decision_mismatch"),
    ("detail", lambda e: e["premises"].update(cost_krw=123456), "premise_mismatch"),
    ("detail", lambda e: e["context_evidence"][0].update(quote="made up"), "evidence_mismatch"),
    ("detail", lambda e: e.update(training_allowed=True), "permission_promotion"),
    ("detail", lambda e: e.update(real_world_premises_verified=True), "authority_promotion"),
    ("detail", lambda e: e.update(body_sha256="f" * 64), "input_binding"),
])
def test_mutated_grade_factors_premises_and_authority_rejected(corpus, target, mutate, pattern):
    rs, ans, es = copy.deepcopy(corpus)
    i = next(i for i, a in enumerate(ans) if a["reference_grade"] == "S1")
    doc_id = ans[i]["doc_id"]
    selected = ans[i] if target == "answer" else next(e for e in es if e["doc_id"] == doc_id)
    mutate(selected)
    with pytest.raises(FactContractError, match=pattern):
        audit.audit_reference(rs, ans, es)


def test_visible_premise_corruption_rehashed_input_still_rejected(corpus):
    rs, ans, es = copy.deepcopy(corpus)
    record = rs[0]
    record["input"]["context"][0]["value"] = record["input"]["context"][0]["value"].replace("해당 판본 전체의 외부 공개: 아니오", "해당 판본 전체의 외부 공개: 예")
    record["input_sha256"] = value_digest(record["input"])
    next(a for a in ans if a["doc_id"] == record["input"]["doc_id"])["input_sha256"] = record["input_sha256"]
    next(e for e in es if e["doc_id"] == record["input"]["doc_id"])["input_sha256"] = record["input_sha256"]
    with pytest.raises(FactContractError):
        audit.audit_reference(rs, ans, es)


def test_missing_and_duplicate_details_rejected(corpus):
    with pytest.raises(FactContractError):
        audit.audit_reference(corpus[0], corpus[1], corpus[2][1:])
    es = copy.deepcopy(corpus[2])
    es[-1] = es[0]
    with pytest.raises(FactContractError):
        audit.audit_reference(corpus[0], corpus[1], es)


def test_semantic_candidates_are_bound_additive_not_blanket(corpus):
    result = audit.semantic_family_candidates(corpus[0])
    assert result["candidate_groups"] == 4
    assert result["candidate_member_documents"] == 8
    assert not result["split_rules_changed"]
    assert not result["exhaustive_review_performed"]
    bodies = {r["input"]["doc_id"]: r["input"]["text"] for r in corpus[0]}
    for proposal in result["proposals"]:
        assert "reason" in proposal
        for member in proposal["members"]:
            assert bodies[member["doc_id"]][member["start"]:member["end"]] == member["quote"]
            assert text_digest(member["quote"]) == member["quote_sha256"]


def test_partial_corpus_has_no_unbound_candidates(corpus):
    result = audit.semantic_family_candidates([corpus[0][0]])
    assert result["candidate_groups"] == 0
    assert len(result["skipped_absent_groups"]) == 4


def _write_pack(pack, corpus):
    pack.mkdir()
    for name, rows in zip(cli.INPUT_FILES, corpus, strict=True):
        path = pack / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_cli_exclusive_output_and_source_unchanged(tmp_path, corpus):
    pack, out = tmp_path / "pack", tmp_path / "result"
    _write_pack(pack, corpus)
    before = {p.relative_to(pack).as_posix(): p.read_bytes() for p in pack.rglob("*") if p.is_file()}
    result = cli.run(pack, out)
    assert result["cases"] == 164
    assert set(p.name for p in out.iterdir()) == {"audit.json", "semantic_candidates.json", "manifest.json"}
    assert {p.relative_to(pack).as_posix(): p.read_bytes() for p in pack.rglob("*") if p.is_file()} == before
    with pytest.raises(FactContractError):
        cli.run(pack, out)
    with pytest.raises(FactContractError):
        cli.run(pack, pack / "nested-output")


def test_cli_zero_rows_no_success_or_output(tmp_path):
    pack, out = tmp_path / "pack", tmp_path / "result"
    _write_pack(pack, ([], [], []))
    with pytest.raises(FactContractError, match="zero_cases"):
        cli.run(pack, out)
    assert not out.exists()


def test_audit_empty_input_fails():
    with pytest.raises(FactContractError):
        audit.audit_reference([], [], [])
