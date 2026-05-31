"""Guide 도메인 리포지토리 — guides 테이블 CRUD."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from lloydk.db.models import Guide


class GuideRepo:
    def __init__(self, db: Session):
        self.db = db

    def upsert(
        self,
        *,
        guide_id: str,
        version: str,
        tenant_id: str = "default",
        effective_date: Optional[str] = None,
        change_summary: Optional[str] = None,
        doc_type: Optional[str] = None,
        filename: Optional[str] = None,
        indexed: bool = False,
        embedding_vector_count: int = 0,
        index_name: Optional[str] = None,
        alias: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Guide:
        """guide_id+version+tenant 조합이 이미 있으면 UPDATE, 없으면 INSERT."""
        existing = self.db.execute(
            select(Guide).where(
                Guide.guide_id == guide_id,
                Guide.version == version,
                Guide.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()

        if existing:
            existing.effective_date = effective_date
            existing.change_summary = change_summary
            existing.doc_type = doc_type
            existing.filename = filename
            existing.indexed = indexed
            existing.embedding_vector_count = embedding_vector_count
            existing.index_name = index_name
            existing.alias = alias
            existing.model = model
            self.db.flush()
            return existing

        row = Guide(
            guide_id=guide_id,
            tenant_id=tenant_id,
            version=version,
            effective_date=effective_date,
            change_summary=change_summary,
            doc_type=doc_type,
            filename=filename,
            indexed=indexed,
            embedding_vector_count=embedding_vector_count,
            index_name=index_name,
            alias=alias,
            model=model,
        )
        self.db.add(row)
        self.db.flush()
        return row

    # 하위호환 alias
    create = upsert

    def list_versions(self, guide_id: str, tenant_id: str = "default") -> list[Guide]:
        stmt = (
            select(Guide)
            .where(Guide.guide_id == guide_id, Guide.tenant_id == tenant_id)
            .order_by(Guide.registered_at)
        )
        return list(self.db.execute(stmt).scalars())

    def latest_training_version(self, guide_id: str, tenant_id: str = "default") -> Optional[str]:
        """현재 학습에 쓰인 버전 — 마지막 indexed=True 버전."""
        stmt = (
            select(Guide.version)
            .where(Guide.guide_id == guide_id, Guide.tenant_id == tenant_id, Guide.indexed.is_(True))
            .order_by(Guide.registered_at.desc())
            .limit(1)
        )
        result = self.db.execute(stmt).scalar()
        return result
