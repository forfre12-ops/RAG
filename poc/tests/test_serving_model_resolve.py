"""서빙 모델 해석 — C-ver 폐곡선 마감 (활성 ModelVersion ↔ 서빙).

ClassifyService가 활성 ModelVersion.model_uri(로컬 디렉토리)를 우선 로드하고, 없으면 env
classifier_model_dir로 폴백하는지 검증. TESTING·serving_prefer_active_model=False는 env 직행.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import koipa.services.classify_service as cs


@contextmanager
def _cm():
    yield object()


def _patch_active(monkeypatch, model_uri):
    import koipa.db as dbmod
    import koipa.repositories as repomod
    monkeypatch.setattr(dbmod, "session_scope", _cm)
    active = SimpleNamespace(model_uri=model_uri) if model_uri is not None else None
    monkeypatch.setattr(repomod, "TrainingRepo", lambda db: SimpleNamespace(get_active=lambda: active))


# ── _active_model_dir ────────────────────────────────────────────────────────


def test_active_dir_valid_local(tmp_path, monkeypatch):
    _patch_active(monkeypatch, str(tmp_path))   # tmp_path는 실존 디렉토리
    assert cs._active_model_dir() == str(tmp_path)


def test_active_dir_none_when_uri_missing_path(monkeypatch):
    _patch_active(monkeypatch, "/no/such/dir/xyz123")
    assert cs._active_model_dir() is None


def test_active_dir_none_when_no_active(monkeypatch):
    _patch_active(monkeypatch, None)
    assert cs._active_model_dir() is None


def test_active_dir_none_on_db_error(monkeypatch):
    import koipa.db as dbmod

    class _Boom:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dbmod, "session_scope", lambda: _Boom())
    assert cs._active_model_dir() is None


# ── _resolve_serving_model_dir ───────────────────────────────────────────────


def test_resolve_env_when_testing(monkeypatch):
    from koipa.config import settings
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setattr(settings, "classifier_model_dir", "envdir")
    assert cs._resolve_serving_model_dir() == "envdir"


def test_resolve_prefers_active(tmp_path, monkeypatch):
    from koipa.config import settings
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "serving_prefer_active_model", True)
    monkeypatch.setattr(settings, "classifier_model_dir", "envdir")
    monkeypatch.setattr(cs, "_active_model_dir", lambda: str(tmp_path))
    assert cs._resolve_serving_model_dir() == str(tmp_path)


def test_resolve_falls_back_to_env_when_no_active(monkeypatch):
    from koipa.config import settings
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "serving_prefer_active_model", True)
    monkeypatch.setattr(settings, "classifier_model_dir", "envdir")
    monkeypatch.setattr(cs, "_active_model_dir", lambda: None)
    assert cs._resolve_serving_model_dir() == "envdir"


def test_resolve_disabled_uses_env(monkeypatch):
    from koipa.config import settings
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "serving_prefer_active_model", False)
    monkeypatch.setattr(settings, "classifier_model_dir", "envdir")
    # disabled면 _active_model_dir 호출 자체가 없어야(폴백 직행)
    monkeypatch.setattr(cs, "_active_model_dir", lambda: (_ for _ in ()).throw(AssertionError("called")))
    assert cs._resolve_serving_model_dir() == "envdir"


# ── reload_model (런타임 핫리로드) ───────────────────────────────────────────


def test_reload_model_rebuilds_inference():
    svc = cs.ClassifyService()        # TESTING → env(빈값) → rule-fallback, 모델 로드 없음
    old = svc.inference
    info = svc.reload_model()
    assert info["reloaded"] is True
    assert svc.inference is not old   # 추론기 재구성됨
    assert "model_version" in info


def test_reload_model_reports_fallback_when_no_dir(monkeypatch):
    monkeypatch.setattr(cs, "_resolve_serving_model_dir", lambda: None)
    svc = cs.ClassifyService()
    info = svc.reload_model()
    assert info["model_version"] == "rule-fallback"
    assert info["model_loaded"] is False
    assert info["model_dir"] is None


@pytest.mark.fullstack
def test_admin_reload_endpoint_requires_admin_and_reloads():
    from fastapi.testclient import TestClient

    from koipa.api.app import app
    from koipa.config import settings

    with TestClient(app) as cli:
        # 비admin은 거부
        r_forbidden = cli.post("/api/v1/admin/model/reload",
                               headers={"X-API-Key": settings.api_key, "X-Actor-Role": "reviewer"})
        assert r_forbidden.status_code in (401, 403)
        # admin은 200 + reloaded
        r = cli.post("/api/v1/admin/model/reload",
                     headers={"X-API-Key": settings.api_key, "X-Actor-Role": "admin"})
        assert r.status_code == 200
        body = r.json()
        assert body["reloaded"] is True
        assert "model_version" in body


# ── 리로드 거부는 500 이 아니라 409 다 (2026-09-05) ──────────────────────────
#
# 실측(로컬 full-train 스택 상대 콘솔 e2e --allow-writes):
#
#   POST /api/v1/admin/model/reload
#     → InferencePipeline._load_model()
#     → ValueError: model config.id2label ... refusing to load (fail-closed)
#     → 처리되지 않고 **HTTP 500**
#
# 거부 자체는 옳다 — 등급 매핑이 어긋난 모델을 올리면 softmax 인덱스가 엉뚱한 등급에
# 붙어 미탐이 난다. 문제는 그것이 500 으로 나가서 화면에 "서버 내부 오류가 발생했습니다"
# 만 뜬다는 것이다. 운영자가 무엇을 해야 할지 알 수 없고 진짜 장애와 구분도 안 된다.
# 이 사업의 오류 계약은 심볼릭 코드 없이 **HTTP 상태로 분기**한다(ICD).
def test_reload_refusal_is_409_not_500(monkeypatch):
    """등급 매핑 불일치로 거부할 때 409 와 사유가 나온다 — 500 이면 안 된다."""
    from fastapi.testclient import TestClient

    import koipa.services.classify_service as cs
    from koipa.api.app import app
    from koipa.config import settings

    class _Refusing:
        def reload_model(self):
            raise ValueError(
                "model config.id2label is missing/invalid or its code set does not "
                "match the active grade registry; refusing to load (fail-closed)"
            )

    monkeypatch.setattr(cs.ClassifyService, "get_instance", staticmethod(lambda: _Refusing()))

    with TestClient(app, raise_server_exceptions=False) as cli:
        r = cli.post("/api/v1/admin/model/reload",
                     headers={"X-API-Key": settings.api_key, "X-Actor-Role": "admin"})
    assert r.status_code == 409, "거부는 409 여야 한다 (실제 %s)" % r.status_code
    detail = r.json().get("detail", "")
    # 운영자가 무엇을 봐야 하는지 문구에 남는다.
    assert "id2label" in detail, detail
    assert "미탐" in detail, detail
