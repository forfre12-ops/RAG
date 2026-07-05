"""[G1 회귀 봉인] /admin/model/activate 가 인증 주체(actor)를 감사에 정직히 전달하는지.

과거 admin.activate_model 은 auth.get("actor_id")를 읽었으나 require_auth 반환 dict에는
그 키가 없어(jwt=claims.sub, api_key=공유키·신원 없음) actor_id 가 **항상 None**으로
익명 기록됐다(강제활성=보안민감 액션인데 서비스레벨 actor 귀속 유실). jwt subject 를 실
actor 로 전달하고, api_key 모드는 사용자 신원이 없어 None 임을 고정한다.

PG 불요 — activate_model_manually 를 스텁으로 대체(activated=False → reload 경로 미진입).
"""
from __future__ import annotations

from types import SimpleNamespace

from lloydk.api.admin import ActivateModelRequest, activate_model


def _patch_capture(monkeypatch) -> dict:
    captured: dict = {}

    def _fake(version_label, *, force, actor_id, actor_role, reason=""):
        captured.update(
            version_label=version_label,
            force=force,
            reason=reason,
            actor_id=actor_id,
            actor_role=actor_role,
        )
        # activated=False → 엔드포인트가 ClassifyService.reload_model() 경로를 타지 않음.
        return {
            "activated": False,
            "blocked": False,
            "forced": False,
            "version_label": version_label,
            "reason": "test",
        }

    import lloydk.services.training_service as ts

    monkeypatch.setattr(ts, "activate_model_manually", _fake)
    return captured


def test_activate_records_jwt_subject_as_actor(monkeypatch):
    captured = _patch_capture(monkeypatch)
    auth = {
        "mode": "jwt",
        "claims": SimpleNamespace(sub="kl-admin-42"),
        "actor_role": "admin",
        "actor_roles": ("admin",),
    }
    resp = activate_model(ActivateModelRequest(version_label="v-x"), auth=auth)
    assert captured["actor_id"] == "kl-admin-42"  # 과거 None 버그 봉인
    assert captured["actor_role"] == "admin"
    assert resp.activated is False


def test_activate_apikey_actor_is_none_not_crash(monkeypatch):
    captured = _patch_capture(monkeypatch)
    auth = {"mode": "api_key", "actor_role": "admin", "actor_roles": ("admin",)}
    activate_model(ActivateModelRequest(version_label="v-y"), auth=auth)
    assert captured["actor_id"] is None  # 공유키=사용자 신원 없음(정직)
    assert captured["actor_role"] == "admin"
