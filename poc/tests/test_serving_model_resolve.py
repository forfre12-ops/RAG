"""서빙 모델 해석 — C-ver 폐곡선 마감 (활성 ModelVersion ↔ 서빙).

ClassifyService가 활성 ModelVersion.model_uri(로컬 디렉토리)를 우선 로드하고, 없으면 env
classifier_model_dir로 폴백하는지 검증. TESTING·serving_prefer_active_model=False는 env 직행.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import lloydk.services.classify_service as cs


@contextmanager
def _cm():
    yield object()


def _patch_active(monkeypatch, model_uri):
    import lloydk.db as dbmod
    import lloydk.repositories as repomod
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
    import lloydk.db as dbmod

    class _Boom:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dbmod, "session_scope", lambda: _Boom())
    assert cs._active_model_dir() is None


# ── _resolve_serving_model_dir ───────────────────────────────────────────────


def test_resolve_env_when_testing(monkeypatch):
    from lloydk.config import settings
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setattr(settings, "classifier_model_dir", "envdir")
    assert cs._resolve_serving_model_dir() == "envdir"


def test_resolve_prefers_active(tmp_path, monkeypatch):
    from lloydk.config import settings
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "serving_prefer_active_model", True)
    monkeypatch.setattr(settings, "classifier_model_dir", "envdir")
    monkeypatch.setattr(cs, "_active_model_dir", lambda: str(tmp_path))
    assert cs._resolve_serving_model_dir() == str(tmp_path)


def test_resolve_falls_back_to_env_when_no_active(monkeypatch):
    from lloydk.config import settings
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "serving_prefer_active_model", True)
    monkeypatch.setattr(settings, "classifier_model_dir", "envdir")
    monkeypatch.setattr(cs, "_active_model_dir", lambda: None)
    assert cs._resolve_serving_model_dir() == "envdir"


def test_resolve_disabled_uses_env(monkeypatch):
    from lloydk.config import settings
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

    from lloydk.api.app import app
    from lloydk.config import settings

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
