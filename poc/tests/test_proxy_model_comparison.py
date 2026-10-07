"""Fail-closed frozen proxy model-comparison tests."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import compare_proxy_models as cli
from koipa import proxy_model_comparison as comparison


def test_evaluator_import_has_no_m5_db_or_service_side_effects():
    script = (
        "import json,sys; sys.path.insert(0, 'src'); "
        "import koipa.proxy_model_comparison; "
        "print(json.dumps(sorted(n for n in sys.modules if "
        "n == 'koipa.db' or n.startswith('koipa.services') or "
        "n == 'koipa.modules.m5_inference.pipeline')))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []


def _frozen_rows() -> list[dict]:
    counts = {"TS": 200, "S1": 250, "S2": 250, "S3": 300}
    rows: list[dict] = []
    ordinal = 0
    for grade, count in counts.items():
        for grade_index in range(count):
            rows.append(
                {
                    "doc_id": f"frozen-{ordinal:04d}",
                    "document_family_id": f"frozen-family-{grade_index % 100:03d}",
                    "text": f"unique frozen proxy text {ordinal:04d} grade {grade}",
                    "label": grade,
                }
            )
            ordinal += 1
    return rows


def _public_s3_rows() -> list[dict]:
    rows: list[dict] = []
    hangul_span = 0xD7A3 - 0xAC00 + 1
    for index in range(comparison.EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS):
        # Deterministic Hangul-rich bodies exercise the real quality validator
        # while keeping this plumbing fixture independent of external sources.
        text = "".join(
            chr(0xAC00 + ((ordinal * 7_919 + index * 17) % hangul_span))
            for ordinal in range(420)
        )
        rows.append(
            {
                "doc_id": f"public-s3-{index:03d}",
                "document_family_id": f"public-family-{index:03d}",
                "text": text,
                "label": "S3",
                "document_origin": "public_real",
                "proxy_role": "public_document",
                "document_type": "public-report",
                "source_id": f"public-source-{index:03d}",
                "source_reference": f"https://example.go.kr/public/{index:03d}",
                "source_license": "KOGL-1",
                "source_sha256": f"{index + 1:064x}",
                "license_evidence_sha256": f"{index + 1_001:064x}",
                "retrieved_at": "2026-08-08T00:00:00+09:00",
                "training_use_permitted": True,
                "evaluation_use_permitted": True,
            }
        )
    return rows


def _jsonl_bytes(rows: list[dict]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in rows
    )


def _write_frozen_bundle(
    root: Path, *, rows: list[dict] | None = None
) -> tuple[Path, Path, list[dict]]:
    frozen_rows = rows if rows is not None else _frozen_rows()
    corpus_path = root / "frozen.jsonl"
    corpus_bytes = _jsonl_bytes(frozen_rows)
    corpus_path.write_bytes(corpus_bytes)
    distribution = dict(Counter(row["label"] for row in frozen_rows))
    manifest_path = root / "assembly.json"
    manifest_path.write_text(
        json.dumps(
            {
                "ready": True,
                "stats": {
                    "selected": 1_000,
                    "selected_by_grade": {
                        "TS": 200,
                        "S1": 250,
                        "S2": 250,
                        "S3": 300,
                    },
                    "shortcut_gate": {"passed": True},
                },
                "artifact": {
                    "path": str(corpus_path),
                    "sha256": hashlib.sha256(corpus_bytes).hexdigest(),
                    "records": 1_000,
                },
                "observed_test_distribution": distribution,
            }
        ),
        encoding="utf-8",
    )
    return corpus_path, manifest_path, frozen_rows


def _write_public_s3_challenge(root: Path) -> Path:
    path = root / "public-s3-challenge.jsonl"
    path.write_bytes(_jsonl_bytes(_public_s3_rows()))
    return path


def _write_training_manifest(root: Path, rows: list[dict]) -> Path:
    run_dir = root / "training-run"
    run_dir.mkdir()
    chunks = [rows[index::3] for index in range(3)]
    artifacts: dict[str, dict] = {}
    for name, split_rows in zip(
        ("train_documents", "validation_documents", "calibration_documents"),
        chunks,
        strict=True,
    ):
        path = run_dir / f"{name}.jsonl"
        payload = _jsonl_bytes(split_rows)
        path.write_bytes(payload)
        artifacts[name] = {
            "path": path.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "records": len(split_rows),
        }
    manifest_path = run_dir / "manifest.json"
    manifest_bytes = (
        json.dumps(
            {
                "schema_version": "proxy-training-run-v1",
                "status": "complete",
                "inputs": {"training": {"row_count": len(rows)}},
                "artifacts": artifacts,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    (run_dir / "COMPLETE").write_text(
        json.dumps(
            {
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _comparison_training_rows() -> list[dict]:
    return [
        {
            "doc_id": f"training-{index:03d}",
            "document_family_id": f"training-family-{index:03d}",
            "text": f"independent training document {index}",
            "label": comparison.LABELS[index % len(comparison.LABELS)],
        }
        for index in range(12)
    ]


def _write_legacy_training_attestation(root: Path, *, model_dir: Path) -> Path:
    legacy_root = root / "legacy-training"
    legacy_root.mkdir()
    rows = _comparison_training_rows()
    paths = {}
    for split, split_rows in zip(
        ("train", "validation", "test"),
        (rows[:4], rows[4:8], rows[8:]),
        strict=True,
    ):
        # Historical corpus fixture intentionally has no doc/family identities.
        payload = _jsonl_bytes(
            [{"text": row["text"], "label": row["label"]} for row in split_rows]
        )
        path = legacy_root / f"{split}.jsonl"
        path.write_bytes(payload)
        paths[split] = path
    attestation_path = legacy_root / "legacy-attestation.json"
    historical_build_manifest = legacy_root / "historical-build.json"
    historical_build_manifest.write_text(
        json.dumps(
            {
                "schema_version": "historical-classifier-build-v1",
                "model_dir": str(model_dir),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    comparison.create_legacy_training_corpus_attestation(
        train_path=paths["train"],
        validation_path=paths["validation"],
        test_path=paths["test"],
        model_dir=model_dir,
        historical_build_manifest_path=historical_build_manifest,
        output_path=attestation_path,
    )
    return attestation_path


def _write_models(root: Path) -> tuple[Path, Path]:
    baseline = root / "baseline"
    candidate = root / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    (baseline / "config.json").write_text('{"version":"baseline"}', encoding="utf-8")
    (candidate / "config.json").write_text('{"version":"candidate"}', encoding="utf-8")
    return baseline, candidate


def _bind_finalized_proxy_candidates(
    monkeypatch: pytest.MonkeyPatch,
    bindings: dict[Path, Path],
) -> None:
    """Stub the separately tested bundle verifier for comparison plumbing tests."""
    manifest_hashes = {
        str(model_dir.resolve()): hashlib.sha256(manifest.read_bytes()).hexdigest()
        for model_dir, manifest in bindings.items()
    }

    def verify(model_dir: Path) -> dict[str, object]:
        return {
            "training_run_manifest_sha256": manifest_hashes[str(model_dir.resolve())]
        }

    monkeypatch.setattr(comparison, "_verify_finalized_model_bundle", verify)


def _perfect_candidate_bad_baseline(
    model_dir: Path,
    rows: list[dict],
    **kwargs,
) -> comparison.ModelPredictionBatch:
    if model_dir.name == "candidate":
        labels = [row["label"] for row in rows]
    else:
        labels = ["S3"] * len(rows)
    predictions = [
        {
            "label": label,
            "confidence": 1.0,
            "scores": {
                grade: 1.0 if grade == label else 0.0
                for grade in ("TS", "S1", "S2", "S3")
            },
            "aggregation_trace": {
                "char_chunk_count": 1,
                "token_window_count": 1,
                "tokenizer_mode_counts": {"fast_overflow": 1},
            },
        }
        for label in labels
    ]
    contract = comparison.serving_aggregation_contract(
        max_length=kwargs["max_length"],
        chunk_overlap=kwargs["chunk_overlap"],
        severe_codes=kwargs["severe_codes"],
        forward_batch_size=kwargs["batch_size"],
        apply_bundle_operating_point=kwargs.get("apply_bundle_operating_point", False),
        raw_model=kwargs.get("raw_model", False),
        require_fast_overflow=kwargs.get("require_fast_overflow", False),
    )
    return comparison.ModelPredictionBatch(
        tuple(predictions),
        {
            "aggregation_contract_sha256": contract["contract_sha256"],
            "documents": len(rows),
            "document_level_predictions": len(rows),
            "total_character_chunks": len(rows),
            "total_token_windows": len(rows),
            "documents_with_multiple_character_chunks": 0,
            "documents_with_overflow_expansion": 0,
            "max_character_chunks_per_document": 1,
            "max_token_windows_per_document": 1,
            "tokenizer": {
                "class": "AttestedTestTokenizer",
                "is_fast": True,
                "mode_counts": {"fast_overflow": len(rows)},
            },
            "temperature": {
                "value": 1.0,
                "source": (
                    "forced_identity_raw_model"
                    if kwargs.get("raw_model", False)
                    else "identity_no_bundle"
                ),
                "artifact_sha256": None,
                "environment_override_applied": False,
            },
            "device": "cpu",
        },
    )


def test_metrics_include_grade_fnr_and_directional_high_grade_underclass():
    truth = ["TS", "TS", "S1", "S1", "S2", "S3"]
    predicted = ["TS", "S2", "S1", "S3", "TS", "S3"]

    metrics = comparison.compute_classification_metrics(truth, predicted)

    assert metrics["per_grade"]["TS"]["fnr"] == 0.5
    assert metrics["per_grade"]["S1"]["fnr"] == 0.5
    assert metrics["ts_s1_fnr"] == 0.5
    assert metrics["high_grade_underclassification_count"] == 2
    assert metrics["high_grade_underclassification_rate"] == 0.5
    assert set(metrics["confusion_matrix"]) == {"TS", "S1", "S2", "S3"}
    assert "f1_macro" in metrics
    assert "f1_weighted" in metrics


def test_combined_high_fnr_excludes_ts_s1_boundary_but_exact_error_keeps_it():
    truth = ["TS", "S1", "TS", "S1"]
    predicted = ["S1", "TS", "S2", "S3"]

    metrics = comparison.compute_classification_metrics(truth, predicted)

    # Only predictions leaving the high-grade set are combined false negatives.
    assert metrics["ts_s1_false_negative_count"] == 2
    assert metrics["ts_s1_fnr"] == 0.5
    # Exact-grade error still exposes both TS<->S1 confusions.
    assert metrics["high_grade_exact_error_count"] == 4
    assert metrics["high_grade_exact_error_rate"] == 1.0
    # Directional underclass: TS->S1, TS->S2, S1->S3; S1->TS is overclass.
    assert metrics["high_grade_underclassification_count"] == 3
    assert metrics["high_grade_underclassification_rate"] == 0.75


def test_prediction_probabilities_fail_closed_on_nan_or_partial_scores():
    with pytest.raises(comparison.ProxyComparisonError, match="all four grade scores"):
        comparison._validate_predictions(
            [{"label": "TS", "confidence": 1.0, "scores": {"TS": 1.0}}],
            expected_count=1,
            model_name="candidate",
        )
    with pytest.raises(comparison.ProxyComparisonError, match="non-probability confidence"):
        comparison._validate_predictions(
            [
                {
                    "label": "TS",
                    "confidence": float("nan"),
                    "scores": {"TS": 1.0, "S1": 0.0, "S2": 0.0, "S3": 0.0},
                }
            ],
            expected_count=1,
            model_name="candidate",
        )


def test_serving_multi_chunk_late_high_signal_survives_severe_aggregation():
    marker = "LATE-TS-SIGNAL"
    text = "가" * 1_700 + marker + "나" * 1_600

    chunks = comparison.serving_char_chunks(text)

    assert len(chunks) >= 3
    assert len(chunks[0]) == comparison.SERVING_MAX_LENGTH * 3
    assert marker not in chunks[0]
    assert any(marker in chunk for chunk in chunks[1:])

    # A first-window-only evaluator predicts S3.  A later short TS window must
    # remain visible under M5's severe max-pool even with a much smaller weight.
    windows = [
        [0.02, 0.02, 0.06, 0.90],
        [0.95, 0.02, 0.02, 0.01],
    ]
    scores = comparison.aggregate_serving_probabilities(windows, [1_536, 40])
    assert comparison.LABELS[max(range(4), key=lambda index: windows[0][index])] == "S3"
    assert max(scores, key=scores.get) == "TS"
    assert sum(scores.values()) == pytest.approx(1.0)

    reversed_scores = comparison.aggregate_serving_probabilities(
        [list(reversed(row)) for row in windows],
        [1_536, 40],
        label_order=("S3", "S2", "S1", "TS"),
    )
    assert reversed_scores == pytest.approx(scores)


def test_fast_tokenizer_overflow_mapping_and_contract_are_attested():
    class MappingList(list):
        def tolist(self):
            return list(self)

    class FakeFastTokenizer:
        is_fast = True

        def __init__(self):
            self.kwargs = None

        def __call__(self, batch, **kwargs):
            self.kwargs = kwargs
            return {
                "input_ids": [[1], [2], [3]],
                "overflow_to_sample_mapping": MappingList([0, 0, 1]),
            }

    tokenizer = FakeFastTokenizer()
    encoded, mapping, mode = comparison._encode_serving_windows(
        tokenizer,
        ["long first chunk", "short second chunk"],
        max_length=512,
        chunk_overlap=64,
    )
    contract = comparison.serving_aggregation_contract()

    assert mapping == [0, 0, 1]
    assert mode == "fast_overflow"
    assert "overflow_to_sample_mapping" not in encoded
    assert tokenizer.kwargs["return_overflowing_tokens"] is True
    assert tokenizer.kwargs["stride"] == 64
    assert contract["char_split"]["size_chars"] == 1_536
    assert contract["char_split"]["overlap_chars"] == 64
    assert contract["tokenizer_windows"]["fast_tokenizer"][
        "return_overflowing_tokens"
    ] is True
    assert len(contract["contract_sha256"]) == 64


def test_family_cluster_bootstrap_is_paired_and_deterministic():
    rows = [
        {
            "doc_id": f"doc-{index}",
            "document_family_id": f"family-{index // 4}",
            "text": f"unique text {index}",
            "label": ("TS", "S1", "S2", "S3")[index % 4],
        }
        for index in range(40)
    ]
    truth = [row["label"] for row in rows]
    baseline = ["S3"] * len(rows)

    first = comparison.family_cluster_bootstrap(
        rows, baseline, truth, replicates=200, seed=77
    )
    second = comparison.family_cluster_bootstrap(
        rows, baseline, truth, replicates=200, seed=77
    )

    assert first == second
    assert first["resampling_unit"] == "document_family_id"
    assert first["family_count"] == 10
    macro_delta = first["candidate_minus_baseline"]["f1_macro"]
    assert macro_delta["estimate"] > 0
    assert macro_delta["lower"] > 0


def test_frozen_loader_rejects_non_1000_and_manifest_hash_tampering(tmp_path: Path):
    corpus_path, manifest_path, rows = _write_frozen_bundle(
        tmp_path, rows=_frozen_rows()[:-1]
    )
    with pytest.raises(comparison.ProxyComparisonError, match="exactly 1,000"):
        comparison.load_frozen_proxy_gold(corpus_path, manifest_path)

    corpus_path, manifest_path, _ = _write_frozen_bundle(tmp_path, rows=rows + [_frozen_rows()[-1]])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(comparison.ProxyComparisonError, match="SHA-256"):
        comparison.load_frozen_proxy_gold(corpus_path, manifest_path)


def test_public_s3_loader_requires_exact_eligible_attested_300(tmp_path: Path):
    challenge_path = _write_public_s3_challenge(tmp_path)

    rows, audit = comparison.load_public_s3_challenge(challenge_path)

    assert len(rows) == 300
    assert audit["records"] == 300
    assert audit["label"] == "S3"
    assert audit["document_origin"] == "public_real"
    assert audit["training_use_inferred"] is False
    assert audit["unique_doc_ids"] == 300
    assert audit["unique_normalized_text_hashes"] == 300
    assert len(audit["file_sha256"]) == 64

    rows[0]["source_license"] = "UNKNOWN"
    challenge_path.write_bytes(_jsonl_bytes(rows))
    with pytest.raises(comparison.ProxyComparisonError, match="KOGL"):
        comparison.load_public_s3_challenge(challenge_path)


@pytest.mark.parametrize("overlap_key", ["doc_id", "document_family_id", "text"])
def test_public_s3_challenge_must_not_overlap_primary(overlap_key: str):
    primary = _frozen_rows()
    challenge = _public_s3_rows()
    challenge[0][overlap_key] = primary[0][overlap_key]

    with pytest.raises(comparison.ProxyComparisonError, match="overlaps"):
        comparison.assert_separate_evaluation_corpora(primary, challenge)


def test_public_s3_metrics_are_overclassification_only():
    metrics = comparison.compute_public_s3_challenge_metrics(
        ["S3", "S3", "S2", "S1", "TS"]
    )

    assert metrics["sample_count"] == 5
    assert metrics["truth_label"] == "S3"
    assert metrics["s3_recall"] == pytest.approx(0.4)
    assert metrics["public_false_positive_rate"] == pytest.approx(0.6)
    assert metrics["mean_overclassification_severity"] == pytest.approx(1.2)
    assert metrics["severe_overclassification_rate"] == pytest.approx(0.4)
    assert metrics["maximum_overclassification_severity"] == 3
    assert "f1_macro" not in metrics


@pytest.mark.parametrize("overlap_key", ["doc_id", "document_family_id", "text"])
def test_training_manifest_is_reloaded_and_overlap_fails_closed(
    tmp_path: Path, overlap_key: str
):
    corpus_path, manifest_path, frozen = _write_frozen_bundle(tmp_path)
    training = [
        {
            "doc_id": "train-1",
            "document_family_id": "train-family-1",
            "text": "unique training text one",
            "label": "TS",
        },
        {
            "doc_id": "train-2",
            "document_family_id": "train-family-2",
            "text": "unique training text two",
            "label": "S1",
        },
        {
            "doc_id": "train-3",
            "document_family_id": "train-family-3",
            "text": "unique training text three",
            "label": "S2",
        },
    ]
    training[0][overlap_key] = frozen[0][overlap_key]
    training_manifest = _write_training_manifest(tmp_path, training)
    frozen_rows, _ = comparison.load_frozen_proxy_gold(corpus_path, manifest_path)
    training_rows, audit = comparison.load_training_manifest(training_manifest)

    assert audit["records"] == 3
    with pytest.raises(comparison.ProxyComparisonError, match="leakage detected"):
        comparison.assert_no_training_overlap(frozen_rows, training_rows)


def test_training_manifest_artifact_hash_is_verified(tmp_path: Path):
    training = [
        {
            "doc_id": f"train-{index}",
            "document_family_id": f"train-family-{index}",
            "text": f"unique training body {index}",
            "label": ("TS", "S1", "S2")[index],
        }
        for index in range(3)
    ]
    manifest_path = _write_training_manifest(tmp_path, training)
    artifact = manifest_path.parent / "train_documents.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")

    with pytest.raises(comparison.ProxyComparisonError, match="SHA-256 mismatch"):
        comparison.load_training_manifest(manifest_path)


def test_comparison_publishes_hash_attested_artifacts_and_proxy_only_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    corpus_path, manifest_path, _ = _write_frozen_bundle(tmp_path)
    baseline, candidate = _write_models(tmp_path)
    training_manifest = _write_training_manifest(tmp_path, _comparison_training_rows())
    _bind_finalized_proxy_candidates(
        monkeypatch,
        {baseline: training_manifest, candidate: training_manifest},
    )

    with pytest.raises(comparison.ProxyComparisonError, match="requires explicit"):
        comparison.compare_proxy_models(
            frozen_corpus_path=corpus_path,
            frozen_manifest_path=manifest_path,
            baseline_model_dir=baseline,
            candidate_model_dir=candidate,
            baseline_training_manifest_path=training_manifest,
            candidate_training_manifest_path=training_manifest,
            output_root=tmp_path / "missing-mode",
            predictor=_perfect_candidate_bad_baseline,
        )

    run_dir, report, complete = comparison.compare_proxy_models(
        frozen_corpus_path=corpus_path,
        frozen_manifest_path=manifest_path,
        baseline_model_dir=baseline,
        candidate_model_dir=candidate,
        baseline_training_manifest_path=training_manifest,
        candidate_training_manifest_path=training_manifest,
        output_root=tmp_path / "comparisons",
        run_id="comparison-unit-001",
        bootstrap_replicates=200,
        predictor=_perfect_candidate_bad_baseline,
        comparison_mode="raw_model",
    )

    assert report["claim_scope"] == comparison.CLAIM_SCOPE
    assert "not customer" in report["prohibited_interpretation"].lower()
    assert report["models"]["candidate"]["metrics"]["f1_macro"] == 1.0
    assert report["inputs"]["training_leakage_check"]["baseline"]["checked"] is True
    assert sorted(path.name for path in run_dir.iterdir()) == [
        "COMPLETE.json",
        "REPORT.md",
        "baseline_predictions.jsonl",
        "candidate_predictions.jsonl",
        "comparison.json",
    ]
    for artifact in complete["artifacts"].values():
        path = run_dir / artifact["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    payload = json.loads((run_dir / "comparison.json").read_text(encoding="utf-8"))
    assert payload["inputs"]["frozen_proxy_gold"]["records"] == 1_000
    assert payload["bootstrap_confidence_intervals"]["family_count"] == 100
    contract = payload["evaluation_contract"]["aggregation"]
    assert payload["evaluation_contract"]["validation_rows_remain_document_level"] is True
    assert contract["char_split"]["size_chars"] == 1_536
    assert contract["probability_aggregation"]["severe_codes"] == ["TS", "S1"]
    assert complete["aggregation_contract_sha256"] == contract["contract_sha256"]
    assert payload["models"]["baseline"]["aggregation_runtime"][
        "document_level_predictions"
    ] == 1_000

    with pytest.raises(comparison.ProxyComparisonError, match="already exists"):
        comparison.compare_proxy_models(
            frozen_corpus_path=corpus_path,
            frozen_manifest_path=manifest_path,
            baseline_model_dir=baseline,
            candidate_model_dir=candidate,
            baseline_training_manifest_path=training_manifest,
            candidate_training_manifest_path=training_manifest,
            output_root=tmp_path / "comparisons",
            run_id="comparison-unit-001",
            bootstrap_replicates=200,
            predictor=_perfect_candidate_bad_baseline,
            comparison_mode="raw_model",
        )


def test_raw_comparison_rejects_full_manifest_unbound_from_finalized_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    corpus_path, manifest_path, _ = _write_frozen_bundle(tmp_path)
    baseline, candidate = _write_models(tmp_path)
    training_manifest = _write_training_manifest(tmp_path, _comparison_training_rows())
    unrelated_root = tmp_path / "unrelated"
    unrelated_root.mkdir()
    unrelated_rows = _comparison_training_rows()
    unrelated_rows[0] = {
        **unrelated_rows[0],
        "text": "independent unrelated training document",
    }
    unrelated_manifest = _write_training_manifest(
        unrelated_root, unrelated_rows
    )
    _bind_finalized_proxy_candidates(
        monkeypatch,
        {baseline: unrelated_manifest, candidate: training_manifest},
    )

    with pytest.raises(comparison.ProxyComparisonError, match="not bound to its supplied"):
        comparison.compare_proxy_models(
            frozen_corpus_path=corpus_path,
            frozen_manifest_path=manifest_path,
            baseline_model_dir=baseline,
            candidate_model_dir=candidate,
            baseline_training_manifest_path=training_manifest,
            candidate_training_manifest_path=training_manifest,
            output_root=tmp_path / "unbound-model",
            predictor=_perfect_candidate_bad_baseline,
            comparison_mode="raw_model",
        )


def test_optional_public_s3_challenge_stays_outside_primary_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    corpus_path, manifest_path, _ = _write_frozen_bundle(tmp_path)
    challenge_path = _write_public_s3_challenge(tmp_path)
    baseline, candidate = _write_models(tmp_path)
    training_manifest = _write_training_manifest(tmp_path, _comparison_training_rows())
    _bind_finalized_proxy_candidates(
        monkeypatch,
        {baseline: training_manifest, candidate: training_manifest},
    )

    run_dir, report, complete = comparison.compare_proxy_models(
        frozen_corpus_path=corpus_path,
        frozen_manifest_path=manifest_path,
        public_s3_challenge_path=challenge_path,
        baseline_model_dir=baseline,
        candidate_model_dir=candidate,
        baseline_training_manifest_path=training_manifest,
        candidate_training_manifest_path=training_manifest,
        output_root=tmp_path / "comparisons",
        run_id="comparison-public-s3-001",
        bootstrap_replicates=200,
        predictor=_perfect_candidate_bad_baseline,
        comparison_mode="raw_model",
    )

    assert report["models"]["candidate"]["metrics"]["sample_count"] == 1_000
    challenge = report["public_s3_challenge"]
    assert challenge["present"] is True
    assert challenge["included_in_primary_metrics"] is False
    assert challenge["separation_from_primary"]["metrics_combined"] is False
    assert challenge["metrics"]["candidate"]["sample_count"] == 300
    assert "f1_macro" not in challenge["metrics"]["candidate"]
    assert report["bootstrap_confidence_intervals"]["family_count"] == 100
    assert report["models"]["candidate"]["aggregation_runtime"][
        "scope_document_counts"
    ] == {
        "primary_frozen": 1_000,
        "public_s3_challenge": 300,
        "total_inference": 1_300,
    }
    assert complete["public_s3_input_sha256"] == report["inputs"][
        "public_s3_challenge"
    ]["file_sha256"]
    assert (run_dir / "baseline_public_s3_predictions.jsonl").is_file()
    assert (run_dir / "candidate_public_s3_predictions.jsonl").is_file()
    assert (
        complete["artifacts"]["candidate_public_s3_predictions"][
            "included_in_primary_metrics"
        ]
        is False
    )
    markdown = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "Separate public-real S3 overclassification challenge" in markdown
    assert "not included in any primary" in markdown


def test_raw_comparison_accepts_reverified_legacy_training_provenance_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    corpus_path, manifest_path, _ = _write_frozen_bundle(tmp_path)
    baseline, candidate = _write_models(tmp_path)
    legacy_attestation = _write_legacy_training_attestation(
        tmp_path, model_dir=baseline
    )
    candidate_manifest = _write_training_manifest(
        tmp_path, _comparison_training_rows()
    )
    _bind_finalized_proxy_candidates(monkeypatch, {candidate: candidate_manifest})

    run_dir, report, _ = comparison.compare_proxy_models(
        frozen_corpus_path=corpus_path,
        frozen_manifest_path=manifest_path,
        baseline_model_dir=baseline,
        candidate_model_dir=candidate,
        baseline_legacy_training_attestation_path=legacy_attestation,
        candidate_training_manifest_path=candidate_manifest,
        output_root=tmp_path / "legacy-comparison",
        run_id="legacy-raw-001",
        predictor=_perfect_candidate_bad_baseline,
        comparison_mode="raw_model",
    )

    assert run_dir.is_dir()
    assert "legacy_training_provenance" in report["claim_scope"]
    assert report["inputs"]["training_manifest"]["baseline"]["legacy_provenance"]
    with pytest.raises(comparison.ProxyComparisonError, match="supplied model directory"):
        comparison.load_legacy_training_corpus_attestation(
            legacy_attestation, model_dir=candidate
        )

    with pytest.raises(comparison.ProxyComparisonError, match="only for raw_model"):
        comparison.compare_proxy_models(
            frozen_corpus_path=corpus_path,
            frozen_manifest_path=manifest_path,
            baseline_model_dir=baseline,
            candidate_model_dir=candidate,
            baseline_legacy_training_attestation_path=legacy_attestation,
            candidate_training_manifest_path=candidate_manifest,
            output_root=tmp_path / "legacy-bundle-rejected",
            predictor=_perfect_candidate_bad_baseline,
            comparison_mode="bundle_operating_point",
        )


def test_cli_runs_same_fail_closed_comparison_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    corpus_path, manifest_path, _ = _write_frozen_bundle(tmp_path)
    baseline, candidate = _write_models(tmp_path)
    training_manifest = _write_training_manifest(tmp_path, _comparison_training_rows())
    monkeypatch.setattr(comparison, "predict_model", _perfect_candidate_bad_baseline)
    _bind_finalized_proxy_candidates(
        monkeypatch,
        {baseline: training_manifest, candidate: training_manifest},
    )

    exit_code = cli.main(
        [
            "--frozen-corpus",
            str(corpus_path),
            "--frozen-manifest",
            str(manifest_path),
            "--baseline-model-dir",
            str(baseline),
            "--candidate-model-dir",
            str(candidate),
            "--baseline-training-manifest",
            str(training_manifest),
            "--candidate-training-manifest",
            str(training_manifest),
            "--comparison-mode",
            "raw_model",
            "--output-root",
            str(tmp_path / "cli-comparisons"),
            "--run-id",
            "cli-comparison-001",
            "--bootstrap-replicates",
            "200",
            "--device",
            "cpu",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["complete"] is True
    assert output["claim_scope"] == comparison.CLAIM_SCOPE
    assert len(output["comparison_sha256"]) == 64
