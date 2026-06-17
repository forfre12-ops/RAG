"""SQLAlchemy 2.0 동기 엔진·세션 팩토리.

- `engine`: settings.database_url로 생성, pool_pre_ping=True
- `SessionLocal`: 세션 팩토리 (autocommit=False, autoflush=False, expire_on_commit=False)
- `get_session()`: FastAPI Depends용 제너레이터
- `session_scope()`: with 블록용 컨텍스트 매니저
- `Base`: ORM 베이스 (DeclarativeBase)
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from lloydk.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """모든 ORM 모델의 베이스 클래스."""


def _engine_connect_args() -> dict:
    """연결이 무한 대기하지 않도록 connect_timeout 주입 (운영 부팅 행 방지).

    PG가 일시 불가일 때 GradeRegistry·FactorRegistry·세션이 연결 대기로 워커 부팅·
    추론기 생성을 무한 블록하는 것을 막는다 — 타임아웃 후 빠르게 예외 → 각 호출부의
    try/except가 폴백 처리한다. connect_timeout(초)은 PostgreSQL(psycopg/libpq) 전용이라
    해당 드라이버 URL에만 주입한다(SQLite 등에는 미적용).
    """
    url = settings.database_url or ""
    timeout = int(getattr(settings, "db_connect_timeout", 5) or 0)
    if url.startswith("postgresql") and timeout > 0:
        return {"connect_timeout": timeout}
    return {}


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    connect_args=_engine_connect_args(),
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
    future=True,
)


def get_session() -> Iterator[Session]:
    """FastAPI Depends용 세션 제공자. 요청당 1세션, 종료 시 close."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """스크립트·테스트·워커용 트랜잭션 컨텍스트. commit/rollback 자동 처리."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        # O3: rollback 사유 로깅 — IntegrityError·OperationalError 등 진단 가능
        logger.error("session_scope rollback: %s", e, exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
