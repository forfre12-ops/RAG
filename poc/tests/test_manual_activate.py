"""[G1] 수동 모델 활성화(activate_model_manually) — deploy gate 적용·force 우회.

계약:
  · 없는 버전 → activated False, reason=version_not_found
  · 현재 활성본 대비 게이트 통과(회귀 없음) → activated True, forced False
  · 게이트 실패(고등급 미탐 fnr 악화) + force=False → blocked True, activated False, 활성 무변경
  · 게이트 실패 + force=True → activated True, forced True (감사 우회)

실 PG 필요(ModelVersion 시드). 미가용 시 skip.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from lloydk.db import engine, session_scope
from lloydk.db.models import ModelVersion
from lloydk.repositories.training_repo import TrainingRepo
from lloydk.services.training_service import activate_model_manually


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(autouse=True)
def _require_pg():
    if not _pg_ok():
        pytest.skip("Postgres not reachable")


def _seed(label, metrics, *, active=False):
    with session_scope() as db:
        repo = TrainingRepo(db)
        mv = repo.register_model_version(version_label=label, base_model="test", metrics=metrics)
        db.flush()
        if active:
            repo.activate_model_version(mv.version_id)


def _active_label():
    with session_scope() as db:
        a = TrainingRepo(db).get_active()
        return a.version_label if a else None


def _cleanup(labels):
    with session_scope() as db:
        db.query(ModelVersion).filter(
            ModelVersion.version_label.in_(labels)
        ).delete(synchronize_session=False)


def test_version_not_found():
    res = activate_model_manually(f"v-nope-{uuid.uuid4().hex[:8]}")
    assert res["activated"] is False
    assert res["blocked"] is False
    assert res["reason"] == "version_not_found"


def test_gate_pass_activates():
    base = f"v-base-{uuid.uuid4().hex[:6]}"
    cand = f"v-cand-{uuid.uuid4().hex[:6]}"
    _seed(base, {"fnr_high": 0.10, "f1_macro": 0.80}, active=True)
    _seed(cand, {"fnr_high": 0.08, "f1_macro": 0.82})  # 미탐↓·성능↑ = 통과
    try:
        res = activate_model_manually(cand)
        assert res["activated"] is True
        assert res["forced"] is False
        assert _active_label() == cand
    finally:
        _cleanup([base, cand])


def test_gate_fail_blocks_without_force_then_force_overrides():
    base = f"v-base-{uuid.uuid4().hex[:6]}"
    bad = f"v-bad-{uuid.uuid4().hex[:6]}"
    _seed(base, {"fnr_high": 0.05, "f1_macro": 0.85}, active=True)
    _seed(bad, {"fnr_high": 0.40, "f1_macro": 0.50})  # 고등급 미탐 대폭 악화 = 거부
    try:
        res = activate_model_manually(bad, force=False)
        assert res["activated"] is False
        assert res["blocked"] is True
        assert "deploy_gate_failed" in res["reason"]
        assert _active_label() == base  # 활성 무변경

        res2 = activate_model_manually(bad, force=True)
        assert res2["activated"] is True
        assert res2["forced"] is True
        assert _active_label() == bad
    finally:
        _cleanup([base, bad])
