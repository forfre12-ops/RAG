"""No real checkpoint loading: tiny safe headers and explicit test runtimes."""
from __future__ import annotations

import copy
import json
import socket
import struct
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import check_customer_model_connection_v1 as cli
import koipa.customer_model_connection_v1 as connection
from koipa.customer_benchmark import FLAGS
from koipa.policy_facts import FactContractError, value_digest


def _json_file(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_weights(path, header=None):
    if header is None:
        header = {"__metadata__": {"format": "pt"},
            "classifier.bias": {"dtype": "F32", "shape": [4], "data_offsets": [0, 16]},
            "classifier.weight": {"dtype": "F32", "shape": [4, 4], "data_offsets": [16, 80]},
            "deberta.embeddings.word_embeddings.weight": {"dtype": "F32", "shape": [8, 4], "data_offsets": [80, 208]}}
    raw = json.dumps(header).encode("utf-8")
    raw += b" "*((-len(raw)) % 8)
    path.write_bytes(struct.pack("<Q", len(raw))+raw+b"\0"*208)


@pytest.fixture
def checkpoint(tmp_path):
    from tokenizers import Tokenizer, models, pre_tokenizers, processors
    root = tmp_path/"checkpoint"
    root.mkdir()
    config = {"architectures": ["DebertaV2ForSequenceClassification"], "model_type": "deberta-v2", "dtype": "float32",
        "hidden_size": 4, "num_hidden_layers": 2, "vocab_size": 8, "max_position_embeddings": 512,
        "pad_token_id": 0, "id2label": {"0": "TS", "1": "S1", "2": "S2", "3": "S3"},
        "label2id": {"TS": 0, "S1": 1, "S2": 2, "S3": 3}}
    _json_file(root/"config.json", config)
    _json_file(root/"tokenizer_config.json", {"tokenizer_class": "BertTokenizer", "model_max_length": 512,
        "pad_token": "[PAD]", "local_files_only": False})
    tokenizer = Tokenizer(models.WordLevel({"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3,
        "이": 4, "문장은": 5, "점검": 6, "확인": 7}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tokenizer.post_processor = processors.TemplateProcessing(single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)])
    tokenizer.enable_truncation(max_length=4)
    tokenizer.enable_padding(length=4)
    (root/"tokenizer.json").write_text(tokenizer.to_str(), encoding="utf-8")
    _write_weights(root/"model.safetensors")
    # Existing checkpoint has these, but the adapter must never load them.
    (root/"training_args.bin").write_bytes(b"not-a-pickle-loader-test")
    (root/"val_logits.jsonl").write_text("not-an-input-source", encoding="utf-8")
    return root


def _rewrite(root, name, mutate):
    path = root/name
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    _json_file(path, value)


def _fake_runtime(monkeypatch, logits=None, mutate=None):
    calls = []
    def loader(root, inventory):
        def forward(payload):
            calls.append(copy.deepcopy(payload))
            if mutate:
                mutate(root, len(calls))
            return logits(len(calls)) if callable(logits) else [0.1, 0.4, -0.3, 0.2] if logits is None else logits
        return connection._Runtime("test_double", inventory["model_sha256"], inventory["label_order"], forward)
    monkeypatch.setattr(connection, "_load_local_runtime", loader)
    return calls


def _smoke(checkpoint):
    inventory = connection.inventory_checkpoint(checkpoint)
    return connection.run_connection_smoke(checkpoint, expected_checkpoint_sha256=inventory["checkpoint_sha256"])


def test_inventory_reads_bytes_without_torch_model_or_answers(checkpoint, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No model loading in inventory")
    monkeypatch.setattr(connection, "_load_local_runtime", forbidden)
    result = connection.inventory_checkpoint(checkpoint)
    assert result["status"] == "local_checkpoint_inventory_only"
    assert result["weight_header"]["tensor_count"] == 3
    assert result["model_forward_executed"] is False and result["model_forward_calls"] == 0
    assert result["model_sha256"] == result["files"]["model.safetensors"]["sha256"]
    assert result["checkpoint_sha256"] == value_digest(result["files"])
    assert result["benchmark_documents_used"] == 0 and not result["accuracy_measured"]
    assert result["label_order"] == ["TS", "S1", "S2", "S3"]
    assert all(result[k] is False for k in FLAGS)
    assert result["ignored_files_not_loaded"] == ["training_args.bin", "val_logits.jsonl"]
    assert len(result["registered_smoke_inputs"]) == 2
    assert all(v["tokens_with_special"] > 4 for v in result["registered_smoke_inputs"])
    assert result["runtime_loader_local_files_only"] is True and result["runtime_loader_trust_remote_code"] is False


def test_name_is_not_model_identity(checkpoint, tmp_path):
    original = connection.inventory_checkpoint(checkpoint)
    moved = tmp_path/"another-model-name"
    checkpoint.rename(moved)
    assert connection.inventory_checkpoint(moved)["checkpoint_sha256"] == original["checkpoint_sha256"]
    path = moved/"model.safetensors"
    raw = path.read_bytes()
    path.write_bytes(raw[:-4]+struct.pack("<f", 0.25))
    changed = connection.inventory_checkpoint(moved)
    assert changed["checkpoint_sha256"] != original["checkpoint_sha256"]
    assert changed["model_sha256"] != original["model_sha256"]


@pytest.mark.parametrize("name", ["adapter_config.json", "adapter_model.safetensors", "pytorch_model.bin",
    "pytorch_model.bin.index.json", "model.safetensors.index.json", "modeling_remote.py", "REMOTE.PY", "extra.safetensors"])
def test_unsafe_or_ambiguous_extra_artifacts_rejected(checkpoint, name):
    (checkpoint/name).write_text("{}", encoding="utf-8")
    with pytest.raises(FactContractError, match="unsupported_loader_artifact|weight_files_ambiguous"):
        connection.inventory_checkpoint(checkpoint)


@pytest.mark.parametrize("name", connection.ARTIFACTS)
def test_missing_required_artifact_rejected(checkpoint, name):
    (checkpoint/name).unlink()
    with pytest.raises(FactContractError):
        connection.inventory_checkpoint(checkpoint)


@pytest.mark.parametrize("name", ["config.json", "tokenizer_config.json"])
@pytest.mark.parametrize("key", ["auto_map", "custom_pipelines", "quantization_config", "compression_config", "code_revision"])
def test_remote_and_custom_configs_rejected(checkpoint, name, key):
    _rewrite(checkpoint, name, lambda value: value.update({key: {"remote": "code"}}))
    with pytest.raises(FactContractError, match="remote_or_custom_code_forbidden"):
        connection.inventory_checkpoint(checkpoint)


@pytest.mark.parametrize("patch, code", [
    ({"id2label": {"0": "TS", "1": "TS", "2": "S2", "3": "S3"}}, "label_map"),
    ({"id2label": {"00": "TS", "1": "S1", "2": "S2", "3": "S3"}}, "label_map"),
    ({"id2label": {"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2", "3": "LABEL_3"}}, "label_map"),
    ({"label2id": {"TS": 1, "S1": 0, "S2": 2, "S3": 3}}, "label_inverse"),
    ({"num_labels": True}, "label_count"), ({"num_labels": 5}, "label_count"),
    ({"architectures": ["RemoteModel"]}, "architecture"), ({"model_type": "bert"}, "architecture"),
    ({"dtype": "float16"}, "dtype"), ({"hidden_size": True}, "dimension"),
    ({"max_position_embeddings": 1024}, "dimension"), ({"pad_token_id": 9}, "pad_token"),
])
def test_config_ambiguities_fail_closed(checkpoint, patch, code):
    _rewrite(checkpoint, "config.json", lambda value: value.update(patch))
    with pytest.raises(FactContractError, match=code):
        connection.inventory_checkpoint(checkpoint)


@pytest.mark.parametrize("patch, code", [({"tokenizer_class": "CustomTokenizer"}, "tokenizer_class"),
    ({"model_max_length": 1024}, "token_limit"), ({"model_max_length": True}, "token_limit"),
    ({"pad_token": "missing"}, "tokenizer_pad"), ({"pad_token": None}, "tokenizer_pad")])
def test_tokenizer_config_ambiguities_rejected(checkpoint, patch, code):
    _rewrite(checkpoint, "tokenizer_config.json", lambda value: value.update(patch))
    with pytest.raises(FactContractError, match=code):
        connection.inventory_checkpoint(checkpoint)


@pytest.mark.parametrize("case", ["short", "huge", "bad_json", "overlap", "trailing_payload", "dtype", "shape"])
def test_weight_header_and_payload_fail_closed(checkpoint, case):
    path = checkpoint/"model.safetensors"
    raw = path.read_bytes()
    if case == "short":
        path.write_bytes(b"x")
    elif case == "huge":
        path.write_bytes(struct.pack("<Q", 2**63)+raw[8:])
    elif case == "bad_json":
        path.write_bytes(struct.pack("<Q", 1)+b"x"+raw[9:])
    elif case == "trailing_payload":
        path.write_bytes(raw+b"\0")
    else:
        length = struct.unpack("<Q", raw[:8])[0]
        header = json.loads(raw[8:8+length])
        if case == "overlap":
            header["classifier.weight"]["data_offsets"] = [0, 64]
        elif case == "dtype":
            header["classifier.weight"]["dtype"] = "F16"
        else:
            header["classifier.weight"]["shape"] = [2, 8]
        _write_weights(path, header)
    with pytest.raises(FactContractError):
        connection.inventory_checkpoint(checkpoint)


def test_oversized_registered_smoke_rejected_without_truncation(checkpoint):
    _rewrite(checkpoint, "config.json", lambda value: value.update({"max_position_embeddings": 8}))
    _rewrite(checkpoint, "tokenizer_config.json", lambda value: value.update({"model_max_length": 8}))
    with pytest.raises(FactContractError, match="smoke_input_oversized_or_truncated"):
        connection.inventory_checkpoint(checkpoint)


def test_registry_cannot_be_replaced_by_benchmark_texts(checkpoint, monkeypatch):
    monkeypatch.setattr(connection, "SMOKE_TEXTS", ("benchmark document", "another benchmark"))
    with pytest.raises(FactContractError, match="smoke_registry_changed"):
        connection.inventory_checkpoint(checkpoint)


def test_smoke_no_arbitrary_input_parameter(checkpoint):
    with pytest.raises(TypeError):
        connection.run_connection_smoke(checkpoint, expected_checkpoint_sha256="x", texts=["external benchmark"])


def test_expected_digest_checked_before_any_loader_call(checkpoint, monkeypatch):
    calls = _fake_runtime(monkeypatch)
    with pytest.raises(FactContractError, match="expected_identity_mismatch"):
        connection.run_connection_smoke(checkpoint, expected_checkpoint_sha256="a"*64)
    assert calls == []


def test_fake_smoke_exact_four_calls_no_real_forward_claim(checkpoint, monkeypatch):
    calls = _fake_runtime(monkeypatch)
    result = _smoke(checkpoint)
    assert len(calls) == result["callback_calls"] == 4
    assert calls[0] == calls[1] and calls[2] == calls[3]
    assert result["runtime_kind"] == "test_double" and not result["model_forward_executed"]
    assert result["model_forward_calls"] == result["benchmark_documents_used"] == 0
    assert result["all_logits_finite"] and all(r["repeat_exact"] for r in result["results"])
    assert not result["accuracy_measured"] and not result["ground_truth_answers_present"]
    assert all(result[k] is False for k in FLAGS)


@pytest.mark.parametrize("logits", [[0, 1, float("nan"), 0], [float("inf"), 0, 0, 0], [0, 1],
    [0, True, 1, 0], [0, "1", 0, 0], [[0, 1, 2, 3]]])
def test_invalid_or_nonfinite_logits_rejected(checkpoint, monkeypatch, logits):
    _fake_runtime(monkeypatch, logits=logits)
    with pytest.raises(FactContractError, match="nonfinite_or_invalid_logits"):
        _smoke(checkpoint)


def test_repeatability_failure_is_not_success(checkpoint, monkeypatch):
    _fake_runtime(monkeypatch, logits=lambda count: [0, count*0.1, 0, 0])
    with pytest.raises(FactContractError, match="repeatability_failed"):
        _smoke(checkpoint)


def test_changed_checkpoint_after_forward_rejected(checkpoint, monkeypatch):
    def mutate(root, count):
        if count == 4:
            (root/"config.json").write_text((root/"config.json").read_text(encoding="utf-8")+" ", encoding="utf-8")
    _fake_runtime(monkeypatch, mutate=mutate)
    with pytest.raises(FactContractError, match="checkpoint_or_runtime_metadata_changed"):
        _smoke(checkpoint)


@pytest.mark.parametrize("kind", ["sha", "label", "kind"])
def test_forged_runtime_identity_rejected(checkpoint, monkeypatch, kind):
    def loader(root, inventory):
        return connection._Runtime("unknown" if kind == "kind" else "test_double",
            "a"*64 if kind == "sha" else inventory["model_sha256"],
            [] if kind == "label" else inventory["label_order"], lambda _: [0, 1, 0, 0])
    monkeypatch.setattr(connection, "_load_local_runtime", loader)
    with pytest.raises(FactContractError, match="runtime_identity|runtime_kind"):
        _smoke(checkpoint)


def test_network_attempt_fails_and_original_socket_functions_restored(checkpoint, monkeypatch):
    originals = (socket.create_connection, socket.getaddrinfo, socket.socket.connect)
    def loader(root, inventory):
        socket.getaddrinfo("example.invalid", 443)
    monkeypatch.setattr(connection, "_load_local_runtime", loader)
    with pytest.raises(FactContractError, match="network_attempt_blocked"):
        _smoke(checkpoint)
    assert originals == (socket.create_connection, socket.getaddrinfo, socket.socket.connect)


def test_local_loader_passes_safe_options_and_uses_cpu_eval(checkpoint, monkeypatch):
    calls = {}
    class Tensor:
        shape = (1, 4)
        def detach(self):
            return self
        def cpu(self):
            return self
        def tolist(self):
            return [[0.1, 0.2, 0.3, 0.4]]
    class Model:
        training = True
        config = SimpleNamespace(id2label={0: "TS", 1: "S1", 2: "S2", 3: "S3"}, label2id={"TS": 0, "S1": 1, "S2": 2, "S3": 3})
        def to(self, device):
            calls["device"] = device
        def eval(self):
            self.training = False
        def parameters(self):
            return [SimpleNamespace(device=SimpleNamespace(type="cpu"), dtype="float32")]
        def __call__(self, **kwargs):
            calls["forward_payload"] = kwargs
            return SimpleNamespace(logits=Tensor())
    def load(path, **kwargs):
        calls["path"], calls["load"] = path, kwargs
        return Model(), {"missing_keys": [], "unexpected_keys": [], "mismatched_keys": [], "error_msgs": []}
    fake_torch = SimpleNamespace(float32="float32", long="long", inference_mode=nullcontext,
        tensor=lambda value, **kwargs: (value, kwargs))
    fake_transformers = SimpleNamespace(DebertaV2Config=SimpleNamespace(from_dict=lambda value: value),
        DebertaV2ForSequenceClassification=SimpleNamespace(from_pretrained=load))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    inventory = connection.inventory_checkpoint(checkpoint)
    runtime = connection._load_local_runtime(checkpoint, inventory)
    assert calls["load"]["local_files_only"] is True
    assert calls["load"]["trust_remote_code"] is False
    assert calls["load"]["use_safetensors"] is calls["load"]["weights_only"] is True
    assert calls["load"]["output_loading_info"] is True and calls["load"]["dtype"] == "float32"
    assert calls["device"] == "cpu" and calls["path"] == str(checkpoint)
    assert runtime.forward({"input_ids": [2, 1, 3], "attention_mask": [1, 1, 1]}) == [0.1, 0.2, 0.3, 0.4]
    assert calls["forward_payload"]["input_ids"][1] == {"dtype": "long", "device": "cpu"}


def test_cli_inventory_exclusive_no_smoke_execution(checkpoint, tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("no smoke execution")
    monkeypatch.setattr(cli, "run_connection_smoke", forbidden)
    out = tmp_path/"inventory-output"
    args = ["inventory", "--model-dir", str(checkpoint), "--out", str(out)]
    assert cli.main(args) == 0
    result = json.loads((out/"result.json").read_text(encoding="utf-8"))
    assert result["model_forward_executed"] is False
    assert cli.main(args) == 1 and "connection_output_exists" in capsys.readouterr().out


def test_cli_bad_smoke_identity_no_output_and_no_false_zero_claim(checkpoint, tmp_path, monkeypatch, capsys):
    calls = _fake_runtime(monkeypatch)
    out = tmp_path/"not-created"
    assert cli.main(["smoke", "--model-dir", str(checkpoint), "--out", str(out),
                     "--expected-checkpoint-sha256", "a"*64]) == 1
    assert calls == [] and not out.exists()
    message = json.loads(capsys.readouterr().out)
    assert message["model_forward_execution_state"] == "not_reported_due_failure"


@pytest.mark.parametrize("kind", ["model_child", "model_alias", "model_itself", "frozen_child", "frozen_deep", "frozen_alias"])
def test_output_protection_before_inventory_or_smoke(checkpoint, tmp_path, monkeypatch, capsys, kind):
    frozen = tmp_path/"frozen-reference"
    frozen.mkdir()
    (frozen/"manifest.json").write_text("{}", encoding="utf-8")
    destinations = {"model_child": checkpoint/"new", "model_alias": checkpoint/".."/checkpoint.name/"new",
        "model_itself": checkpoint, "frozen_child": frozen/"new", "frozen_deep": frozen/"nested"/"new",
        "frozen_alias": frozen/".."/frozen.name/"new"}
    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe output rejected before source reads/model execution")
    monkeypatch.setattr(cli, "inventory_checkpoint", forbidden)
    monkeypatch.setattr(cli, "run_connection_smoke", forbidden)
    out = destinations[kind]
    for command in ("inventory", "smoke"):
        args = [command, "--model-dir", str(checkpoint), "--out", str(out)]
        if command == "smoke":
            args += ["--expected-checkpoint-sha256", "a"*64]
        assert cli.main(args) == 1
        code = json.loads(capsys.readouterr().out)["code"]
        assert code in {"connection_output_inside_checkpoint", "connection_output_inside_frozen_pack"}
    assert not (checkpoint/"new").exists() and not (frozen/"new").exists()


def test_direct_writer_rejects_changed_source_before_output(checkpoint, tmp_path):
    result = connection.inventory_checkpoint(checkpoint)
    (checkpoint/"config.json").write_text((checkpoint/"config.json").read_text(encoding="utf-8")+" ", encoding="utf-8")
    out = tmp_path/"not-written"
    with pytest.raises(FactContractError, match="source_changed_before_output"):
        cli.write_result(out, result, model_dir=checkpoint,
            expected_script_sha256=connection._sha_file(connection.Path(cli.__file__))["sha256"])
    assert not out.exists()


def test_writer_rechecks_source_after_output(checkpoint, tmp_path, monkeypatch):
    result = connection.inventory_checkpoint(checkpoint)
    original = cli.inventory_checkpoint
    calls = []
    def changed(root):
        calls.append(root)
        value = original(root)
        if len(calls) == 2:
            value["files"]["config.json"]["sha256"] = "a"*64
        return value
    monkeypatch.setattr(cli, "inventory_checkpoint", changed)
    with pytest.raises(FactContractError, match="source_changed_during_output"):
        cli.write_result(tmp_path/"failed-pack", result, model_dir=checkpoint,
            expected_script_sha256=connection._sha_file(connection.Path(cli.__file__))["sha256"])
    assert len(calls) == 2
    assert (tmp_path/"failed-pack"/"result.json").exists()
    assert not (tmp_path/"failed-pack"/"manifest.json").exists()


def test_manifest_written_only_after_successful_final_recheck(checkpoint, tmp_path, monkeypatch):
    result = connection.inventory_checkpoint(checkpoint)
    original = cli.inventory_checkpoint
    calls = []
    out = tmp_path/"completed-pack"
    def checked(root):
        calls.append(root)
        assert not (out/"manifest.json").exists()
        if len(calls) == 2:
            assert (out/"result.json").exists()
        return original(root)
    monkeypatch.setattr(cli, "inventory_checkpoint", checked)
    cli.write_result(out, result, model_dir=checkpoint,
        expected_script_sha256=connection._sha_file(connection.Path(cli.__file__))["sha256"])
    assert len(calls) == 2 and (out/"manifest.json").exists()
