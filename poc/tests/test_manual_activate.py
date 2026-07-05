"""[G1] 수동 모델 활성화(activate_model_manually) — deploy gate 적용·force 우회.

계약:
  · 없는 버전 → activated False, reason=version_not_found
  · 현재 활성본 대비 게이트 통과(회귀 없음) → activated True, forced False
  · 게이트 실패(고등급 미탐 fnr 악화) + force=False → blocked True, activated False, 활성 무변경
  · 게이트 실패 + force=True → activated True, forced True (감사 우회)
  · [배포전 하드닝 P0#①] deploy_gate_manual_require_locked_eval=True + locked 미준비
    → 회귀 gate를 통과해도 blocked True(locked_eval_not_ready); force=True로만 우회(forced True).
    기본(False)에선 locked 없이도 활성(현행 보존).

실 PG 필요(ModelVersion 시드). 미가용 시 skip.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import lloydk.config as config_mod
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


def test_manual_locked_eval_gate_blocks_then_force_overrides(monkeypatch):
    """[배포전 하드닝 P0#①] 하드닝 프로파일서 수동 활성이 locked-eval readiness를 요구.

    회귀 gate는 통과(성능↑)해도 locked_gold_eval 미준비면 blocked — 미검증 모델의 무심한
    GA 활성 차단. force=True로만 우회(forced True, 감사됨). 기본(플래그 False)에선 현행대로 활성.
    """
    # 무실데이터(locked 비어있음)에서 수동 요구 게이트만 ON — locked_eval_jsonl은 이미 "" (미준비).
    monkeypatch.setattr(config_mod.settings, "deploy_gate_manual_require_locked_eval", True)
    monkeypatch.setattr(config_mod.settings, "locked_eval_jsonl", "")

    base = f"v-base-{uuid.uuid4().hex[:6]}"
    good = f"v-good-{uuid.uuid4().hex[:6]}"
    _seed(base, {"fnr_high": 0.10, "f1_macro": 0.80}, active=True)
    _seed(good, {"fnr_high": 0.08, "f1_macro": 0.82})  # 회귀 gate는 통과할 후보
    try:
        # 회귀 gate 통과에도 locked 미준비라 blocked.
        res = activate_model_manually(good, force=False)
        assert res["activated"] is False
        assert res["blocked"] is True
        assert "locked_eval_not_ready" in res["reason"]
        assert res["locked"]["ready"] is False
        assert _active_label() == base  # 활성 무변경

        # force=True로만 우회(미검증 승인은 forced=True로 감사에 남는다).
        res2 = activate_model_manually(good, force=True)
        assert res2["activated"] is True
        assert res2["forced"] is True
        assert _active_label() == good
    finally:
        _cleanup([base, good])


def test_manual_locked_gate_off_activates_without_locked(monkeypatch):
    """플래그 기본(False)=현행 보존: locked 미준비여도 회귀 gate만 통과하면 force 없이 활성."""
    monkeypatch.setattr(config_mod.settings, "deploy_gate_manual_require_locked_eval", False)
    base = f"v-base-{uuid.uuid4().hex[:6]}"
    good = f"v-good-{uuid.uuid4().hex[:6]}"
    _seed(base, {"fnr_high": 0.10, "f1_macro": 0.80}, active=True)
    _seed(good, {"fnr_high": 0.08, "f1_macro": 0.82})
    try:
        res = activate_model_manually(good, force=False)
        assert res["activated"] is True
        assert res["forced"] is False
        assert res["locked"]["ready"] is False  # locked는 미준비지만 요구 안 함 → 노출만
        assert _active_label() == good
    finally:
        _cleanup([base, good])


def test_force_requires_reason_and_actor_when_hardened(monkeypatch):
    """[P0#①-b/c] 하드닝: force 우회에 사유+식별된 actor 필수 — 없으면 blocked, 둘 다 있으면 활성."""
    monkeypatch.setattr(config_mod.settings, "deploy_gate_manual_require_locked_eval", True)
    monkeypatch.setattr(config_mod.settings, "locked_eval_jsonl", "")  # locked 미준비 → 게이트 실패
    monkeypatch.setattr(config_mod.settings, "manual_activate_force_requires_reason", True)
    base = f"v-base-{uuid.uuid4().hex[:6]}"
    good = f"v-good-{uuid.uuid4().hex[:6]}"
    _seed(base, {"fnr_high": 0.10, "f1_macro": 0.80}, active=True)
    _seed(good, {"fnr_high": 0.08, "f1_macro": 0.82})
    try:
        # force=True 이지만 사유·actor 없음 → 둘 다 요구, blocked.
        r1 = activate_model_manually(good, force=True)
        assert r1["activated"] is False and r1["blocked"] is True
        assert "force_reason_required" in r1["reason"]
        assert "force_actor_required" in r1["reason"]
        assert _active_label() == base

        # 사유는 있으나 actor 없음(api_key 공유키 모드) → 여전히 blocked(익명 강제활성 차단).
        r2 = activate_model_manually(good, force=True, reason="긴급 배포")
        assert r2["activated"] is False and r2["blocked"] is True
        assert "force_actor_required" in r2["reason"]
        assert _active_label() == base

        # force + 사유 + 식별된 actor(jwt) → forced 활성, 사유가 결과 reason 에 남는다.
        r3 = activate_model_manually(
            good, force=True, reason="긴급 파일럿 배포 승인 by VP", actor_id="kl-admin-7"
        )
        assert r3["activated"] is True and r3["forced"] is True
        assert "긴급 파일럿 배포" in r3["reason"]
        assert _active_label() == good
    finally:
        _cleanup([base, good])


def test_forced_activation_writes_audit_chain_row(monkeypatch):
    """[P0#①-c] 강제(미검증) 활성은 model.activate.forced 감사 체인 행을 남긴다(누가·왜·우회대상)."""
    from lloydk.db.models import AuditLog

    monkeypatch.setattr(config_mod.settings, "deploy_gate_manual_require_locked_eval", True)
    monkeypatch.setattr(config_mod.settings, "locked_eval_jsonl", "")
    monkeypatch.setattr(config_mod.settings, "manual_activate_force_requires_reason", True)
    actor = f"kl-admin-{uuid.uuid4().hex[:6]}"
    base = f"v-base-{uuid.uuid4().hex[:6]}"
    cand = f"v-cand-{uuid.uuid4().hex[:6]}"
    _seed(base, {"fnr_high": 0.10, "f1_macro": 0.80}, active=True)
    _seed(cand, {"fnr_high": 0.08, "f1_macro": 0.82})
    try:
        r = activate_model_manually(cand, force=True, reason="긴급 승인", actor_id=actor)
        assert r["activated"] is True and r["forced"] is True
        with session_scope() as db:
            rows = db.query(AuditLog).filter(
                AuditLog.action == "model.activate.forced",
                AuditLog.actor_id == actor,
            ).all()
        assert len(rows) == 1, f"강제활성 감사행 1건 기대, got {len(rows)}"
        assert rows[0].target_id is not None
        assert ":" in (rows[0].payload_hash or "")  # 체인 패킹(prev16:payload32)
    finally:
        _cleanup([base, cand])
        with session_scope() as db:
            db.query(AuditLog).filter(AuditLog.actor_id == actor).delete(
                synchronize_session=False
            )
