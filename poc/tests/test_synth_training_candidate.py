"""Approved synthetic documents are explicit, gated inputs to training."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from koipa.api._jwt_auth import JWTClaims, require_auth
from koipa.api.app import app
from koipa.services.synthesis_service import SynthesisService


def test_candidate_is_ready_only_when_hygiene_passes(monkeypatch) -> None:
    rows = [{"doc_id": "a", "text": "검토 문서", "label": "S2"}]
    monkeypatch.setattr(
        SynthesisService,
        "build_training_rows",
        lambda _self: {
            "rows": rows,
            "dataset_version": "synth-a1b2c3d4",
            "approved_total": 1,
            "included": 1,
            "excluded_noise": 0,
            "excluded_empty": 0,
            "excluded_gate_error": 0,
            "grade_corrected": 0,
            "corpus_leakage": {
                "documents": 1,
                "length_only_1nn": 0.0,
                "tell_coverage": 0.0,
            },
        },
    )

    result, candidate = SynthesisService().training_candidate()

    assert result["rows"] == rows
    assert candidate.ready is True
    assert candidate.dataset_version == "synth-a1b2c3d4"
    assert candidate.included == 1
    assert candidate.blockers == []


def test_candidate_blocks_missing_hygiene_measurement(monkeypatch) -> None:
    monkeypatch.setattr(
        SynthesisService,
        "build_training_rows",
        lambda _self: {
            "rows": [{"doc_id": "a", "text": "검토 문서", "label": "S2"}],
            "dataset_version": "synth-a1b2c3d4",
            "approved_total": 1,
            "included": 1,
            "corpus_leakage": {"documents": 0, "error": "measurement unavailable"},
        },
    )

    _, candidate = SynthesisService().training_candidate()

    assert candidate.ready is False
    assert candidate.blockers == ["학습 후보 품질 검사를 완료하지 못했습니다"]


def test_freeze_training_candidate_binds_rows_and_manifest(tmp_path, monkeypatch) -> None:
    rows = [{"doc_id": "a", "text": "검토 문서", "label": "S2"}]
    monkeypatch.setattr(
        SynthesisService,
        "build_training_rows",
        lambda _self: {
            "rows": rows,
            "dataset_version": "synth-a1b2c3d4",
            "approved_total": 1,
            "included": 1,
            "excluded_noise": 0,
            "excluded_empty": 0,
            "excluded_gate_error": 0,
            "grade_corrected": 0,
            "corpus_leakage": {"documents": 1, "length_only_1nn": 0.0, "tell_coverage": 0.0},
        },
    )

    frozen = SynthesisService().freeze_training_candidate(tmp_path)
    restored, candidate = SynthesisService.load_frozen_training_candidate(
        frozen["snapshot_path"],
        expected_dataset_version="synth-a1b2c3d4",
        expected_rows_sha256=frozen["rows_sha256"],
    )

    assert frozen["ready"] is True
    assert candidate.ready is True
    assert restored["rows"] == rows

    # A worker must fail rather than train from a modified file under the same name.
    with open(frozen["snapshot_path"], "a", encoding="utf-8") as stream:
        stream.write('{"doc_id":"tampered","text":"x","label":"S1"}\n')
    with pytest.raises(ValueError, match="무결성"):
        SynthesisService.load_frozen_training_candidate(frozen["snapshot_path"])


def test_training_submit_records_only_snapshot_identity(monkeypatch) -> None:
    """DB/job metadata retains reproducible identifiers, never the server file path."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4

    from koipa.schemas.common import Actor
    from koipa.schemas.training import TrainRequest
    from koipa.services import training_service as training_module
    from koipa.services.training_service import TrainingService

    candidate = SimpleNamespace(dataset_version="synth-a1b2c3d4", included=2, approved_total=3)
    frozen = {
        "ready": True,
        "candidate": candidate,
        "snapshot_path": "datasets/retrain/approved_synthetic_snapshots/synth-a1b2c3d4.jsonl",
        "rows_sha256": "a" * 64,
        "created_at": "2026-10-11T00:00:00+00:00",
    }
    monkeypatch.setattr(SynthesisService, "freeze_training_candidate", lambda *_args: frozen)
    monkeypatch.setattr(training_module, "_celery_dispatch_available", lambda: False)
    service = TrainingService()
    service.jobs = MagicMock()
    captured: dict = {}

    def create_run(_req, *, hyperparameters):
        captured["hyperparameters"] = hyperparameters
        return uuid4()

    monkeypatch.setattr(service, "_create_run", create_run)
    service.submit(
        TrainRequest(
            training_type="full",
            actor=Actor(user_id="admin", role="admin"),
            include_approved_synthetic=True,
        )
    )

    snapshot = captured["hyperparameters"]["approved_synthetic_snapshot"]
    assert snapshot["dataset_version"] == "synth-a1b2c3d4"
    assert snapshot["rows_sha256"] == "a" * 64
    assert "snapshot_path" not in snapshot
    assert service.jobs.create.call_args.kwargs["payload"]["approved_synthetic_snapshot"] == snapshot


def test_training_candidate_endpoint_returns_current_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        SynthesisService,
        "training_candidate",
        lambda _self: (
            {},
            {
                "ready": True,
                "dataset_version": "synth-a1b2c3d4",
                "approved_total": 1,
                "included": 1,
                "excluded_noise": 0,
                "excluded_empty": 0,
                "excluded_gate_error": 0,
                "grade_corrected": 0,
                "blockers": [],
            },
        ),
    )
    admin = JWTClaims(sub="admin-test", roles=("admin",), exp=9999999999)
    app.dependency_overrides[require_auth] = lambda: {
        "mode": "jwt", "claims": admin, "actor_role": "admin", "actor_roles": ["admin"],
    }
    try:
        response = TestClient(app).get("/api/v1/synth/training-candidate")
    finally:
        app.dependency_overrides.pop(require_auth, None)

    assert response.status_code == 200
    assert response.json()["dataset_version"] == "synth-a1b2c3d4"
    assert response.json()["ready"] is True
