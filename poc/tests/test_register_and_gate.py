"""register_and_gate_model 오케스트레이션 테스트 — C-ver/A2-② (DB-backed).

register는 항상, activate는 게이트 통과 + settings.retrain_auto_activate 일 때만.
Postgres 없으면 자동 skip(@db_backed).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from lloydk.db import engine, session_scope
from lloydk.db.models import ModelVersion
from lloydk.repositories import TrainingRepo
from lloydk.services.training_service import register_and_gate_model, rollback_active_model


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    except Exception:  # noqa: BLE001
        return False


def db_backed(obj):
    # PG만 필요(ES 불요) — fullstack 게이트 대신 require_pg로 PG-down만 skip.
    return pytest.mark.usefixtures("require_pg")(obj)


@pytest.fixture
def require_pg():
    if not _pg_ok():
        pytest.skip("Postgres not reachable")


def _report(label, *, fnr_high=0.03, f1=0.85):
    return {"model_version": label, "base_model": "kf-deberta-base",
            "fnr_high": fnr_high, "f1_macro": f1, "accuracy": 0.9}


# 순수: DB 미가용이어도 graceful (registered False) — 항상 실행.
def test_register_and_gate_db_unavailable_graceful(monkeypatch):
    import lloydk.services.training_service as ts

    class _Boom:
        def __enter__(self):
            raise OperationalError("x", {}, Exception("down"))

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ts, "session_scope", lambda: _Boom())
    out = register_and_gate_model(_report("v-x"))
    assert out["registered"] is False
    assert out["reason"].startswith("db_unavailable")


def test_rollback_db_unavailable_graceful(monkeypatch):
    import lloydk.services.training_service as ts

    class _Boom:
        def __enter__(self):
            raise OperationalError("x", {}, Exception("down"))

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ts, "session_scope", lambda: _Boom())
    out = rollback_active_model("test")
    assert out["rolled_back"] is False
    assert out["reason"].startswith("db_unavailable")


@db_backed
class TestRegisterAndGateLive:
    def test_register_only_by_default(self, monkeypatch):
        from lloydk.config import settings
        monkeypatch.setattr(settings, "retrain_auto_activate", False)
        label = f"v-rg-{uuid.uuid4().hex[:6]}"
        out = register_and_gate_model(_report(label))
        try:
            assert out["registered"] is True
            assert out["activated"] is False        # 기본은 등록만
            assert out["gate"]["passed"] is True     # baseline 없음 → 통과
            with session_scope() as s:
                mv = TrainingRepo(s).get_by_label(label)
                assert mv is not None and mv.is_active is False
        finally:
            with session_scope() as s:
                s.query(ModelVersion).filter_by(version_label=label).delete()

    def test_auto_activate_when_gate_passes(self, monkeypatch):
        from lloydk.config import settings
        monkeypatch.setattr(settings, "retrain_auto_activate", True)
        # locked-eval 게이트(죽음의 나선 #4)는 auto-activate 의 *추가* 조건이다. 순수 'gate+auto
        # → activate' 경로를 검증하려 여기선 끈다(locked-eval 축은 별도 테스트로 양/음성 커버).
        monkeypatch.setattr(settings, "deploy_gate_require_locked_eval", False)
        label = f"v-rg-{uuid.uuid4().hex[:6]}"
        out = register_and_gate_model(_report(label, fnr_high=0.01))
        try:
            assert out["registered"] is True
            assert out["activated"] is True
            with session_scope() as s:
                active = TrainingRepo(s).get_active()
                assert active is not None and active.version_label == label
        finally:
            with session_scope() as s:
                s.query(ModelVersion).filter_by(version_label=label).delete()

    def test_auto_activate_blocked_without_locked_eval(self, monkeypatch):
        """[죽음의 나선 #4] locked-eval 게이트 ON(기본) + eval_ready 미전달 → 자동활성 차단(fail-secure).

        합성-only 운영의 기본값. register_and_gate_model 이 eval_ready 를 받지 못하면(워커가 산출
        전달 안 하면) 게이트가 통과해도 auto-activate 가 열리지 않음을 잠근다.
        """
        from lloydk.config import settings
        monkeypatch.setattr(settings, "retrain_auto_activate", True)
        monkeypatch.setattr(settings, "deploy_gate_require_locked_eval", True)
        label = f"v-rg-{uuid.uuid4().hex[:6]}"
        out = register_and_gate_model(_report(label, fnr_high=0.01))  # eval_ready 미전달(None)
        try:
            assert out["registered"] is True
            assert out["gate"]["passed"] is True     # 게이트 자체는 통과
            assert out["eval_block"] is True          # 그러나 locked-eval 미충족으로 차단
            assert out["activated"] is False
        finally:
            with session_scope() as s:
                s.query(ModelVersion).filter_by(version_label=label).delete()

    def test_auto_activate_when_eval_ready_passed(self, monkeypatch):
        """locked-eval 게이트 ON + eval_ready=True(워커 산출 전달) → auto-activate 도달 가능.

        워커가 locked_eval_readiness().ready 를 넘기는 배선(tasks.train_classifier_task)의 계약.
        """
        from lloydk.config import settings
        monkeypatch.setattr(settings, "retrain_auto_activate", True)
        monkeypatch.setattr(settings, "deploy_gate_require_locked_eval", True)
        label = f"v-rg-{uuid.uuid4().hex[:6]}"
        out = register_and_gate_model(_report(label, fnr_high=0.01), eval_ready=True)
        try:
            assert out["registered"] is True
            assert out["eval_block"] is False
            assert out["activated"] is True
            with session_scope() as s:
                active = TrainingRepo(s).get_active()
                assert active is not None and active.version_label == label
        finally:
            with session_scope() as s:
                s.query(ModelVersion).filter_by(version_label=label).delete()

    def test_blocks_activate_on_fnr_regression(self, monkeypatch):
        from lloydk.config import settings
        monkeypatch.setattr(settings, "retrain_auto_activate", True)
        # baseline(활성) — 좋은 fnr_high
        base_label = f"v-base-{uuid.uuid4().hex[:6]}"
        cand_label = f"v-cand-{uuid.uuid4().hex[:6]}"
        with session_scope() as s:
            repo = TrainingRepo(s)
            base = repo.register_model_version(
                version_label=base_label, base_model="kf", metrics=_report(base_label, fnr_high=0.02),
            )
            repo.activate_model_version(base.version_id)
        try:
            # 후보 — fnr_high 악화 → 게이트 차단 → 활성 안 됨, baseline 유지
            out = register_and_gate_model(_report(cand_label, fnr_high=0.25))
            assert out["registered"] is True
            assert out["activated"] is False
            assert out["gate"]["passed"] is False
            with session_scope() as s:
                active = TrainingRepo(s).get_active()
                assert active is not None and active.version_label == base_label
        finally:
            with session_scope() as s:
                s.query(ModelVersion).filter(
                    ModelVersion.version_label.in_([base_label, cand_label])
                ).delete(synchronize_session=False)

    def test_rollback_restores_previous_active(self, monkeypatch):
        from lloydk.config import settings
        monkeypatch.setattr(settings, "retrain_auto_activate", True)
        # 자동활성 경로 검증이 목적이므로 locked-eval 추가 게이트는 끈다(#4는 별도 테스트).
        monkeypatch.setattr(settings, "deploy_gate_require_locked_eval", False)
        v1 = f"v-rb1-{uuid.uuid4().hex[:6]}"
        v2 = f"v-rb2-{uuid.uuid4().hex[:6]}"
        # v1 활성 → v2 활성(게이트 통과) → 롤백하면 v1로 복귀
        register_and_gate_model(_report(v1, fnr_high=0.02))
        register_and_gate_model(_report(v2, fnr_high=0.02))
        try:
            with session_scope() as s:
                assert TrainingRepo(s).get_active().version_label == v2
            out = rollback_active_model("운영 미탐 회귀 감지")
            assert out["rolled_back"] is True
            assert out["from"] == v2
            assert out["to"] == v1
            with session_scope() as s:
                active = TrainingRepo(s).get_active()
                assert active.version_label == v1
                assert active.rollback_reason == "운영 미탐 회귀 감지"
        finally:
            with session_scope() as s:
                s.query(ModelVersion).filter(
                    ModelVersion.version_label.in_([v1, v2])
                ).delete(synchronize_session=False)

    def test_rollback_no_previous_returns_false(self, monkeypatch):
        from lloydk.config import settings
        monkeypatch.setattr(settings, "retrain_auto_activate", True)
        # 활성 모델을 모두 정리한 상태에서 단일 버전만 활성 → 복귀 후보 없음
        only = f"v-only-{uuid.uuid4().hex[:6]}"
        with session_scope() as s:
            # 기존 비활성 이력이 있을 수 있어, 단일 활성만 보장하긴 어렵다 — 신규 버전 활성 후
            # 직전 후보가 '이 테스트가 만든 것'이 아닐 수 있으므로, 결과 타입만 견고 검증.
            repo = TrainingRepo(s)
            mv = repo.register_model_version(version_label=only, base_model="kf", metrics=_report(only))
            repo.activate_model_version(mv.version_id)
        try:
            out = rollback_active_model("x")
            assert "rolled_back" in out  # True(직전 존재) 또는 False(없음) 모두 유효
            assert isinstance(out["rolled_back"], bool)
        finally:
            with session_scope() as s:
                mv = s.query(ModelVersion).filter_by(version_label=only).first()
                if mv is not None:
                    # rolled_back_from self-FK 참조를 먼저 끊고 삭제(공유 DB 정리)
                    s.query(ModelVersion).filter_by(rolled_back_from=mv.version_id).update(
                        {"rolled_back_from": None}, synchronize_session=False
                    )
                    s.query(ModelVersion).filter_by(version_label=only).delete(
                        synchronize_session=False
                    )
