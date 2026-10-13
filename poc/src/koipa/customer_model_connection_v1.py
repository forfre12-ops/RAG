"""Local CPU connection smoke only: two registered neutral texts, no benchmark.

No arbitrary input path, dataset permission promotion, answer access or scoring.
Inventory reads artifact bytes without importing torch or loading model weights.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import math
import socket
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from koipa.customer_benchmark import FLAGS, strict_loads
from koipa.policy_facts import FactContractError, require, text_digest, value_digest

SCHEMA = "customer-local-model-connection-v1"
LABELS = {"TS", "S1", "S2", "S3"}
ARTIFACTS = ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors")
SMOKE_TEXTS = (
    "연결 확인용 문장입니다. 오늘 회의실의 시계와 달력을 확인하고 화면에 표시된 안내문을 읽습니다.",
    "이 문장은 로컬 모델 입력 연결을 점검하는 예시입니다. 책상 위 공책을 펼친 다음 첫 줄의 문장을 확인합니다.",
)
SMOKE_REGISTRY_SHA256 = value_digest(list(SMOKE_TEXTS))
MAX_TOKENS = 512
REPEAT_TOLERANCE = 1e-6


def _sha_file(path):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8*1024*1024), b""):
            digest.update(block)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), "connection_artifact_changed")
    return {"sha256": digest.hexdigest(), "bytes": after.st_size}


def _local_root(model_dir):
    supplied = Path(model_dir)
    require(not supplied.is_symlink() and supplied.is_dir(), "connection_local_directory_required")
    root = supplied.resolve()
    dangerous = {"adapter_config.json", "adapter_model.safetensors", "pytorch_model.bin",
                 "pytorch_model.bin.index.json", "model.safetensors.index.json"}
    names = {p.name for p in root.iterdir()}
    require(not names & dangerous and not any(n.casefold().endswith(".py") for n in names), "connection_unsupported_loader_artifact")
    require({p.name for p in root.glob("*.safetensors")} == {"model.safetensors"}, "connection_weight_files_ambiguous")
    for name in ARTIFACTS:
        path = root/name
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root), "connection_artifact_missing_or_linked")
    return root


def _json_file(path):
    require(path.stat().st_size <= 16*1024*1024, "connection_json_oversized")
    try:
        value = strict_loads(path.read_bytes().decode("utf-8"))
    except (ValueError, UnicodeError):
        raise FactContractError("connection_json_invalid") from None
    require(type(value) is dict, "connection_json_object_required")
    return value


def _labels(config):
    mapping, reverse = config.get("id2label"), config.get("label2id")
    require(type(mapping) is dict and set(mapping) == {"0", "1", "2", "3"}, "connection_label_map_invalid")
    require(all(type(v) is str for v in mapping.values()) and set(mapping.values()) == LABELS,
            "connection_label_map_invalid")
    order = [mapping[str(i)] for i in range(4)]
    require(type(reverse) is dict and set(reverse) == LABELS and
            all(type(reverse[label]) is int and reverse[label] == i for i, label in enumerate(order)),
            "connection_label_inverse_invalid")
    require("num_labels" not in config or type(config["num_labels"]) is int and config["num_labels"] == 4,
            "connection_label_count_invalid")
    return order


def _config(config, tokenizer_config):
    for value in (config, tokenizer_config):
        require(not set(value) & {"auto_map", "custom_pipelines", "quantization_config", "compression_config", "code_revision"},
                "connection_remote_or_custom_code_forbidden")
    require(config.get("model_type") == "deberta-v2" and
            config.get("architectures") == ["DebertaV2ForSequenceClassification"], "connection_architecture_unsupported")
    require(config.get("dtype", config.get("torch_dtype")) == "float32", "connection_dtype_unsupported")
    for key, ceiling in (("hidden_size", 4096), ("vocab_size", 1000000), ("num_hidden_layers", 128),
                         ("max_position_embeddings", MAX_TOKENS)):
        require(type(config.get(key)) is int and 0 < config[key] <= ceiling, "connection_dimension_invalid")
    require(tokenizer_config.get("tokenizer_class") in {"BertTokenizer", "BertTokenizerFast"}, "connection_tokenizer_class_unsupported")
    limit = tokenizer_config.get("model_max_length")
    require(type(limit) is int and 0 < limit <= MAX_TOKENS and limit == config["max_position_embeddings"],
            "connection_token_limit_mismatch")
    require(type(config.get("pad_token_id")) is int and 0 <= config["pad_token_id"] < config["vocab_size"],
            "connection_pad_token_invalid")
    return _labels(config)


def _weight_header(path, config):
    size = path.stat().st_size
    with path.open("rb") as handle:
        lead = handle.read(8)
        require(len(lead) == 8, "connection_safetensors_header_invalid")
        length = struct.unpack("<Q", lead)[0]
        require(0 < length <= 8*1024*1024 and 8+length < size, "connection_safetensors_header_invalid")
        try:
            header = strict_loads(handle.read(length).decode("utf-8"))
        except (ValueError, UnicodeError):
            raise FactContractError("connection_safetensors_header_invalid") from None
    require(type(header) is dict and header.get("__metadata__") == {"format": "pt"}, "connection_safetensors_metadata_invalid")
    tensors = {k: v for k, v in header.items() if k != "__metadata__"}
    require(bool(tensors), "connection_safetensors_empty")
    intervals = []
    for name, tensor in tensors.items():
        require(type(name) is str and name and type(tensor) is dict and set(tensor) == {"dtype", "shape", "data_offsets"},
                "connection_tensor_header_invalid")
        shape, offsets = tensor["shape"], tensor["data_offsets"]
        require(tensor["dtype"] == "F32" and type(shape) is list and
            all(type(s) is int and s > 0 for s in shape), "connection_tensor_shape_or_dtype_invalid")
        require(type(offsets) is list and len(offsets) == 2 and all(type(o) is int and o >= 0 for o in offsets) and
            offsets[1]-offsets[0] == 4*math.prod(shape), "connection_tensor_offsets_invalid")
        intervals.append((offsets[0], offsets[1]))
    cursor = 0
    for begin, end in sorted(intervals):
        require(begin == cursor and end > begin, "connection_tensor_gap_or_overlap")
        cursor = end
    require(cursor == size-8-length, "connection_tensor_payload_size_invalid")
    expected = {"classifier.weight": [4, config["hidden_size"]], "classifier.bias": [4],
                "deberta.embeddings.word_embeddings.weight": [config["vocab_size"], config["hidden_size"]]}
    require(all(name in tensors and tensors[name]["shape"] == shape for name, shape in expected.items()),
            "connection_classifier_or_embedding_shape_mismatch")
    return {"tensor_count": len(tensors), "header_bytes": length, "header_sha256": value_digest(header),
        "dtype": "F32", "required_shapes": expected, "tensor_payload_bytes": cursor,
        "all_weight_values_finite_verified": False}


def _registered_inputs(root, config, tokenizer_config):
    require(len(SMOKE_TEXTS) == 2 and value_digest(list(SMOKE_TEXTS)) == SMOKE_REGISTRY_SHA256,
            "connection_smoke_registry_changed")
    from tokenizers import Tokenizer
    data = (root/"tokenizer.json").read_bytes()
    try:
        tokenizer = Tokenizer.from_str(data.decode("utf-8"))
    except Exception:
        raise FactContractError("connection_tokenizer_invalid") from None
    tokenizer.no_padding()
    tokenizer.no_truncation()
    require(tokenizer.get_vocab_size(with_added_tokens=True) == config["vocab_size"], "connection_tokenizer_vocab_mismatch")
    pad = tokenizer_config.get("pad_token")
    require(type(pad) is str and tokenizer.token_to_id(pad) == config["pad_token_id"], "connection_tokenizer_pad_mismatch")
    values = []
    for index, text in enumerate(SMOKE_TEXTS):
        encoded = tokenizer.encode(text, add_special_tokens=True)
        require(not encoded.overflowing and 0 < len(encoded.ids) <= config["max_position_embeddings"],
                "connection_smoke_input_oversized_or_truncated")
        require(all(type(i) is int and 0 <= i < config["vocab_size"] for i in encoded.ids), "connection_token_id_out_of_range")
        payload = {"input_ids": encoded.ids, "attention_mask": encoded.attention_mask}
        require(len(payload["input_ids"]) == len(payload["attention_mask"]) and all(i == 1 for i in payload["attention_mask"]),
                "connection_attention_mask_invalid")
        values.append({"smoke_id": f"connection-neutral-{index+1}", "text": text, "text_sha256": text_digest(text),
            "tokens_with_special": len(encoded.ids), "token_payload_sha256": value_digest(payload), "payload": payload})
    return values


def _versions():
    result = {}
    for name in ("torch", "transformers", "tokenizers", "safetensors"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def inventory_checkpoint(model_dir):
    """Content-derived identity; no model load, model forward or benchmark read."""
    root = _local_root(model_dir)
    files = {name: _sha_file(root/name) for name in ARTIFACTS}
    config, tokenizer_config = _json_file(root/"config.json"), _json_file(root/"tokenizer_config.json")
    order = _config(config, tokenizer_config)
    header = _weight_header(root/"model.safetensors", config)
    views = _registered_inputs(root, config, tokenizer_config)
    require(files == {name: _sha_file(root/name) for name in ARTIFACTS}, "connection_artifact_changed")
    return {**FLAGS, "schema_version": SCHEMA, "status": "local_checkpoint_inventory_only",
        "checkpoint_sha256": value_digest(files), "model_sha256": files["model.safetensors"]["sha256"],
        "files": files, "source_module_sha256": _sha_file(Path(__file__))["sha256"],
        "architecture": config["architectures"][0], "model_type": config["model_type"],
        "hidden_size": config["hidden_size"], "layers": config["num_hidden_layers"],
        "label_order": order, "max_tokens_with_special": config["max_position_embeddings"],
        "weight_header": header, "registered_smoke_inputs": views, "smoke_registry_sha256": SMOKE_REGISTRY_SHA256,
        "installed_package_versions": _versions(), "model_forward_executed": False, "model_forward_calls": 0,
        "benchmark_documents_used": 0, "accuracy_measured": False, "ground_truth_answers_present": False,
        "ignored_files_not_loaded": sorted(p.name for p in root.iterdir() if p.is_file() and p.name not in ARTIFACTS),
        "tokenizer_saved_local_files_only": tokenizer_config.get("local_files_only"),
        "runtime_loader_local_files_only": True, "runtime_loader_trust_remote_code": False,
        "runtime_loader_safetensors_only": True, "calibration_or_operating_point_applied": False}


@dataclass
class _Runtime:
    kind: str
    model_sha256: str
    label_order: list[str]
    forward: object


def _load_local_runtime(root, inventory):
    """Only invoked by explicit run_connection_smoke, not by inventory."""
    import torch
    from transformers import DebertaV2Config, DebertaV2ForSequenceClassification

    config = DebertaV2Config.from_dict(_json_file(root/"config.json"))
    model, info = DebertaV2ForSequenceClassification.from_pretrained(str(root), config=config,
        local_files_only=True, trust_remote_code=False, use_safetensors=True, weights_only=True,
        dtype=torch.float32, output_loading_info=True)
    require(not any(info.get(key) for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")),
            "connection_weights_incomplete_or_mismatched")
    actual = {str(k): v for k, v in model.config.id2label.items()}
    order = _labels({"id2label": actual, "label2id": model.config.label2id})
    require(order == inventory["label_order"], "connection_loaded_label_mapping_changed")
    model.to("cpu")
    model.eval()
    require(not model.training and all(p.device.type == "cpu" and p.dtype == torch.float32 for p in model.parameters()),
            "connection_cpu_eval_float32_required")

    def forward(payload):
        tensors = {key: torch.tensor([value], dtype=torch.long, device="cpu") for key, value in payload.items()}
        with torch.inference_mode():
            logits = model(**tensors).logits
        require(tuple(logits.shape) == (1, 4), "connection_logit_shape_invalid")
        return logits.detach().cpu().tolist()[0]

    return _Runtime("local_transformers_cpu", inventory["model_sha256"], order, forward)


@contextmanager
def _deny_network():
    # Scoped to the standalone smoke process. Restore even when model loading fails.
    targets = [(socket, "create_connection"), (socket, "getaddrinfo"), (socket, "gethostbyname"),
               (socket, "gethostbyname_ex"), (socket.socket, "connect"), (socket.socket, "connect_ex")]
    saved = [(owner, name, getattr(owner, name)) for owner, name in targets]
    def denied(*args, **kwargs):
        raise FactContractError("connection_network_attempt_blocked")
    try:
        for owner, name, _ in saved:
            setattr(owner, name, denied)
        yield
    finally:
        for owner, name, previous in saved:
            setattr(owner, name, previous)


def _run_fixed_forwards(root, before):
    runtime = _load_local_runtime(root, before)
    require(runtime.model_sha256 == before["model_sha256"] and runtime.label_order == before["label_order"],
            "connection_runtime_identity_mismatch")
    require(runtime.kind in {"local_transformers_cpu", "test_double"}, "connection_runtime_kind_invalid")
    results, calls = [], 0
    for view in before["registered_smoke_inputs"]:
        logits = []
        for _ in range(2):
            values = runtime.forward({k: list(v) for k, v in view["payload"].items()})
            calls += 1
            require(type(values) is list and len(values) == 4 and
                all(type(x) in (float, int) and math.isfinite(x) for x in values), "connection_nonfinite_or_invalid_logits")
            logits.append([float(x) for x in values])
        difference = max(abs(a-b) for a, b in zip(*logits, strict=True))
        require(difference <= REPEAT_TOLERANCE, "connection_repeatability_failed")
        winner = max(range(4), key=lambda i: logits[0][i])
        results.append({"smoke_id": view["smoke_id"], "text_sha256": view["text_sha256"],
            "token_payload_sha256": view["token_payload_sha256"], "tokens_with_special": view["tokens_with_special"],
            "logits_repetitions": logits, "max_absolute_repeat_difference": difference,
            "repeat_exact": logits[0] == logits[1], "argmax_column": winner,
            "mapped_output_label_not_ground_truth": before["label_order"][winner]})
    return runtime.kind, results, calls


def run_connection_smoke(model_dir, *, expected_checkpoint_sha256):
    """Four forwards, two registered neutral inputs twice. No arbitrary texts API.

    Run in a dedicated process: its scoped network guard temporarily patches
    socket connection/DNS functions and is not a shared server runtime facility.
    """
    root = _local_root(model_dir)
    before = inventory_checkpoint(root)
    require(type(expected_checkpoint_sha256) is str and before["checkpoint_sha256"] == expected_checkpoint_sha256,
            "connection_expected_identity_mismatch")
    with _deny_network():
        runtime_kind, results, calls = _run_fixed_forwards(root, before)
    after = inventory_checkpoint(root)
    require(value_digest(before) == value_digest(after), "connection_checkpoint_or_runtime_metadata_changed")
    real = runtime_kind == "local_transformers_cpu"
    return {**FLAGS, "schema_version": SCHEMA, "status": "local_connection_smoke_only" if real else "test_double_smoke_only",
        "checkpoint_sha256": before["checkpoint_sha256"], "model_sha256": before["model_sha256"],
        "inventory_sha256": value_digest(before), "source_module_sha256": before["source_module_sha256"],
        "smoke_registry_sha256": SMOKE_REGISTRY_SHA256, "runtime_kind": runtime_kind,
        "installed_package_versions": _versions(), "device": "cpu", "batch_size": 1,
        "smoke_documents": 2, "repetitions_per_document": 2, "callback_calls": calls,
        "model_forward_executed": real, "model_forward_calls": calls if real else 0,
        "all_logits_finite": True, "repeatability_tolerance": REPEAT_TOLERANCE,
        "label_order": before["label_order"], "results": results,
        "network_guard": "socket_connect_and_dns_blocked_during_model_load_and_forward",
        "benchmark_documents_used": 0, "accuracy_measured": False, "ground_truth_answers_present": False,
        "training_performed": False, "calibration_or_operating_point_applied": False,
        "scope": "Raw local CPU connectivity only; no benchmark evaluation or release permission."}
