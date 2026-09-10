"""[NFR-OPS-01] 다른 프로세스가 바꾼 활성 모델을 각 프로세스가 스스로 따라간다.

종전에는 활성화 · 롤백 · /admin/model/reload 가 그 요청을 받은 프로세스만 다시 올려, 나머지 API
워커와 Celery 워커(비동기 분류 — tasks.classify_async 도 같은 싱글턴)가 옛 모델로 판별했다.
ClassifyService._maybe_refresh_model 이 serving_model_refresh_seconds 마다 활성 모델을 확인한다.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

import koipa.services.classify_service as cs


class _FakePipeline:
    built: list = []

    def __init__(self, model_dir=None):
        _FakePipeline.built.append(model_dir)
        if model_dir and "refuse" in str(model_dir):
            raise ValueError("model config.id2label does not match (fail-closed)")
        self.model_dir = Path(model_dir) if model_dir else None
        self._model = object() if model_dir else None


@pytest.fixture
def svc(monkeypatch):
    from koipa.config import settings

    _FakePipeline.built = []
    monkeypatch.setattr(cs, "InferencePipeline", _FakePipeline)
    monkeypatch.setattr(cs, "_resolve_serving_model_dir", lambda: "dir-a")
    monkeypatch.setattr(settings, "serving_model_refresh_seconds", 30.0)
    s = cs.ClassifyService()
    assert s._serving_dir == "dir-a"
    s._refresh_checked_at = None  # 다음 호출이 곧바로 확인하게
    return s


def test_same_active_model_is_noop(svc, monkeypatch):
    monkeypatch.setattr(cs, "_resolve_serving_model_dir_strict", lambda: "dir-a")
    before = svc.inference
    svc._maybe_refresh_model()
    assert svc.inference is before


def test_follows_model_changed_by_another_process(svc, monkeypatch):
    monkeypatch.setattr(cs, "_resolve_serving_model_dir_strict", lambda: "dir-b")
    svc._maybe_refresh_model()
    assert svc.inference.model_dir == Path("dir-b")
    assert svc._serving_dir == "dir-b"


def test_checks_at_most_once_per_interval(svc, monkeypatch):
    calls = {"n": 0}

    def _resolve():
        calls["n"] += 1
        return "dir-a"

    monkeypatch.setattr(cs, "_resolve_serving_model_dir_strict", _resolve)
    svc._maybe_refresh_model()
    svc._maybe_refresh_model()
    assert calls["n"] == 1, "간격 안에서는 DB 를 다시 보지 않아야 한다"


def test_zero_interval_disables(svc, monkeypatch):
    from koipa.config import settings

    monkeypatch.setattr(settings, "serving_model_refresh_seconds", 0)

    def _must_not_call():
        raise AssertionError("꺼져 있으면 확인하지 않아야 한다")

    monkeypatch.setattr(cs, "_resolve_serving_model_dir_strict", _must_not_call)
    svc._maybe_refresh_model()


def test_lookup_failure_keeps_current_model(svc, monkeypatch):
    """DB 가 잠깐 끊겨도 env 모델로 넘어가지 않는다 — 모르면 바꾸지 않는다."""
    def _boom():
        raise cs._ActiveLookupFailed("OperationalError: db down")

    monkeypatch.setattr(cs, "_resolve_serving_model_dir_strict", _boom)
    before = svc.inference
    svc._maybe_refresh_model()
    assert svc.inference is before
    assert svc._serving_dir == "dir-a"


def test_refused_reload_keeps_old_model_and_is_not_retried(svc, monkeypatch):
    monkeypatch.setattr(cs, "_resolve_serving_model_dir_strict", lambda: "refuse-dir")
    before = svc.inference
    n_built = len(_FakePipeline.built)
    svc._maybe_refresh_model()  # 적재 거부 — 예외가 분류로 새면 안 된다
    assert svc.inference is before
    assert svc._serving_dir == "dir-a"
    svc._refresh_checked_at = None
    svc._maybe_refresh_model()  # 같은 대상은 다시 적재하지 않는다(무거운 적재 반복 방지)
    assert len(_FakePipeline.built) == n_built + 1


def test_explicit_reload_endpoint_path_still_resolves(svc, monkeypatch):
    """/admin/model/reload 경로(인자 없음)는 종전대로 지금 활성 모델을 푼다."""
    monkeypatch.setattr(cs, "_resolve_serving_model_dir", lambda: "dir-c")
    info = svc.reload_model()
    assert info["model_dir"] == "dir-c"
    assert svc._serving_dir == "dir-c"


def test_strict_resolver_raises_on_db_error(monkeypatch):
    import koipa.db as dbmod
    from koipa.config import settings

    class _Boom:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "serving_prefer_active_model", True)
    monkeypatch.setattr(dbmod, "session_scope", lambda: _Boom())
    with pytest.raises(cs._ActiveLookupFailed):
        cs._resolve_serving_model_dir_strict()


def test_strict_resolver_returns_active_dir(tmp_path, monkeypatch):
    import koipa.db as dbmod
    import koipa.repositories as repomod
    from koipa.config import settings
    from types import SimpleNamespace

    @contextmanager
    def _cm():
        yield object()

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(settings, "serving_prefer_active_model", True)
    monkeypatch.setattr(settings, "classifier_model_dir", "envdir")
    monkeypatch.setattr(dbmod, "session_scope", _cm)
    monkeypatch.setattr(repomod, "TrainingRepo",
                        lambda db: SimpleNamespace(get_active=lambda: SimpleNamespace(model_uri=str(tmp_path))))
    assert cs._resolve_serving_model_dir_strict() == str(tmp_path)
    # 활성 버전이 없으면 env — 이것은 '조회 실패'가 아니라 '없음'이다.
    monkeypatch.setattr(repomod, "TrainingRepo", lambda db: SimpleNamespace(get_active=lambda: None))
    assert cs._resolve_serving_model_dir_strict() == "envdir"


def test_strict_resolver_uses_env_when_testing(monkeypatch):
    from koipa.config import settings

    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setattr(settings, "classifier_model_dir", "envdir")
    assert cs._resolve_serving_model_dir_strict() == "envdir"


def test_same_model_dir_normalizes():
    assert cs._same_model_dir(None, "")
    assert cs._same_model_dir("a/b", "a/b/")
    assert not cs._same_model_dir("a/b", None)
    assert not cs._same_model_dir("a/b", "a/c")


def test_classify_goes_through_refresh(monkeypatch):
    from koipa.schemas.classify import ClassifyRequest

    seen = {"n": 0}
    monkeypatch.setattr(cs.ClassifyService, "_maybe_refresh_model",
                        lambda self: seen.__setitem__("n", seen["n"] + 1))
    svc = cs.ClassifyService()
    svc.classify(ClassifyRequest(doc_id="refresh-1", content="사내 공지사항입니다. 워크숍 일정을 안내합니다."))
    assert seen["n"] == 1
