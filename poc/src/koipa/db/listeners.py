"""SQLAlchemy event listeners — application-level cascade 안전망.

목적:
- ``chunks`` 테이블은 RANGE PARTITION (created_at) 부모라서 PG 16에서도
  ``documents.doc_id`` 를 가리키는 FK를 받을 수 없음. 결과적으로
  ``DELETE FROM tb_documents WHERE doc_id=?`` 만 실행하면 ``chunks`` 자식
  파티션의 행이 고아로 남고, 감사 추적성·스토리지 회수가 무너진다.
- 정공법은 ``DocumentService.delete_document`` 트랜잭션 내에서
  ``ChunkRepo.delete_by_doc_id`` → ``DocumentRepo.delete`` → audit_log 순으로
  명시 cascade. 본 모듈은 **그 호출 누락에 대비한 2차 안전망** 으로,
  ORM 레벨 ``Session.delete(document)`` 흐름에서도 자동 cascade 한다.

설계 노트:
- ``after_delete`` 가 아니라 ``before_delete`` 를 사용한다 — ``after_delete``
  단계는 row가 이미 사라진 시점이므로 DELETE 쿼리 추가 발행 안전성이
  떨어진다. ``before_delete`` 에서 같은 세션에 chunk DELETE 를 emit 하면
  하나의 트랜잭션에 묶여 atomic.
- 본 listener는 ORM의 ``Session.delete(obj)`` → ``flush`` 경로에서만 동작.
  ``delete(Document).where(...)`` 같은 bulk core DELETE 는 거치지 않으므로
  Service 레이어의 명시적 ChunkRepo 호출이 진실의 출처(primary path)이며
  listener는 보조 안전망이다.
- 멱등 — 같은 doc_id로 두 번 호출되어도 두 번째는 0행 삭제 (NO-OP).
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, event
from sqlalchemy.orm import Mapper, Session

from koipa.db.models import Chunk, Document

logger = logging.getLogger(__name__)


@event.listens_for(Document, "before_delete")
def _cascade_chunks_on_document_delete(
    mapper: Mapper,  # noqa: ARG001 — SQLAlchemy 시그니처
    connection,  # noqa: ANN001 — Connection 또는 Session.connection
    target: Document,
) -> None:
    """Document ORM 삭제 직전 같은 트랜잭션에서 chunks 일괄 정리.

    DocumentService.delete_document 가 이미 ChunkRepo로 청크를 정리했어도
    멱등이라 안전 — 0행 추가 삭제.
    """
    # tenant 제거: doc_id 단독으로 chunk 정리 충분 (격리는 KL 포털 전담).
    doc_id = target.doc_id
    if doc_id is None:
        # 비정상 상태 — 무결성을 위해 단순히 스킵 (예: 트랜잭션 외부에서
        # detached 인스턴스로 호출된 케이스).
        logger.debug("cascade skip: doc_id=%s (unbound state)", doc_id)
        return

    stmt = delete(Chunk).where(Chunk.doc_id == doc_id)
    result = connection.execute(stmt)
    deleted = int(result.rowcount or 0)
    if deleted > 0:
        logger.info("cascade chunks: doc_id=%s deleted=%d", doc_id, deleted)


def register_listeners() -> None:
    """명시 등록 hook — 임포트 시점 외에 명시적 활성화가 필요한 경우 사용."""
    # 모듈 임포트만으로 데코레이터가 실행되므로 본 함수는 NO-OP.
    # 가시성과 사이드이펙트 명시화를 위한 anchor.
    _ = Session  # noqa: F841 — keep import to ensure SA loaded


__all__ = ["register_listeners"]
