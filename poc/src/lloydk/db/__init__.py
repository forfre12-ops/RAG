"""DB 영속화 계층 — SQLAlchemy 2.0 sync session + 17테이블 ORM 매핑.

벡터스토어는 ES 단일 (doc/13 §4.1). PG는 트랜잭션·라벨·이력 메타 전용.
"""

from lloydk.db.session import Base, engine, SessionLocal, get_session, session_scope

__all__ = ["Base", "engine", "SessionLocal", "get_session", "session_scope"]
