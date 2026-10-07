"""New pack adapter cannot silently succeed on zero rows or mix answer text."""
from __future__ import annotations

import copy

import pytest

import measure_customer_guide_shortcuts as probe
from customer_guide_cases import build_reference
from koipa.customer_guide_reference import validate_reference
from koipa.policy_facts import FactContractError


@pytest.mark.parametrize("profile", ["body_only", "context_only", "body_context"])
def test_projection(profile):
    records, answers, details = build_reference()
    docs = validate_reference(records, answers, details)
    rows = probe.project_rows(docs, answers, profile)
    assert len(rows) == 20 and len({r["family_id"] for r in rows}) == 20
    for row, d in zip(rows, docs, strict=True):
        assert "other_grade_exclusions" not in row["text"]
        assert "reference_grade" not in row["text"]
        if profile == "body_only":
            assert row["text"] == d.input.text
        if profile == "context_only":
            assert d.input.text not in row["text"]
            assert "synthetic_assumption" in row["text"]


def test_zero_rows_fail():
    with pytest.raises(FactContractError):
        probe.project_rows([], [], "body_only")


@pytest.mark.parametrize("value,expected", [(19.999, False), (20, False), (20.000000000000007, False), (20.00001, True)])
def test_warning_threshold_ignores_roundoff(value, expected):
    assert probe.excess_warning({"stratified_cv": {"excess_pp": value}, "family_cv": {"excess_pp": 0}}) is expected


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign"])
def test_malformed_answer_coverage(mutation):
    records, answers, details = build_reference()
    docs = validate_reference(records, answers, details)
    if mutation == "missing":
        answers.pop()
    elif mutation == "duplicate":
        answers[1] = copy.deepcopy(answers[0])
    else:
        answers[0]["doc_id"] = "doc-" + "0"*24
    with pytest.raises(FactContractError):
        probe.project_rows(docs, answers, "body_context")


def test_cli_unsupported_pack_fails(tmp_path):
    assert probe.main(["--pack", str(tmp_path), "--out", str(tmp_path/"result.json")]) == 2


def test_adapter_writes_diagnostic_not_qualification(tmp_path, monkeypatch):
    root = tmp_path/"pack"
    probe.pack.prepare(root)
    monkeypatch.setattr(probe, "measure", lambda rows, seeds: {"stratified_cv": {"excess_pp": 40}, "family_cv": {"excess_pp": 40}})
    out = tmp_path/"diagnostic.json"
    result = probe.audit(root, out)
    assert result["documents"] == 20 and result["model_evaluation_allowed"] is False
    assert all(v["warning_excess_over_permutation"] for v in result["profiles"].values())
    assert probe.main(["--pack", str(root), "--out", str(out)]) == 2
    assert probe.main(["--pack", str(root), "--out", str(root/"new.json")]) == 2
