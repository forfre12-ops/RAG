from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import p1_train_classifier as p1_cli
from scripts.finalize_proxy_classifier import (
    discover_checkpoints,
    verify_materialized_training_run,
    verify_proxy_training_execution,
)
from lloydk.modules.m4_training.trainer import (
    TrainSpec,
    _proxy_materialization_audit,
    _write_proxy_training_execution,
)
from lloydk.modules.m4_training.chunk_expand import expand_records_evidence_aware
from lloydk.modules.m5_inference.pipeline import InferencePipeline
from lloydk import proxy_model_comparison as comparison
from lloydk.proxy_training_finalization import (
    DocumentWindowLogits,
    ProxyTrainingFinalizationError,
    aggregate_trace_probabilities,
    assert_document_splits_disjoint,
    classification_metrics,
    collect_document_window_logits,
    evaluate_checkpoint_traces,
    fit_document_temperature,
    fit_escalation_operating_point,
    load_document_rows,
    predict_with_escalation,
    select_checkpoint,
)


LABELS = ("TS", "S1", "S2", "S3")


def _trace(
    doc_id: str,
    label: str,
    logits: list[list[float]],
    weights: list[float] | None = None,
) -> DocumentWindowLogits:
    return DocumentWindowLogits(
        doc_id=doc_id,
        document_family_id=f"family-{doc_id}",
        label=label,
        label_idx=LABELS.index(label),
        label_order=LABELS,
        window_logits=tuple(tuple(float(value) for value in row) for row in logits),
        window_weights=tuple(weights or [100.0] * len(logits)),
        char_chunk_count=len(logits),
        tokenizer_mode_counts=(("fast_overflow", 1),),
    )


def _write_materialized_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "training-run"
    run_dir.mkdir()
    artifacts: dict[str, dict[str, object]] = {}
    complete_artifacts: dict[str, dict[str, object]] = {}
    train_rows: list[dict[str, object]] = []
    for split_prefix, filename in (
        ("train", "train_documents.jsonl"),
        ("validation", "validation_documents.jsonl"),
        ("calibration", "calibration_documents.jsonl"),
    ):
        rows = [
            {
                "doc_id": f"{split_prefix}-{grade}",
                "document_family_id": f"family-{split_prefix}-{grade}",
                "text": f"{split_prefix} {grade} 고유 본문",
                "label": grade,
            }
            for grade in LABELS
        ]
        if split_prefix == "train":
            for row in rows:
                if row["label"] in {"TS", "S1"}:
                    row["evidence_card"] = {
                        "factors": {
                            "secret": {
                                "spans": [
                                    {
                                        "start": 0,
                                        "end": len(row["text"]),
                                        "quote": row["text"],
                                    }
                                ]
                            }
                        }
                    }
            train_rows = rows
        payload = b"".join(
            (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
            for row in rows
        )
        path = run_dir / filename
        path.write_bytes(payload)
        import hashlib

        digest = hashlib.sha256(payload).hexdigest()
        key = filename.removesuffix(".jsonl")
        artifacts[key] = {"path": filename, "sha256": digest, "records": 4}
        complete_artifacts[key] = {"sha256": digest, "records": 4}
    chunk_rows = expand_records_evidence_aware(train_rows)
    chunk_payload = b"".join(
        (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        for row in chunk_rows
    )
    chunk_path = run_dir / "train_chunks.jsonl"
    chunk_path.write_bytes(chunk_payload)
    import hashlib

    chunk_hash = hashlib.sha256(chunk_payload).hexdigest()
    artifacts["train_chunks"] = {
        "path": "train_chunks.jsonl",
        "sha256": chunk_hash,
        "records": len(chunk_rows),
    }
    complete_artifacts["train_chunks"] = {
        "sha256": chunk_hash,
        "records": len(chunk_rows),
    }
    manifest = {
        "schema_version": "proxy-training-run-v1",
        "run_id": "training-test",
        "status": "complete",
        "artifacts": artifacts,
        "leakage_checks": {
            "family_overlap_with_frozen_or_blocked": 0,
            "normalized_text_hash_overlap_with_frozen_or_blocked": 0,
            "doc_id_overlap_with_frozen_or_blocked": 0,
            "family_overlap_across_splits": 0,
            "doc_id_overlap_across_splits": 0,
            "normalized_text_hash_overlap_across_splits": 0,
            "train_chunk_source_doc_id_overlap_with_validation_or_calibration": 0,
            "train_chunk_family_overlap_with_validation_or_calibration": 0,
            "train_chunk_text_hash_overlap_with_validation_or_calibration": 0,
            "frozen_records_in_splits": 0,
        },
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    (run_dir / "manifest.json").write_bytes(manifest_bytes)
    import hashlib

    complete = {
        "schema_version": "proxy-training-run-v1",
        "run_id": "training-test",
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "artifacts": complete_artifacts,
    }
    (run_dir / "COMPLETE").write_text(
        json.dumps(complete, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return run_dir


def test_document_aggregation_recomputes_window_softmax_before_severe_max():
    trace = _trace(
        "late-signal",
        "TS",
        [
            [-3.0, -3.0, 0.0, 5.0],
            [8.0, -3.0, -3.0, -3.0],
        ],
        [1_536.0, 30.0],
    )

    cold = aggregate_trace_probabilities(trace, temperature=1.0)
    warm = aggregate_trace_probabilities(trace, temperature=4.0)

    assert max(cold, key=cold.get) == "TS"
    assert cold["TS"] > warm["TS"]
    assert sum(cold.values()) == pytest.approx(1.0)
    assert sum(warm.values()) == pytest.approx(1.0)


def test_validation_and_calibration_must_be_family_disjoint():
    validation = [
        {"doc_id": "v1", "document_family_id": "shared", "text": "검증 본문", "label": "TS"}
    ]
    calibration = [
        {"doc_id": "c1", "document_family_id": "shared", "text": "보정 본문", "label": "S1"}
    ]

    with pytest.raises(ProxyTrainingFinalizationError, match="leakage"):
        assert_document_splits_disjoint(validation, calibration)


def test_loader_rejects_pre_chunked_validation_row(tmp_path: Path):
    path = tmp_path / "validation_documents.jsonl"
    path.write_text(
        json.dumps(
            {
                "doc_id": "d1",
                "document_family_id": "f1",
                "text": "본문",
                "label": "TS",
                "chunk_id": "d1:0",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProxyTrainingFinalizationError, match="not a document"):
        load_document_rows(path, purpose="validation split")


def test_temperature_fit_uses_documents_and_never_worsens_nll():
    traces = (
        _trace("ts", "TS", [[7.0, 0.0, 0.0, 0.0]]),
        _trace("s1", "S1", [[7.0, 1.0, 0.0, 0.0]]),
        _trace("s2", "S2", [[0.0, 0.0, 7.0, 0.0]]),
        _trace("s3", "S3", [[0.0, 0.0, 7.0, 1.0]]),
    )

    report = fit_document_temperature(traces, steps=50, fail_on_boundary=False)

    assert report["fit_unit"] == "document"
    assert report["n_documents"] == 4
    assert report["n_windows"] == 4
    assert float(report["temperature"]) > 1.0
    assert float(report["nll_after"]) <= float(report["nll_before"])
    assert report["search_boundary_hit"] is True

    with pytest.raises(ProxyTrainingFinalizationError, match="search boundary"):
        fit_document_temperature(traces, steps=50)


def test_tau_fit_uses_temperature_scaled_document_scores_and_meets_target():
    traces = (
        _trace("ts", "TS", [[2.0, 1.0, 0.0, 0.0]]),
        _trace("s1", "S1", [[0.9, 1.2, 0.8, 0.0]]),
        _trace("s2", "S2", [[0.2, 0.1, 2.0, 0.3]]),
        _trace("s3", "S3", [[0.1, 0.1, 0.2, 2.0]]),
    )

    report = fit_escalation_operating_point(
        traces,
        temperature=2.0,
        fnr_target=0.0,
    )

    assert report["selection_split"] == "calibration_documents"
    assert report["temperature"] == 2.0
    assert report["selected_metrics"]["fnr_high"] == 0.0
    assert report["candidate_count"] > 1


def test_escalation_prediction_matches_m5_severity_order():
    scores = {"TS": 0.24, "S1": 0.31, "S2": 0.35, "S3": 0.10}
    assert predict_with_escalation(scores, tau=None) == "S2"
    assert predict_with_escalation(scores, tau=0.30) == "S1"
    assert predict_with_escalation(scores, tau=0.20) == "TS"


def test_checkpoint_selection_uses_balanced_metric_then_f1_then_nll():
    selected, metrics = select_checkpoint(
        [
            ("checkpoint-100", {"fnr_high_balanced": 0.2, "f1_macro": 0.8, "nll": 0.7}),
            ("checkpoint-200", {"fnr_high_balanced": 0.1, "f1_macro": 0.7, "nll": 0.8}),
            ("checkpoint-300", {"fnr_high_balanced": 0.1, "f1_macro": 0.8, "nll": 0.9}),
            ("checkpoint-400", {"fnr_high_balanced": 0.1, "f1_macro": 0.8, "nll": 0.6}),
        ]
    )
    assert selected == "checkpoint-400"
    assert metrics["nll"] == 0.6


def test_evaluation_counts_documents_not_windows():
    traces = (
        _trace("one", "TS", [[-2, 0, 0, 3], [8, -2, -2, -2]], [1_500, 20]),
        _trace("two", "S3", [[0, 0, 0, 5], [0, 0, 0, 4]], [1_500, 500]),
    )
    report = evaluate_checkpoint_traces(traces)
    assert report["documents"] == 2
    assert report["evaluation_unit"] == "document"
    assert sum(sum(row) for row in report["confusion_matrix"]) == 2


def test_balanced_metric_penalizes_all_ts_predictor():
    metrics = classification_metrics(
        ["TS", "S1", "S2", "S3"],
        ["TS", "TS", "TS", "TS"],
    )
    assert metrics["fnr_high"] == 0.0
    assert metrics["over_class_rate"] == 1.0
    assert metrics["degenerate_penalty"] == 1.0
    assert metrics["fnr_high_balanced"] == 2.0


def test_collection_fails_closed_when_fast_overflow_falls_back(monkeypatch):
    torch = pytest.importorskip("torch")

    class Encoding(dict):
        def to(self, _device):
            return self

    class Tokenizer:
        is_fast = True

    class Model:
        config = SimpleNamespace(id2label={0: "TS", 1: "S1", 2: "S2", 3: "S3"})
        training = False

        def to(self, _device):
            return self

        def eval(self):
            return self

        def __call__(self, **_kwargs):
            return SimpleNamespace(logits=torch.tensor([[1.0, 0.0, 0.0, 0.0]]))

    monkeypatch.setattr(
        "lloydk.proxy_training_finalization._encode_serving_windows",
        lambda *_args, **_kwargs: (Encoding(input_ids=torch.tensor([[1]])), [0], "fast_overflow_error_truncation"),
    )
    rows = [
        {"doc_id": "d1", "document_family_id": "f1", "text": "본문", "label": "TS"}
    ]
    with pytest.raises(ProxyTrainingFinalizationError, match="refusing truncation fallback"):
        collect_document_window_logits(Model(), Tokenizer(), rows, device="cpu")


def test_materialized_run_rebinds_independent_artifact_hashes(tmp_path: Path):
    run_dir = _write_materialized_run(tmp_path)
    validation, calibration, audit = verify_materialized_training_run(run_dir)
    assert len(validation) == 4
    assert len(calibration) == 4
    assert audit["separation"] == {
        "doc_id_overlap": 0,
        "document_family_id_overlap": 0,
        "normalized_text_hash_overlap": 0,
        "train_chunk_source_doc_id_overlap": 0,
        "train_chunk_document_family_id_overlap": 0,
        "train_chunk_normalized_text_hash_overlap": 0,
    }

    with (run_dir / "calibration_documents.jsonl").open("ab") as handle:
        handle.write(b"\n")
    with pytest.raises(ProxyTrainingFinalizationError, match="hash mismatch"):
        verify_materialized_training_run(run_dir)


def test_materialized_run_recomputes_train_evaluation_leakage(tmp_path: Path):
    run_dir = _write_materialized_run(tmp_path)
    train_path = run_dir / "train_documents.jsonl"
    validation = json.loads(
        (run_dir / "validation_documents.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    train_rows = [
        json.loads(line)
        for line in train_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    train_rows[0]["doc_id"] = validation["doc_id"]
    train_payload = b"".join(
        (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        for row in train_rows
    )
    train_path.write_bytes(train_payload)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib

    digest = hashlib.sha256(train_payload).hexdigest()
    manifest["artifacts"]["train_documents"]["sha256"] = digest
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    complete_path = run_dir / "COMPLETE"
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    complete["artifacts"]["train_documents"]["sha256"] = digest
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ProxyTrainingFinalizationError, match="source_doc_id coverage|leakage check mismatch"
    ):
        verify_materialized_training_run(run_dir)


def test_materialized_run_rejects_rehashed_forged_train_chunk(tmp_path: Path):
    run_dir = _write_materialized_run(tmp_path)
    chunk_path = run_dir / "train_chunks.jsonl"
    chunks = [
        json.loads(line)
        for line in chunk_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    chunks[0]["text"] = "forged frozen-looking training chunk"
    chunk_payload = b"".join(
        (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        for row in chunks
    )
    chunk_path.write_bytes(chunk_payload)
    import hashlib

    digest = hashlib.sha256(chunk_payload).hexdigest()
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["train_chunks"]["sha256"] = digest
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    complete_path = run_dir / "COMPLETE"
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    complete["artifacts"]["train_chunks"]["sha256"] = digest
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProxyTrainingFinalizationError, match="deterministic expansion mismatch"):
        verify_materialized_training_run(run_dir)


def test_materialized_run_rejects_unknown_leakage_check_key(tmp_path: Path):
    run_dir = _write_materialized_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["leakage_checks"]["ignored_check"] = 0
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    import hashlib

    complete_path = run_dir / "COMPLETE"
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProxyTrainingFinalizationError, match="leakage check keys"):
        verify_materialized_training_run(run_dir)


def test_checkpoint_discovery_is_numeric_and_rejects_empty_root(tmp_path: Path):
    root = tmp_path / "checkpoints"
    root.mkdir()
    (root / "not-a-checkpoint").mkdir()
    with pytest.raises(ProxyTrainingFinalizationError, match="no checkpoint"):
        discover_checkpoints(root)

    (root / "checkpoint-100").mkdir()
    (root / "checkpoint-20").mkdir()
    assert [path.name for path in discover_checkpoints(root)] == [
        "checkpoint-20",
        "checkpoint-100",
    ]


@pytest.mark.parametrize("tau", [None, 0.27])
def test_m5_locks_bundle_temperature_and_tau_from_same_trace(
    tmp_path: Path, monkeypatch, tau: float | None
):
    trace_hash = "a" * 64
    input_hash = "c" * 64
    manifest_hash = "d" * 64
    contract_hash = comparison.serving_aggregation_contract(
        apply_bundle_operating_point=True,
        require_fast_overflow=True,
    )["contract_sha256"]
    (tmp_path / "temperature.json").write_text(
        json.dumps(
            {
                "schema_version": "proxy-document-temperature-v1",
                "status": "complete",
                "fit_unit": "document",
                "temperature": 1.75,
                "calibration_trace_sha256": trace_hash,
                "calibration_input_sha256": input_hash,
                "training_run_manifest_sha256": manifest_hash,
                "serving_aggregation_contract_sha256": contract_hash,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "operating_point.json").write_text(
        json.dumps(
            {
                "schema_version": "proxy-operating-point-v1",
                "status": "complete",
                "selection_split": "calibration_documents",
                "selection_unit": "document",
                "temperature": 1.75,
                "classifier_escalation_tau": tau,
                "calibration_trace_sha256": trace_hash,
                "calibration_input_sha256": input_hash,
                "training_run_manifest_sha256": manifest_hash,
                "serving_aggregation_contract_sha256": contract_hash,
            }
        ),
        encoding="utf-8",
    )
    pipe = object.__new__(InferencePipeline)
    pipe.model_dir = tmp_path
    pipe._model_temperature = None
    pipe._model_escalation_tau = None
    pipe._locked_operating_point = False
    pipe.calibrated = None
    monkeypatch.setattr(
        comparison,
        "_verify_finalized_model_bundle",
        lambda _: {"serving_aggregation_contract_sha256": contract_hash},
    )
    assert pipe._apply_bundle_calibration() == "bundle"
    assert pipe._apply_bundle_operating_point() == "bundle_locked"

    from lloydk import config

    monkeypatch.setattr(config.settings, "classifier_temperature", 3.0)
    monkeypatch.setattr(config.settings, "classifier_escalation_tau", 0.30)
    assert pipe._temperature == pytest.approx(1.75)
    assert pipe._escalation_tau == tau


def test_m5_rejects_operating_point_from_different_temperature(
    tmp_path: Path, monkeypatch
):
    trace_hash = "b" * 64
    input_hash = "c" * 64
    manifest_hash = "d" * 64
    contract_hash = comparison.serving_aggregation_contract(
        apply_bundle_operating_point=True,
        require_fast_overflow=True,
    )["contract_sha256"]
    (tmp_path / "temperature.json").write_text(
        json.dumps(
            {
                "schema_version": "proxy-document-temperature-v1",
                "status": "complete",
                "fit_unit": "document",
                "temperature": 1.5,
                "calibration_trace_sha256": trace_hash,
                "calibration_input_sha256": input_hash,
                "training_run_manifest_sha256": manifest_hash,
                "serving_aggregation_contract_sha256": contract_hash,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "operating_point.json").write_text(
        json.dumps(
            {
                "schema_version": "proxy-operating-point-v1",
                "status": "complete",
                "selection_split": "calibration_documents",
                "selection_unit": "document",
                "temperature": 2.0,
                "classifier_escalation_tau": 0.25,
                "calibration_trace_sha256": trace_hash,
                "calibration_input_sha256": input_hash,
                "training_run_manifest_sha256": manifest_hash,
                "serving_aggregation_contract_sha256": contract_hash,
            }
        ),
        encoding="utf-8",
    )
    pipe = object.__new__(InferencePipeline)
    pipe.model_dir = tmp_path
    pipe._model_temperature = None
    pipe._model_escalation_tau = None
    pipe._locked_operating_point = False
    pipe.calibrated = None
    monkeypatch.setattr(
        comparison,
        "_verify_finalized_model_bundle",
        lambda _: {"serving_aggregation_contract_sha256": contract_hash},
    )
    assert pipe._apply_bundle_calibration() == "bundle"
    with pytest.raises(ValueError, match="does not match"):
        pipe._apply_bundle_operating_point()


def test_m5_proxy_operating_point_requires_finalized_model_bundle(tmp_path: Path):
    (tmp_path / "operating_point.json").write_text("{}\n", encoding="utf-8")
    pipe = object.__new__(InferencePipeline)
    pipe.model_dir = tmp_path
    pipe._model_temperature = 1.0
    pipe._model_escalation_tau = None
    pipe._locked_operating_point = False

    with pytest.raises(ValueError, match="cannot verify finalized proxy model bundle"):
        pipe._apply_bundle_operating_point()


def test_proxy_training_execution_binds_all_inputs_and_checkpoint_bytes(tmp_path: Path):
    training_run = _write_materialized_run(tmp_path)
    checkpoint_root = tmp_path / "checkpoint-root"
    checkpoint_root.mkdir()
    spec = TrainSpec(
        train_path=str(training_run / "train_chunks.jsonl"),
        val_path=str(training_run / "validation_documents.jsonl"),
        test_path=None,
        output_dir=str(checkpoint_root),
        train_input_mode="pre_chunked",
        chunk_expand=False,
        proxy_candidate_mode=True,
        proxy_training_run_dir=str(training_run),
        base_model_revision="1" * 40,
        training_entrypoint_path=str(Path(__file__).resolve()),
        use_mlflow=False,
    )
    materialization = _proxy_materialization_audit(spec)
    assert materialization is not None
    for name in ("checkpoint-10", "checkpoint-20"):
        checkpoint = checkpoint_root / name
        checkpoint.mkdir()
        (checkpoint / "config.json").write_text(
            json.dumps({"checkpoint": name}), encoding="utf-8"
        )
    _write_proxy_training_execution(
        spec=spec,
        materialization_start=materialization,
        base_model_attestation={
            "identifier": "test/model",
            "requested_revision": "1" * 40,
            "resolved_revision": "1" * 40,
            "revision_kind": "huggingface_commit",
            "initial_state_dict_sha256": "2" * 64,
            "config_sha256": "3" * 64,
            "tokenizer_contract_sha256": "4" * 64,
            "tokenizer_is_fast": True,
        },
        run_id="mlflow-run-test",
    )
    _, _, finalizer_materialization = verify_materialized_training_run(training_run)
    checkpoints, execution = verify_proxy_training_execution(
        checkpoint_root,
        materialization_audit=finalizer_materialization,
    )
    assert [path.name for path in checkpoints] == ["checkpoint-10", "checkpoint-20"]
    assert execution["input_use"]["calibration_documents_used"] is False

    (checkpoint_root / "checkpoint-20" / "config.json").write_text(
        '{"tampered":true}', encoding="utf-8"
    )
    with pytest.raises(ProxyTrainingFinalizationError, match="checkpoint bytes"):
        verify_proxy_training_execution(
            checkpoint_root,
            materialization_audit=finalizer_materialization,
        )


def test_proxy_candidate_contract_forbids_any_test_input(tmp_path: Path):
    training_run = _write_materialized_run(tmp_path)
    spec = TrainSpec(
        train_path=str(training_run / "train_chunks.jsonl"),
        val_path=str(training_run / "validation_documents.jsonl"),
        test_path=str(training_run / "calibration_documents.jsonl"),
        output_dir=str(tmp_path / "candidate-root"),
        train_input_mode="pre_chunked",
        proxy_candidate_mode=True,
        proxy_training_run_dir=str(training_run),
    )
    with pytest.raises(ValueError, match="forbids test_path"):
        _proxy_materialization_audit(spec)


def test_comparison_tau_label_validation_matches_m5_non_argmax_selection():
    prediction = {
        "label": "S1",
        "confidence": 0.31,
        "scores": {"TS": 0.24, "S1": 0.31, "S2": 0.35, "S3": 0.10},
    }
    assert comparison._validate_predictions(
        [prediction],
        expected_count=1,
        model_name="candidate",
        escalation_tau=0.30,
    ) == ["S1"]
    with pytest.raises(comparison.ProxyComparisonError, match="disagree"):
        comparison._validate_predictions(
            [prediction],
            expected_count=1,
            model_name="candidate",
            escalation_tau=None,
        )


def test_raw_and_bundle_comparison_contracts_are_explicit_and_fast_only():
    legacy = comparison.serving_aggregation_contract()
    raw = comparison.serving_aggregation_contract(
        raw_model=True, require_fast_overflow=True
    )
    bundle = comparison.serving_aggregation_contract(
        apply_bundle_operating_point=True,
        require_fast_overflow=True,
    )
    assert "comparison_mode" not in legacy
    assert raw["comparison_mode"] == "raw_model"
    assert "forced identity T=1.0" in raw["temperature_policy"]
    assert bundle["comparison_mode"] == "bundle_operating_point"
    assert bundle["probability_aggregation"]["selection"] == (
        "bundle_calibration_tau_or_argmax"
    )
    assert raw["contract_sha256"] != legacy["contract_sha256"]
    assert bundle["contract_sha256"] != legacy["contract_sha256"]


def test_bundle_mode_refuses_missing_operating_point(tmp_path: Path):
    with pytest.raises(comparison.ProxyComparisonError, match="requires"):
        comparison._load_bundle_operating_point(tmp_path, temperature=1.0)


def test_bundle_comparison_rebinds_finalization_manifest_and_complete(tmp_path: Path):
    import hashlib

    model_dir = tmp_path / "finalized-model"
    model_dir.mkdir()
    model_payload = b"fixture-model-weights\n"
    (model_dir / "model.safetensors").write_bytes(model_payload)
    clean_model_attestation = comparison.hash_model_directory(model_dir)
    trace_hash = "a" * 64
    input_hash = "b" * 64
    training_hash = "c" * 64
    contract_hash = comparison.serving_aggregation_contract(
        apply_bundle_operating_point=True,
        require_fast_overflow=True,
    )["contract_sha256"]
    temperature = {
        "schema_version": "proxy-document-temperature-v1",
        "status": "complete",
        "fit_unit": "document",
        "temperature": 1.5,
        "calibration_trace_sha256": trace_hash,
        "calibration_input_sha256": input_hash,
        "training_run_manifest_sha256": training_hash,
        "serving_aggregation_contract_sha256": contract_hash,
    }
    operating_point = {
        "schema_version": "proxy-operating-point-v1",
        "status": "complete",
        "selection_split": "calibration_documents",
        "selection_unit": "document",
        "temperature": 1.5,
        "classifier_escalation_tau": 0.25,
        "calibration_trace_sha256": trace_hash,
        "calibration_input_sha256": input_hash,
        "training_run_manifest_sha256": training_hash,
        "serving_aggregation_contract_sha256": contract_hash,
    }
    payloads = {
        "checkpoint_selection": ("checkpoint_selection.json", b"{}\n"),
        "validation_window_logits": ("validation_window_logits.jsonl", b"{}\n"),
        "calibration_window_logits": ("calibration_window_logits.jsonl", b"{}\n"),
        "temperature": (
            "temperature.json",
            (json.dumps(temperature, sort_keys=True) + "\n").encode(),
        ),
        "operating_point": (
            "operating_point.json",
            (json.dumps(operating_point, sort_keys=True) + "\n").encode(),
        ),
    }
    artifacts = {}
    for key, (filename, payload) in payloads.items():
        (model_dir / filename).write_bytes(payload)
        artifacts[key] = {
            "path": filename,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
    manifest = {
        "schema_version": "proxy-classifier-finalization-v1",
        "run_id": "finalized-test",
        "status": "complete",
        "artifact_role": "proxy_deployment_candidate",
        "production_eligible": False,
        "customer_document_deployment_approved": False,
        "contracts": {
            "calibration_use": "temperature_and_escalation_tau_only",
            "frozen_or_blind_tuning_allowed": False,
        },
        "calibration": {
            "temperature": temperature,
            "operating_point": operating_point,
        },
        "published_model_before_calibration_metadata": clean_model_attestation,
        "artifacts": artifacts,
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    (model_dir / "finalization_manifest.json").write_bytes(manifest_payload)
    complete = {
        "schema_version": "proxy-classifier-finalization-v1",
        "run_id": "finalized-test",
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "artifacts": artifacts,
    }
    (model_dir / "COMPLETE").write_text(
        json.dumps(complete, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = comparison._verify_finalized_model_bundle(model_dir)
    assert audit["calibration_input_sha256"] == input_hash
    assert audit["calibration_trace_sha256"] == trace_hash
    assert audit["training_run_manifest_sha256"] == training_hash
    assert audit["serving_aggregation_contract_sha256"] == contract_hash
    assert audit["model_payload_tree_sha256"] == clean_model_attestation["tree_sha256"]

    (model_dir / "model.safetensors").write_bytes(b"different-model-weights\n")
    with pytest.raises(comparison.ProxyComparisonError, match="model payload hash"):
        comparison._verify_finalized_model_bundle(model_dir)
    (model_dir / "model.safetensors").write_bytes(model_payload)

    (model_dir / "operating_point.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(comparison.ProxyComparisonError, match="hash mismatch"):
        comparison._verify_finalized_model_bundle(model_dir)


def test_raw_production_comparison_rejects_tokenizer_fallback():
    contract = comparison.serving_aggregation_contract(
        raw_model=True, require_fast_overflow=True
    )
    batch = comparison.ModelPredictionBatch(
        predictions=(
            {
                "label": "TS",
                "confidence": 1.0,
                "scores": {"TS": 1.0, "S1": 0.0, "S2": 0.0, "S3": 0.0},
                "aggregation_trace": {
                    "char_chunk_count": 1,
                    "token_window_count": 1,
                    "tokenizer_mode_counts": {"fast_overflow_error_truncation": 1},
                },
            },
        ),
        runtime_attestation={
            "aggregation_contract_sha256": contract["contract_sha256"],
            "documents": 1,
            "document_level_predictions": 1,
            "total_character_chunks": 1,
            "total_token_windows": 1,
            "documents_with_multiple_character_chunks": 0,
            "documents_with_overflow_expansion": 0,
            "max_character_chunks_per_document": 1,
            "max_token_windows_per_document": 1,
            "tokenizer": {
                "class": "BrokenFastTokenizer",
                "is_fast": True,
                "mode_counts": {"fast_overflow_error_truncation": 1},
            },
            "temperature": {
                "value": 1.0,
                "source": "forced_identity_raw_model",
                "artifact_sha256": None,
                "environment_override_applied": False,
            },
            "operating_point": {
                "applied": False,
                "classifier_escalation_tau": None,
                "source": "excluded",
                "artifact_sha256": None,
                "environment_override_applied": False,
                "reselected_on_evaluation_corpora": False,
            },
            "finalization": None,
            "device": "cpu",
        },
    )
    with pytest.raises(comparison.ProxyComparisonError, match="fast overflow"):
        comparison._validate_prediction_batch(
            batch,
            expected_count=1,
            expected_contract_sha256=str(contract["contract_sha256"]),
            model_name="candidate",
            expect_raw_temperature=True,
            require_fast_overflow=True,
        )


def test_p1_proxy_candidate_cli_derives_only_attested_train_and_validation_paths(
    tmp_path: Path, monkeypatch, capsys
):
    training_run = tmp_path / "training-run"
    output = tmp_path / "checkpoint-root"
    captured = {}

    def fake_train(spec):
        captured["spec"] = spec
        return SimpleNamespace(
            model_version="candidate-test",
            deployable=False,
            artifact_status="proxy_checkpoint_candidates_only",
        )

    monkeypatch.setattr(
        "lloydk.modules.m4_training.trainer.train_classifier", fake_train
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "p1_train_classifier.py",
            "--mode",
            "full",
            "--proxy-candidate-mode",
            "--proxy-training-run-dir",
            str(training_run),
            "--output-dir",
            str(output),
            "--no-mlflow",
            "--no-bf16",
        ],
    )
    assert p1_cli.main() == 0
    spec = captured["spec"]
    assert spec.proxy_candidate_mode is True
    assert spec.train_path == str(training_run / "train_chunks.jsonl")
    assert spec.val_path == str(training_run / "validation_documents.jsonl")
    assert spec.test_path is None
    assert spec.train_input_mode == "pre_chunked"
    assert spec.chunk_expand is False
    assert spec.training_entrypoint_path.endswith("p1_train_classifier.py")
    assert "deployable" in capsys.readouterr().out
