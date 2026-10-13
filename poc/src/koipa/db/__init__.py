"""DB 영속화 계층 — SQLAlchemy 2.0 sync session + 17테이블 ORM 매핑.

벡터스토어는 ES 단일 (doc/13 §4.1). PG는 트랜잭션·라벨·이력 메타 전용.

import side-effects:
- ``koipa.db.listeners`` 모듈 임포트는 Document→Chunk cascade ``before_delete``
  이벤트 리스너를 등록한다. 파티션 테이블 chunks 가 FK를 받을 수 없는
  PG 16 제약에 대한 application-level 안전망.
"""

from koipa.db.session import (
    Base,
    engine,
    SessionLocal,
    database_reachable_fast,
    get_session,
    session_scope,
)
from koipa.db.listeners import register_listeners

# 임포트 시점에 Document→Chunk cascade 이벤트 등록 (멱등 — 데코레이터 1회).
register_listeners()

__all__ = [
    "Base",
    "engine",
    "SessionLocal",
    "database_reachable_fast",
    "get_session",
    "session_scope",
    "register_listeners",
]
