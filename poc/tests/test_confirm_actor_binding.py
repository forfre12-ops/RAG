"""[신원 무결성] /confirm·/relabel 이 corrected_by 를 인증 principal 로 바인딩하는지.

배경: 핸들러가 클라이언트가 보낸 req.actor.user_id 를 그대로 corrected_by 로 저장하고,
그 값은 promotion_service 에서 label_source='human_review' 의 labeler_id 로 흘러 평가진실
경로에 기록된다. reviewer 로 인증한 호출자가 actor.user_id='타인' 을 새기면 위조 신원이
사람검수 라벨러로 남는다(_is_machine_reviewer 는 머신 접두사만 막음). jwt sub(서명=위조불가)를
권위로 override 하고, api_key(공유키·신뢰된 KL 포털 전파)는 클라 값을 유지한다.

PG 불요 — ConfirmService/RelabelService 를 스텁으로 대체(핸들러 바인딩만 검증).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from lloydk.api.confirm import confirm, relabel, resolve_actor_user_id
from lloydk.schemas.confirm import ConfirmRequest, RelabelRequest
from lloydk.services.confirm_service import ConfirmResult, RelabelResult


# ── 순수 판정 ────────────────────────────────────────────────────────────────

def test_jwt_sub_overrides_spoofed_user_id():
    auth = {"mode": "jwt", "claims": SimpleNamespace(sub="real-reviewer")}
    eff, overridden = resolve_actor_user_id("someone-else", auth)
    assert eff == "real-reviewer" and overridden is True


def test_jwt_sub_matches_client_no_override_flag():
    auth = {"mode": "jwt", "claims": SimpleNamespace(sub="real-reviewer")}
    eff, overridden = resolve_actor_user_id("real-reviewer", auth)
    assert eff == "real-reviewer" and overridden is False


def test_api_key_keeps_client_user_id():
    # 공유키 = 개별 신원 없음. 신뢰된 KL 포털이 실 검수자를 actor.user_id 로 전파(설계 채널).
    auth = {"mode": "api_key", "actor_role": "kl_backend"}
    eff, overridden = resolve_actor_user_id("portal-forwarded-reviewer", auth)
    assert eff == "portal-forwarded-reviewer" and overridden is False


def test_none_claims_keeps_client_user_id():
    eff, overridden = resolve_actor_user_id("x", {"claims": None})
    assert eff == "x" and overridden is False


# ── 핸들러 통합 (PG 불요, 서비스 스텁) ────────────────────────────────────────

def _stub_confirm(monkeypatch) -> dict:
    captured: dict = {}

    def _fake(self, req):
        captured["user_id"] = req.actor.user_id
        return ConfirmResult(
            confirmation_id=uuid.uuid4(), confirmed_at="2026-07-02T00:00:00Z",
            persisted=True, warnings=[],
        )

    from lloydk.services import confirm_service as cs
    monkeypatch.setattr(cs.ConfirmService, "confirm", _fake)
    return captured


def _stub_relabel(monkeypatch) -> dict:
    captured: dict = {}

    def _fake(self, req):
        captured["user_id"] = req.actor.user_id
        return RelabelResult(
            relabel_id=uuid.uuid4(), queue_size=0, retrain_threshold=50,
            persisted=True, warnings=[],
        )

    from lloydk.services import confirm_service as cs
    monkeypatch.setattr(cs.RelabelService, "relabel", _fake)
    return captured


def _confirm_req(user_id: str) -> ConfirmRequest:
    return ConfirmRequest(
        doc_id="doc-1", confirmed_label="S1",
        actor={"user_id": user_id, "role": "reviewer"},
    )


def _relabel_req(user_id: str) -> RelabelRequest:
    return RelabelRequest(
        doc_id="doc-1", original_label="S2", corrected_label="S1",
        actor={"user_id": user_id, "role": "reviewer"},
    )


def test_confirm_handler_binds_jwt_sub(monkeypatch):
    captured = _stub_confirm(monkeypatch)
    auth = {"mode": "jwt", "claims": SimpleNamespace(sub="kl-reviewer-7"), "actor_role": "reviewer"}
    confirm(_confirm_req("IMPERSONATED"), auth=auth)
    assert captured["user_id"] == "kl-reviewer-7"  # 위조 값 폐기, 서명 sub 기록


def test_confirm_handler_apikey_keeps_forwarded(monkeypatch):
    captured = _stub_confirm(monkeypatch)
    auth = {"mode": "api_key", "actor_role": "kl_backend"}
    confirm(_confirm_req("portal-reviewer"), auth=auth)
    assert captured["user_id"] == "portal-reviewer"  # 신뢰된 포털 전파 유지


def test_relabel_handler_binds_jwt_sub(monkeypatch):
    captured = _stub_relabel(monkeypatch)
    auth = {"mode": "jwt", "claims": SimpleNamespace(sub="kl-reviewer-9"), "actor_role": "reviewer"}
    relabel(_relabel_req("IMPERSONATED"), auth=auth)
    assert captured["user_id"] == "kl-reviewer-9"
