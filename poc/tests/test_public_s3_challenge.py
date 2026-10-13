"""Standalone public-real S3 challenge evaluator tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from koipa import proxy_model_comparison as comparison
from koipa import public_s3_challenge as challenge


def _challenge_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    hangul_span = 0xD7A3 - 0xAC00 + 1
    for index in range(challenge.EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS):
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
                "training_use_permitted": False,
                "evaluation_use_permitted": True,
            }
        )
    return rows


def _write_challenge(root: Path, rows: list[dict[str, object]] | None = None) -> Path:
    path = root / "public-s3.jsonl"
    selected = rows if rows is not None else _challenge_rows()
    path.write_bytes(
        b"".join(
            (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
            for row in selected
        )
    )
    return path


def _write_model(root: Path) -> Path:
    model = root / "model"
    model.mkdir()
    (model / "config.json").write_text('{"version":"challenge"}', encoding="utf-8")
    return model


def _attested_predictor(
    model_dir: Path,
    rows: list[dict[str, object]],
    **kwargs: object,
) -> comparison.ModelPredictionBatch:
    del model_dir
    labels = [
        *("S3" for _ in range(240)),
        *("S2" for _ in range(30)),
        *("S1" for _ in range(20)),
        *("TS" for _ in range(10)),
    ]
    assert len(rows) == len(labels)
    predictions = tuple(
        {
            "label": label,
            "confidence": 1.0,
            "scores": {
                grade: 1.0 if grade == label else 0.0
                for grade in comparison.LABELS
            },
            "aggregation_trace": {
                "char_chunk_count": 1,
                "token_window_count": 1,
                "tokenizer_mode_counts": {"fast_overflow": 1},
            },
        }
        for label in labels
    )
    contract = comparison.serving_aggregation_contract(
        max_length=int(kwargs["max_length"]),
        chunk_overlap=int(kwargs["chunk_overlap"]),
        severe_codes=kwargs["severe_codes"],
        forward_batch_size=int(kwargs["batch_size"]),
    )
    return comparison.ModelPredictionBatch(
        predictions,
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
                "class": "AttestedChallengeTokenizer",
                "is_fast": True,
                "mode_counts": {"fast_overflow": len(rows)},
            },
            "temperature": {
                "value": 1.0,
                "source": "identity_no_bundle",
                "artifact_sha256": None,
                "environment_override_applied": False,
            },
            "device": "cpu",
        },
    )


def test_evaluator_writes_immutable_hash_attested_challenge_run(tmp_path: Path):
    challenge_path = _write_challenge(tmp_path)
    model_dir = _write_model(tmp_path)
    output_root = tmp_path / "reports"

    run_dir, manifest, complete = challenge.evaluate_public_s3_challenge(
        challenge_path=challenge_path,
        model_dir=model_dir,
        output_root=output_root,
        run_id="challenge-001",
        device="cpu",
        predictor=_attested_predictor,
    )

    assert run_dir == output_root / "challenge-001"
    assert sorted(path.name for path in run_dir.iterdir()) == [
        "COMPLETE.json",
        "REPORT.md",
        "manifest.json",
        "predictions.jsonl",
    ]
    metrics = manifest["metrics"]
    assert metrics["s3_recall"] == pytest.approx(0.8)
    assert metrics["public_false_positive_rate"] == pytest.approx(0.2)
    assert metrics["overclassification_rate"] == pytest.approx(0.2)
    assert metrics["prediction_distribution"] == {
        "TS": 10,
        "S1": 20,
        "S2": 30,
        "S3": 240,
    }
    assert metrics["mean_overclassification_severity"] == pytest.approx(1 / 3)
    assert metrics["maximum_overclassification_severity"] == 3
    assert metrics["ts_overclassification_count"] == 10
    assert metrics["s1_overclassification_count"] == 20
    assert metrics["ts_s1_severe_overclassification_count"] == 30
    assert "accuracy" not in metrics
    assert "balanced_accuracy" not in metrics
    assert manifest["prohibited_claims"] == [
        "overall_four_grade_accuracy",
        "balanced_accuracy",
        "customer_document_accuracy",
    ]
    assert manifest["evaluation_contract"]["bootstrap_applied"] is False
    assert manifest["evaluation_contract"]["primary_proxy_metrics_combined"] is False

    predictions_path = run_dir / "predictions.jsonl"
    prediction_lines = predictions_path.read_text(encoding="utf-8").splitlines()
    assert len(prediction_lines) == 300
    assert "text" not in json.loads(prediction_lines[0])
    assert complete["artifacts"]["predictions"]["sha256"] == hashlib.sha256(
        predictions_path.read_bytes()
    ).hexdigest()
    assert complete["artifacts"]["manifest"]["sha256"] == hashlib.sha256(
        (run_dir / "manifest.json").read_bytes()
    ).hexdigest()
    assert complete["challenge_input_sha256"] == hashlib.sha256(
        challenge_path.read_bytes()
    ).hexdigest()
    assert len(complete["model_tree_sha256"]) == 64
    assert len(complete["evaluation_code_tree_sha256"]) == 64
    assert len(complete["aggregation_contract_sha256"]) == 64

    with pytest.raises(comparison.ProxyComparisonError, match="already exists"):
        challenge.evaluate_public_s3_challenge(
            challenge_path=challenge_path,
            model_dir=model_dir,
            output_root=output_root,
            run_id="challenge-001",
            device="cpu",
            predictor=_attested_predictor,
        )


def test_evaluator_rejects_duplicate_family_even_when_ids_and_texts_are_unique(
    tmp_path: Path,
):
    rows = _challenge_rows()
    rows[1]["document_family_id"] = rows[0]["document_family_id"]
    challenge_path = _write_challenge(tmp_path, rows)
    model_dir = _write_model(tmp_path)

    with pytest.raises(comparison.ProxyComparisonError, match="duplicate document_family_id"):
        challenge.evaluate_public_s3_challenge(
            challenge_path=challenge_path,
            model_dir=model_dir,
            output_root=tmp_path / "reports",
            run_id="duplicate-family",
            predictor=_attested_predictor,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("document_origin", "synthetic", "challenge_requires_public_real"),
        ("label", "S2", "challenge_requires_S3"),
        (
            "evaluation_use_permitted",
            False,
            "challenge_requires_evaluation_permission",
        ),
    ],
)
def test_evaluator_rejects_non_public_non_s3_or_non_permitted_records(
    tmp_path: Path, field: str, value: object, message: str
):
    rows = _challenge_rows()
    rows[0][field] = value
    challenge_path = _write_challenge(tmp_path, rows)
    model_dir = _write_model(tmp_path)

    with pytest.raises(comparison.ProxyComparisonError, match=message):
        challenge.evaluate_public_s3_challenge(
            challenge_path=challenge_path,
            model_dir=model_dir,
            output_root=tmp_path / "reports",
            run_id=f"invalid-{field}",
            predictor=_attested_predictor,
        )


def test_evaluator_detects_model_mutation_during_inference(tmp_path: Path):
    challenge_path = _write_challenge(tmp_path)
    model_dir = _write_model(tmp_path)

    def mutating_predictor(model: Path, rows: list[dict], **kwargs: object):
        result = _attested_predictor(model, rows, **kwargs)
        (model / "config.json").write_text('{"version":"changed"}', encoding="utf-8")
        return result

    with pytest.raises(comparison.ProxyComparisonError, match="model changed"):
        challenge.evaluate_public_s3_challenge(
            challenge_path=challenge_path,
            model_dir=model_dir,
            output_root=tmp_path / "reports",
            run_id="mutated-model",
            predictor=mutating_predictor,
        )
