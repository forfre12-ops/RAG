"""[SEC-4 / SEC-2] 데모 purge 안전장치 — blast-radius 캡 + 운영 비활성 게이트.

purge_demo_data 는 created_by='demo-console' 스코프를 물리삭제한다. created_by 가 클라이언트
제어 값(업로드 actor.user_id)이라 실 데이터가 데모 마커로 오태깅될 수 있어:
  - [SEC-4] 삭제 대상이 데모 규모(DEMO_PURGE_MAX_DOCS)를 넘으면 어떤 삭제도 없이 409 거부.
  - [SEC-2] demo_console_enabled=False(하드닝 프로파일)면 스코프 조회조차 없이 404.
DB 불요 — session_scope 를 가짜로 대체해 안전장치 로직만 검증.
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from koipa.api import admin as admin_mod


class _FakeRes:
    def __init__(self, scalar_val: int = 0):
        self.rowcount = 0
        self._scalar = scalar_val

    def scalar(self):
        return self._scalar


class _FakeDB:
    """count(*) 쿼리엔 지정 스코프 수를, DELETE 엔 rowcount 0 을 반환. 실행 SQL 을 로깅."""

    def __init__(self, scoped_count: int, log: list[str]):
        self._count = scoped_count
        self._log = log

    def execute(self, stmt, params=None):
        s = str(stmt)
        self._log.append(s)
        if "count(" in s.lower():
            return _FakeRes(self._count)
        return _FakeRes(0)


def _patch_scope(monkeypatch, scoped_count: int, log: list[str]):
    @contextlib.contextmanager
    def _fake_scope():
        yield _FakeDB(scoped_count, log)

    monkeypatch.setattr("koipa.db.session_scope", _fake_scope)


def test_demo_purge_refuses_over_cap(monkeypatch):
    """스코프 문서 수가 안전캡 초과 → 409 거부 + 어떤 DELETE 도 실행 안 됨(fail-safe)."""
    log: list[str] = []
    _patch_scope(monkeypatch, admin_mod.DEMO_PURGE_MAX_DOCS + 1, log)
    with pytest.raises(HTTPException) as ei:
        admin_mod.purge_demo_data()
    assert ei.value.status_code == 409
    assert not any("DELETE" in s.upper() for s in log), "캡 초과 시 삭제가 실행되면 안 됨"


def test_demo_purge_proceeds_under_cap(monkeypatch):
    """스코프 문서 수가 캡 이하 → 정상 삭제 진행 + purged=True."""
    log: list[str] = []
    _patch_scope(monkeypatch, 7, log)
    resp = admin_mod.purge_demo_data()
    assert resp.purged is True
    assert any("DELETE" in s.upper() for s in log), "캡 이하면 삭제가 실행돼야 함"


def test_demo_purge_disabled_returns_404(monkeypatch):
    """demo_console_enabled=False(하드닝 프로파일) → 404, 스코프 조회조차 안 함(SEC-2)."""
    log: list[str] = []
    _patch_scope(monkeypatch, 7, log)
    monkeypatch.setattr(
        "koipa.config.settings", SimpleNamespace(demo_console_enabled=False)
    )
    with pytest.raises(HTTPException) as ei:
        admin_mod.purge_demo_data()
    assert ei.value.status_code == 404
    assert not log, "비활성 시 DB 접근조차 없어야 함"
