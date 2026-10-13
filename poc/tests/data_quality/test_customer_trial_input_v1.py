"""Public-input lineage and fail-closed offline preflight, no model runtime."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import prepare_customer_trial_input_v1 as cli
from koipa.customer_benchmark import FLAGS, presented_text, ModelInput
from koipa.customer_guide_reference import POLICY_SHA256, encode_context
from koipa.customer_trial_input_v1 import prepare_trial_inputs, validate_diagnostic_predictions, verify_trial_inputs
from koipa.policy_facts import FactContractError, text_digest, value_digest


@pytest.fixture
def tokenizer(tmp_path):
    from tokenizers import Tokenizer, models, pre_tokenizers, processors
    value = Tokenizer(models.WordLevel({"[UNK]": 0, "[CLS]": 1, "[SEP]": 2, "내용": 3}, unk_token="[UNK]"))
    value.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    value.post_processor = processors.TemplateProcessing(single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)])
    # Active persisted truncation must be disabled by preflight, not silently used.
    value.enable_truncation(max_length=8)
    value.enable_padding(length=8)
    path = tmp_path/"tokenizer.json"
    path.write_text(value.to_str(), encoding="utf-8")
    return path


@pytest.fixture
def row():
    facts = {"public_exact_body": False, "obtainable_without_holder": False,
        "ordinary_access_difficult": True, "cost_krw": 30000000, "person_hours": 600,
        "economic_utility": True, "investment_scope_exact": True, "secrecy_manageable": True,
        "all_staff_knows": False, "business_need_only": True, "individual_approval": True,
        "access_enforced": True, "release_authorized": False, "other_risk_present": False}
    data = {"text": "측정 절차\n\n"+"내용 "*50, "context": encode_context(facts)}
    data["doc_id"] = "doc-"+value_digest(data)[:24]
    return {**FLAGS, **data, "input_sha256": value_digest(data)}


def _rebind(row):
    data = {k: row[k] for k in ("doc_id", "text", "context")}
    row["input_sha256"] = value_digest(data)
    return row


def _kwargs(tokenizer):
    return {"tokenizer_path": tokenizer, "expected_tokenizer_sha256": hashlib.sha256(tokenizer.read_bytes()).hexdigest(),
            "expected_policy_sha256": POLICY_SHA256}


def _prepare(row, tokenizer, **kwargs):
    return prepare_trial_inputs([row], **{**_kwargs(tokenizer), **kwargs})


def _prediction(preflight, **overrides):
    view = preflight["views"][0]
    result = {k: view[k] for k in ("doc_id", "input_sha256", "presented_input_sha256", "policy_sha256",
        "profile", "tokenizer_sha256", "token_payload_sha256")}
    return {**result, "run_id": "diagnostic-stub", "model_sha256": "a"*64,
            "status": "ok", "predicted_grade": "TS", "reason_code": None, **overrides}


def _bind(preflight, row, tokenizer, predictions):
    return validate_diagnostic_predictions(preflight, [row], predictions, **_kwargs(tokenizer),
        expected_model_sha256="a"*64, run_id="diagnostic-stub")


def test_exact_existing_renderer_and_no_metadata_exposed(row, tokenizer):
    value = _prepare(row, tokenizer)
    view = value["views"][0]
    from types import SimpleNamespace
    document = ModelInput.model_validate({k: row[k] for k in ("doc_id", "text", "context")})
    assert view["presented_text"] == presented_text(SimpleNamespace(input=document), "body_context")
    assert view["presented_input_sha256"] == text_digest(view["presented_text"])
    assert view["token_payload_sha256"] == value_digest(view["model_payload"])
    assert view["model_payload"]["input_ids"][:1] == [1]
    assert view["model_payload"]["input_ids"][-1:] == [2]
    assert view["tokens_with_special"] > 8  # persisted truncation disabled
    assert set(view["model_payload"]) == {"input_ids", "attention_mask", "token_type_ids"}
    assert not any(k in value or k in view for k in ("reference_grade", "factors", "rule_ids", "family_id", "style", "rationale"))
    assert value["input_ready_only"] == 1 and value["needs_review"] == 0
    assert value["grade_scoring_allowed"] is value["model_artifact_verified"] is False
    assert all(value[k] is False and view[k] is False for k in FLAGS)


def test_no_answer_or_decision_function_is_called(row, tokenizer, monkeypatch):
    import koipa.customer_benchmark as benchmark
    import koipa.customer_guide_reference as reference
    def forbidden(*args, **kwargs):
        raise AssertionError("Answer access or classification is forbidden")
    monkeypatch.setattr(benchmark, "validate_answers", forbidden)
    monkeypatch.setattr(benchmark, "score_predictions", forbidden)
    monkeypatch.setattr(reference, "decide", forbidden)
    assert _prepare(row, tokenizer)["documents"] == 1


@pytest.mark.parametrize("key", ["reference_grade", "factors", "rule_ids", "answers", "family_id", "style", "claims", "rationale"])
def test_authoring_or_answer_field_is_rejected(row, tokenizer, key):
    row[key] = "forbidden"
    with pytest.raises(FactContractError, match="input_fields_invalid"):
        _prepare(row, tokenizer)


@pytest.mark.parametrize("flag", list(FLAGS))
@pytest.mark.parametrize("value", [True, 0, None, "false"])
def test_permission_flags_are_strict_false(row, tokenizer, flag, value):
    row[flag] = value
    with pytest.raises(FactContractError, match="permission_invalid"):
        _prepare(row, tokenizer)


def test_rows_unchanged_and_order_independent(row, tokenizer):
    other = _rebind({**copy.deepcopy(row), "doc_id": "doc-"+"b"*24, "text": row["text"]+"부록"})
    before = copy.deepcopy(row)
    first = prepare_trial_inputs([row, other], **_kwargs(tokenizer))
    assert first == prepare_trial_inputs([other, row], **_kwargs(tokenizer))
    assert row == before


@pytest.mark.parametrize("profile", ["body_context", "body_only"])
def test_missing_context_is_hold_not_false(row, tokenizer, profile):
    row["context"] = []
    _rebind(row)
    result = _prepare(row, tokenizer, profile=profile)
    assert result["needs_review"] == 1
    view = result["views"][0]
    assert "required_context_group_missing" in view["reason_codes"]
    assert len(view["missing_context_groups"]) == 5
    assert not view["body_only_grade_scoring_allowed"]


def test_unknown_required_context_fact_is_hold(row, tokenizer):
    row["context"][0]["value"] = row["context"][0]["value"].replace("해당 판본 전체의 외부 공개: 아니오", "해당 판본 전체의 외부 공개: 미수신")
    _rebind(row)
    view = _prepare(row, tokenizer)["views"][0]
    assert view["status"] == "needs_review"
    assert view["missing_context_facts"] == ["public_exact_body"]


def test_missing_release_fact_does_not_grant_disclosure(row, tokenizer):
    row["context"][3]["value"] = None
    _rebind(row)
    value = _prepare(row, tokenizer)
    assert value["input_ready_only"] == 1  # input fit, not policy/disclosure approval
    assert value["grade_scoring_allowed"] is False


def test_malformed_complete_context_cannot_reach_model(row, tokenizer):
    row["context"][0]["value"] += "; reference_grade: TS"
    _rebind(row)
    with pytest.raises(FactContractError, match="context_labels_invalid"):
        _prepare(row, tokenizer)


def test_body_only_always_held_even_when_short(row, tokenizer):
    value = _prepare(row, tokenizer, profile="body_only")
    view = value["views"][0]
    assert view["presented_text"] == row["text"]
    assert view["reason_codes"] == ["body_only_context_not_presented"]
    assert value["input_ready_only"] == 0 and value["needs_review"] == 1
    assert _bind(value, row, tokenizer, [])["statuses"] == {"ok": 0, "needs_review": 1, "error": 0}
    with pytest.raises(FactContractError, match="prediction_ids_invalid"):
        _bind(value, row, tokenizer, [_prediction(value)])


def test_long_document_is_held_without_truncation(row, tokenizer):
    row["text"] = "내용 "*700
    _rebind(row)
    value = _prepare(row, tokenizer)
    view = value["views"][0]
    assert view["tokens_with_special"] > 700 and len(view["model_payload"]["input_ids"]) > 700
    assert view["reason_codes"] == ["input_exceeds_512_tokens"]
    assert not view["truncation_applied"] and not view["chunking_applied"]
    assert _bind(value, row, tokenizer, [])["results"][0]["reason_code"] == "preflight_hold"


@pytest.mark.parametrize("count, held", [(512, False), (513, True)])
def test_exact_token_boundary(row, tokenizer, count, held):
    first = _prepare(row, tokenizer)
    overhead = first["views"][0]["tokens_with_special"] - len(row["text"].split())
    row["text"] = "내용 "*(count-overhead)
    _rebind(row)
    result = _prepare(row, tokenizer)
    assert result["views"][0]["tokens_with_special"] == count
    assert (result["views"][0]["status"] == "needs_review") == held


@pytest.mark.parametrize("field", ["text", "context", "doc_id"])
def test_changed_input_without_rebinding_rejected(row, tokenizer, field):
    row[field] = {"text": row["text"]+"수정", "context": [], "doc_id": "doc-"+"b"*24}[field]
    with pytest.raises(FactContractError, match="input_hash_mismatch"):
        _prepare(row, tokenizer)


def test_changed_tokenizer_and_policy_rejected(row, tokenizer):
    kwargs = _kwargs(tokenizer)
    tokenizer.write_text(tokenizer.read_text(encoding="utf-8")+" ", encoding="utf-8")
    with pytest.raises(FactContractError, match="tokenizer_hash_mismatch"):
        prepare_trial_inputs([row], **kwargs)
    with pytest.raises(FactContractError, match="policy_hash_mismatch"):
        _prepare(row, tokenizer, expected_policy_sha256="0"*64)


@pytest.mark.parametrize("mutate", ["source", "presented", "tokens", "status", "flags", "contract", "source_hash"])
def test_forged_preflight_replayed_before_output_binding(row, tokenizer, mutate):
    value = _prepare(row, tokenizer)
    if mutate == "source":
        value["source_inputs"][0]["text"] += "변경"
    elif mutate == "presented":
        value["views"][0]["presented_text"] += "변경"
    elif mutate == "tokens":
        value["views"][0]["model_payload"]["input_ids"] = [0]
    elif mutate == "status":
        value["views"][0]["status"] = "needs_review"
    elif mutate == "flags":
        value["views"][0]["training_allowed"] = True
    elif mutate == "contract":
        value["max_tokens_with_special"] = 4096
    else:
        value["source_files_sha256"] = {}
    with pytest.raises(FactContractError, match="preflight_replay_mismatch"):
        _bind(value, row, tokenizer, [])


@pytest.mark.parametrize("field", ["model_sha256", "run_id", "input_sha256", "presented_input_sha256", "policy_sha256",
                                  "tokenizer_sha256", "token_payload_sha256", "profile"])
def test_forged_prediction_lineage_rejected(row, tokenizer, field):
    value = _prepare(row, tokenizer)
    raw = _prediction(value)
    raw[field] = "body_only" if field == "profile" else "another-run" if field == "run_id" else "0"*64
    with pytest.raises(FactContractError, match="identity_mismatch|lineage_mismatch"):
        _bind(value, row, tokenizer, [raw])


@pytest.mark.parametrize("status, grade, code", [("ok", None, None), ("ok", "TS", "unexpected"), ("error", "TS", "error-run"),
    ("error", None, None), ("needs_review", None, None), ("needs_review", "TS", "unsafe message with details")])
def test_prediction_status_contract(row, tokenizer, status, grade, code):
    value = _prepare(row, tokenizer)
    with pytest.raises(FactContractError):
        _bind(value, row, tokenizer, [_prediction(value, status=status, predicted_grade=grade, reason_code=code)])


@pytest.mark.parametrize("status, grade, code", [("ok", "TS", None), ("error", None, "tokenizer-error"),
    ("needs_review", None, "evidence-unknown"), ("needs_review", "S1", "policy-conflict")])
def test_prediction_statuses_preserved_not_scored(row, tokenizer, status, grade, code):
    value = _prepare(row, tokenizer)
    result = _bind(value, row, tokenizer, [_prediction(value, status=status, predicted_grade=grade, reason_code=code)])
    assert result["results"][0]["status"] == status
    assert result["results"][0]["predicted_grade"] == grade
    assert result["statuses"][status] == 1 and result["denominator"] == 1
    assert not result["grade_scoring_performed"] and not result["model_artifact_verified"]


def test_missing_prediction_is_error_not_removed(row, tokenizer):
    value = _prepare(row, tokenizer)
    result = _bind(value, row, tokenizer, [])
    assert result["denominator"] == result["missing_responses"] == result["statuses"]["error"] == 1
    assert result["results"][0]["reason_code"] == "diagnostic_prediction_missing"


@pytest.mark.parametrize("case", ["duplicate", "extra", "extra_field"])
def test_prediction_extra_and_duplicate_rejected(row, tokenizer, case):
    value = _prepare(row, tokenizer)
    raw = _prediction(value)
    if case == "extra":
        raw["doc_id"] = "doc-"+"b"*24
    elif case == "extra_field":
        raw["reference_grade"] = "TS"
    with pytest.raises(FactContractError):
        _bind(value, row, tokenizer, [raw, raw] if case == "duplicate" else [raw])


def _pack(row, tokenizer, tmp_path):
    source = tmp_path/"input.jsonl"
    source.write_text(cli._jsonl([row]), encoding="utf-8")
    kwargs = {"inputs": source, "expected_input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "tokenizer": tokenizer, "expected_tokenizer_sha256": _kwargs(tokenizer)["expected_tokenizer_sha256"]}
    root = tmp_path/"new-pack"
    return root, kwargs


def test_cli_pack_reads_only_supplied_inputs_and_no_answers(row, tokenizer, tmp_path, monkeypatch):
    root, kwargs = _pack(row, tokenizer, tmp_path)
    original = Path.read_bytes
    accessed = []
    def read(path):
        assert "answers" not in path.parts
        accessed.append(path)
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    summary = cli.prepare(root, **kwargs)
    assert summary["documents"] == 1 and summary["input_ready_only"] == 1
    assert cli.verify(root, **kwargs) == summary
    assert all("answers" not in path.parts for path in accessed)
    assert len(list(root.rglob("*.json*"))) == 5
    with pytest.raises(FactContractError, match="output_exists"):
        cli.prepare(root, **kwargs)


@pytest.mark.parametrize("case", ["public", "rendered", "rehash", "flags", "zero_flag", "path", "extra", "source", "tokenizer", "source_bytes"])
def test_pack_tampering_or_live_source_change_rejected(row, tokenizer, tmp_path, case):
    root, kwargs = _pack(row, tokenizer, tmp_path)
    cli.prepare(root, **kwargs)
    manifest = json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if case in {"public", "rendered", "rehash"}:
        name = "inputs/public_inputs.jsonl" if case == "public" else "inputs/presented_inputs.jsonl"
        text = (root/name).read_text(encoding="utf-8").replace("측정 절차", "변경 절차")
        (root/name).write_text(text, encoding="utf-8")
        if case == "rehash":
            manifest["files"][name] = text_digest(text)
    elif case in {"flags", "zero_flag"}:
        manifest["training_allowed"] = True if case == "flags" else 0
    elif case == "path":
        manifest["files"]["../escape"] = "a"*64
    elif case == "extra":
        (root/"extra.txt").write_text("extra", encoding="utf-8")
    elif case == "source":
        manifest["script_sha256"] = "a"*64
    elif case == "tokenizer":
        tokenizer.write_text(tokenizer.read_text(encoding="utf-8")+" ", encoding="utf-8")
    else:
        kwargs["inputs"].write_text(cli._jsonl([row])+" ", encoding="utf-8")
    (root/"manifest.json").write_text(cli._json(manifest), encoding="utf-8")
    with pytest.raises(FactContractError):
        cli.verify(root, **kwargs)


def test_cli_empty_source_fails_nonzero_without_output(tokenizer, tmp_path, capsys):
    path = tmp_path/"empty.jsonl"
    path.write_text("", encoding="utf-8")
    out = tmp_path/"never-created"
    args = ["prepare", "--inputs", str(path), "--expected-input-sha256", text_digest(""),
        "--tokenizer", str(tokenizer), "--expected-tokenizer-sha256", _kwargs(tokenizer)["expected_tokenizer_sha256"],
        "--out", str(out)]
    assert cli.main(args) == 1
    assert not out.exists() and "trial_source_empty_or_blank_row" in capsys.readouterr().out


def test_replay_same_input_is_exact(row, tokenizer):
    value = _prepare(row, tokenizer)
    assert verify_trial_inputs(value, [row], **_kwargs(tokenizer)) == value
