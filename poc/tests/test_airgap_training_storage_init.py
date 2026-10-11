"""The air-gap training volumes must be writable by the non-root runtime user."""
from __future__ import annotations

from pathlib import Path


_COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.airgap.yml"


def test_airgap_training_storage_is_initialized_before_api_and_worker() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")

    assert "training-storage-init:" in text
    assert 'user: "0:0"' in text
    assert "chown -R 1000:1000 /app/artifacts_out /app/datasets/demo_retrain" in text
    assert "chmod -R u+rwX,g+rwX,o-rwx /app/artifacts_out /app/datasets/demo_retrain" in text
    assert text.count("training-storage-init: { condition: service_completed_successfully }") == 2
