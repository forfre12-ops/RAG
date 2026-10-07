"""DB 연결 타임아웃 주입 — 운영 부팅 행(hang) 방지 단위 테스트.

PG 일시 불가 시 연결이 무한 대기하면 GradeRegistry·추론기 생성이 블록되므로,
postgresql 드라이버에 connect_timeout을 주입한다. 본 테스트는 주입 규칙을 검증한다.
"""

from __future__ import annotations

from koipa.db import session as _sess


def test_postgres_url_gets_connect_timeout(monkeypatch):
    from koipa import config as cfg

    monkeypatch.setattr(
        cfg.settings, "database_url", "postgresql+psycopg://u:p@h:5432/db", raising=False
    )
    monkeypatch.setattr(cfg.settings, "db_connect_timeout", 5, raising=False)
    assert _sess._engine_connect_args() == {"connect_timeout": 5}


def test_non_postgres_url_no_connect_timeout(monkeypatch):
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "database_url", "sqlite:///x.db", raising=False)
    assert _sess._engine_connect_args() == {}


def test_zero_timeout_disables(monkeypatch):
    from koipa import config as cfg

    monkeypatch.setattr(
        cfg.settings, "database_url", "postgresql+psycopg://u:p@h/db", raising=False
    )
    monkeypatch.setattr(cfg.settings, "db_connect_timeout", 0, raising=False)
    assert _sess._engine_connect_args() == {}
