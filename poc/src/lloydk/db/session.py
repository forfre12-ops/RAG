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


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
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
