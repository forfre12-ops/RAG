"""Regression closure for aliases, direct evaluators, and incomplete strict runs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from koipa import dataset_usage as usage
from test_classification_quality_barriers import make_pack, write_rows


@pytest.mark.parametrize("field,key", [("document_family_id", "family_id"),
                                      ("source_document_family_id", "family_id"),
                                      ("source_document_id", "doc_id")])
def test_existing_identity_aliases_are_not_a_bypass(field, key):
    known = usage.fixture_registry()["records"][0]
    row = {"text": "changed partial body", "label": "S1", field: known[key]}
    with pytest.raises(usage.DatasetUsageError):
        usage.assert_dataset_usage([row], purpose="model_evaluation")


@pytest.mark.parametrize("purpose,flag", [("training", "training_use_permitted"),
                                         ("model_evaluation", "evaluation_use_permitted"),
                                         ("model_evaluation", "evaluation_allowed")])
@pytest.mark.parametrize("value", [False, "false", 0, None])
def test_permission_aliases_deny_even_with_other_true_flags(purpose, flag, value):
    row = {"text": "unrelated", "label": "S1", f"{purpose}_allowed": True, flag: value}
    with pytest.raises(usage.DatasetUsageError):
        usage.assert_dataset_usage([row], purpose=purpose)


def test_identity_whitespace_is_not_a_bypass():
    row = {"text": "changed partial body", "doc_id": " " + usage.fixture_registry()["records"][0]["doc_id"] + "\n"}
    with pytest.raises(usage.DatasetUsageError):
        usage.assert_dataset_usage([row], purpose="training")


def test_strict_without_character_probe_is_incomplete(tmp_path):
    import measure_grade_phrase_leak as leak
    make_pack(tmp_path)
    # The repeated-phrase detector alone deliberately observes no signal.
    assert leak.main(["--pool", str(tmp_path), "--strict", "--min-docs", "999", "--top", "0"]) == 3


@pytest.mark.parametrize("entry", ["comparison", "window"])
def test_direct_evaluation_callbacks_reject_before_execution(monkeypatch, entry):
    from koipa.proxy_model_comparison import predict_model
    from koipa.proxy_training_finalization import collect_document_window_logits
    monkeypatch.setitem(sys.modules, "torch", None)
    rows = [{"text": "unrelated", "label": "S1", "dataset_role": "policy_fixture"}]
    with pytest.raises(usage.DatasetUsageError):
        if entry == "comparison":
            predict_model(Path("absent"), rows)
        elif entry == "window":
            collect_document_window_logits(None, None, rows)


@pytest.mark.parametrize("module,function", [("eval_serving_path", "load_rows"),
                                             ("eval_serving_vs_raw", "load_rows")])
def test_serving_evaluation_loaders_check_before_projection(tmp_path, module, function):
    import importlib
    source = tmp_path / "test.jsonl"
    row = {"text": "changed partial body", "label": "S1", "label_source": "public_definitive",
           "document_family_id": usage.fixture_registry()["records"][0]["family_id"]}
    write_rows(source, [row])
    with pytest.raises(usage.DatasetUsageError):
        getattr(importlib.import_module(module), function)(source)


def test_ab_training_and_evaluation_have_distinct_permissions(tmp_path):
    from ab_tell_train import load
    source = tmp_path / "split.jsonl"
    write_rows(source, [{"text": "training only", "label": "S1", "training_allowed": True,
                         "model_evaluation_allowed": False}])
    assert len(load(source, purpose="training")[0]) == 1
    with pytest.raises(usage.DatasetUsageError):
        load(source, purpose="model_evaluation")
    write_rows(source, [{"text": "evaluation only", "label": "S1", "training_allowed": False,
                         "model_evaluation_allowed": True}])
    assert len(load(source, purpose="model_evaluation")[0]) == 1
    with pytest.raises(usage.DatasetUsageError):
        load(source, purpose="training")


def test_audit_rejects_source_change_during_measurement(tmp_path, monkeypatch):
    import measure_grade_phrase_leak as leak
    make_pack(tmp_path)
    original = leak._null_single_clue

    def mutate(*args, **kwargs):
        value = original(*args, **kwargs)
        source = tmp_path / "answers.candidate.jsonl"
        source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return value

    monkeypatch.setattr(leak, "_null_single_clue", mutate)
    with pytest.raises(ValueError, match="changed"):
        leak.audit(pool=tmp_path, min_docs=2, min_chars=3)


def test_registry_reference_file_is_packaged():
    import tomllib
    poc = Path(__file__).resolve().parents[2]
    project = tomllib.loads((poc / "pyproject.toml").read_text(encoding="utf-8"))
    assert "policy_fixture_registry.json" in project["tool"]["setuptools"]["package-data"]["koipa"]
    assert json.loads(usage.REGISTRY_PATH.read_text(encoding="utf-8"))["usage"] == "policy_fixture"
