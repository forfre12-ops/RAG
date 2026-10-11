from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from koipa.config import settings
from koipa.schemas.common import Actor
from koipa.schemas.training import TrainRequest
from koipa.services.training_service import (
    TrainingService,
    TrainingDatasetTooSmall,
    TrainingDatasetUnavailable,
    TrainingInputRejected,
    ensure_training_dataset_ready,
)


_LABELS = ("TS", "S1", "S2", "S3")


def _write_split(root, name: str, rows: int = 1, labels: tuple[str, ...] = _LABELS) -> None:
    (root / f"{name}.jsonl").write_text(
        "".join(json.dumps({"id": i, "label": labels[i % len(labels)]}) + "\n" for i in range(rows)),
        encoding="utf-8",
    )


def test_training_dataset_preflight_accepts_minimum_balanced_splits(tmp_path, monkeypatch):
    for split, rows in (("train", 400), ("val", 80), ("test", 80)):
        _write_split(tmp_path, split, rows)
    monkeypatch.setattr(settings, "training_dataset_dir", str(tmp_path))

    assert ensure_training_dataset_ready() == {"train": 400, "val": 80, "test": 80}


def test_training_dataset_preflight_rejects_insufficient_or_unbalanced_data(tmp_path, monkeypatch):
    _write_split(tmp_path, "train", 400, labels=("S3",))
    _write_split(tmp_path, "val", 80)
    _write_split(tmp_path, "test", 80)
    monkeypatch.setattr(settings, "training_dataset_dir", str(tmp_path))

    with pytest.raises(TrainingDatasetTooSmall) as raised:
        ensure_training_dataset_ready()

    assert "등급(TS/S1/S2/S3)별 50건 이상" in raised.value.public_message


def test_formal_training_profile_rejects_unimplemented_modes_and_overrides():
    with pytest.raises(ValidationError):
        TrainRequest(training_type="incremental", actor=Actor(user_id="admin", role="admin"))

    service = TrainingService()
    with pytest.raises(TrainingInputRejected, match="5에폭"):
        service.submit(
            TrainRequest(
                training_type="full",
                hyperparams={"epochs": 1},
                actor=Actor(user_id="admin", role="admin"),
            )
        )


def test_training_dataset_preflight_rejects_missing_split_before_enqueue(tmp_path, monkeypatch):
    _write_split(tmp_path, "train")
    _write_split(tmp_path, "val")
    monkeypatch.setattr(settings, "training_dataset_dir", str(tmp_path))

    with pytest.raises(TrainingDatasetUnavailable) as raised:
        ensure_training_dataset_ready()

    assert raised.value.public_message == "학습 데이터가 서버에 준비되지 않았습니다. 관리자에게 문의하세요."
    assert "test" in str(raised.value)


def test_training_dataset_preflight_rejects_malformed_jsonl(tmp_path, monkeypatch):
    for split in ("train", "val", "test"):
        _write_split(tmp_path, split)
    (tmp_path / "val.jsonl").write_text("not-json\n", encoding="utf-8")
    monkeypatch.setattr(settings, "training_dataset_dir", str(tmp_path))

    with pytest.raises(TrainingDatasetUnavailable):
        ensure_training_dataset_ready()


def test_submit_checks_dataset_before_creating_a_queued_run(tmp_path, monkeypatch):
    _write_split(tmp_path, "train")
    _write_split(tmp_path, "val")
    monkeypatch.setattr(settings, "training_dataset_dir", str(tmp_path))
    service = TrainingService()
    monkeypatch.setattr(
        service,
        "_create_run",
        lambda _req: pytest.fail("preflight failure must not create a TrainingRun"),
    )

    with pytest.raises(TrainingDatasetUnavailable):
        service.submit(
            TrainRequest(training_type="full", actor=Actor(user_id="admin", role="admin"))
        )
