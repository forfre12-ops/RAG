"""Document 도메인 — ``documents`` 부모 테이블 CRUD.

본 리포지토리는 ChunkRepo와 짝을 이뤄 cascade 삭제를 코드 레벨로 보장한다.
파티션 자식이 부모 FK를 가질 수 없는 PG 16 제약을 회피하기 위함이다.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from lloydk.db.models import Document


class DocumentRepo:
    def __init__(self, db: Session):
        self.db = db

    def get(
        self,
        doc_id: uuid.UUID | str,
        tenant_id: str | None = None,
        *,
        include_deleted: bool = False,
    ) -> Document | None:
        """doc_id로 Document 조회.

        보안: tenant_id가 주어지면 소유 tenant가 일치할 때만 반환(객체 수준 권한).
        타 tenant 문서면 None → 교차테넌트 본문/메타 유출 차단. tenant_id=None은
        하위호환(스코프 미적용) — 신규 호출은 가능한 한 tenant_id를 넘길 것.

        #38: soft-delete된(deleted_at IS NOT NULL) 행은 기본적으로 숨긴다.
        감사/복구 등 예외 경로는 include_deleted=True로 명시 opt-in.
        """
        doc = self.db.get(Document, doc_id)
        if doc is None:
            return None
        if tenant_id is not None and doc.tenant_id != tenant_id:
            return None
        if not include_deleted and doc.deleted_at is not None:
            return None
        return doc

    def list_by_tenant(
        self,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[Document]:
        """테넌트 스코프 Document 목록 — 최신 업로드 우선.

        #38: 기본적으로 soft-delete된 행(deleted_at IS NOT NULL)을 제외한다.
        include_deleted=True면 보존정책 검토/복구용으로 삭제 행도 포함.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for document list")

        stmt = select(Document).where(Document.tenant_id == tenant_id)
        if not include_deleted:
            stmt = stmt.where(Document.deleted_at.is_(None))
        stmt = stmt.order_by(Document.uploaded_at.desc()).limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars().all())

    def create(
        self,
        *,
        tenant_id: str,
        filename: str,
        source_format: str,
        file_hash: str | None = None,
        file_size_bytes: int | None = None,
        raw_text_uri: str | None = None,
        normalized_text_uri: str | None = None,
        text_preview: str | None = None,
        char_count: int | None = None,
        extraction_method: str | None = None,
        extraction_quality: float | None = None,
        ocr_used: bool = False,
        processing_status: str = "ready",
        external_ref: str | None = None,
        metadata: dict | None = None,
        created_by: str | None = None,
    ) -> Document:
        """``documents`` 행 1건 생성. 업로드 ingestion의 진실 소스.

        doc_id는 PG server_default(gen_random_uuid)가 채우므로 flush 후 확보된다.
        provenance(원본 보관 위치·해시·포맷·추출 메서드)를 1:1로 기록 — 감사·증빙 요건.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for document create")
        if not filename:
            raise ValueError("filename is required for document create")

        doc = Document(
            tenant_id=tenant_id,
            filename=filename,
            source_format=(source_format or "bin")[:10],
            file_hash=file_hash,
            file_size_bytes=file_size_bytes,
            raw_text_uri=raw_text_uri,
            normalized_text_uri=normalized_text_uri,
            text_preview=text_preview,
            char_count=char_count,
            extraction_method=extraction_method,
            extraction_quality=extraction_quality,
            ocr_used=ocr_used,
            processing_status=processing_status,
            external_ref=external_ref,
            metadata_=metadata or {},
            created_by=created_by,
        )
        self.db.add(doc)
        self.db.flush()  # doc_id 확보 — 이후 chunks가 FK처럼 참조
        return doc

    def soft_delete(self, doc_id: uuid.UUID | str, tenant_id: str) -> int:
        """단일 Document 논리 삭제(#38) — deleted_at=NOW() 세팅, tenant 검증 포함.

        비파괴: 행/청크/감사 추적을 보존하면서 조회에서만 숨긴다(보존정책).
        이미 삭제된(deleted_at IS NOT NULL) 행은 재삭제하지 않아 멱등하다.
        반환값: 새로 soft-delete된 행 수 (0 또는 1).

        물리 회수(purge)는 보존기간 만료 후 별도 운영 잡이 담당(본 메서드 범위 밖).
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for document soft delete")

        stmt = (
            update(Document)
            .where(
                Document.doc_id == doc_id,
                Document.tenant_id == tenant_id,
                Document.deleted_at.is_(None),
            )
            .values(deleted_at=dt.datetime.now(dt.timezone.utc))
        )
        result = self.db.execute(stmt)
        return int(result.rowcount or 0)

    def delete(self, doc_id: uuid.UUID | str, tenant_id: str) -> int:
        """단일 Document 삭제 — tenant 검증 포함.

        반환값: 삭제된 행 수 (0 또는 1).
        주의: Chunk cascade는 호출자가 ``ChunkRepo.delete_by_doc_id``를
        먼저 호출하거나 SQLAlchemy event listener에 위임해야 한다.
        본 메서드는 Document 단독 삭제만 책임진다.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for document delete")

        stmt = delete(Document).where(
            Document.doc_id == doc_id,
            Document.tenant_id == tenant_id,
        )
        result = self.db.execute(stmt)
        return int(result.rowcount or 0)
