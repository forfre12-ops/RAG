"""Guide 도메인 리포지토리 — guides 테이블 CRUD."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from koipa.db.models import Guide


class GuideRepo:
    def __init__(self, db: Session):
        self.db = db

    def upsert(
        self,
        *,
        guide_id: str,
        version: str,
        effective_date: Optional[str] = None,
        change_summary: Optional[str] = None,
        doc_type: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> Guide:
        """guide_id+version 조합이 이미 있으면 UPDATE, 없으면 INSERT.

        tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진, 전역 네임스페이스).
        """
        existing = self.db.execute(
            select(Guide).where(
                Guide.guide_id == guide_id,
                Guide.version == version,
            )
        ).scalar_one_or_none()

        if existing:
            existing.effective_date = effective_date
            existing.change_summary = change_summary
            existing.doc_type = doc_type
            existing.filename = filename
            self.db.flush()
            return existing

        row = Guide(
            guide_id=guide_id,
            version=version,
            effective_date=effective_date,
            change_summary=change_summary,
            doc_type=doc_type,
            filename=filename,
        )
        self.db.add(row)
        self.db.flush()
        return row

    # 하위호환 alias
    create = upsert

    def list_versions(self, guide_id: str) -> list[Guide]:
        stmt = (
            select(Guide)
            .where(Guide.guide_id == guide_id)
            .order_by(Guide.registered_at)
        )
        return list(self.db.execute(stmt).scalars())

    def latest_training_version(self, guide_id: str) -> Optional[str]:
        """가장 최근에 등록된 버전 — RAG 인덱싱 폐기(2026-09)로 '학습에 쓰인' 구분은 없다."""
        stmt = (
            select(Guide.version)
            .where(Guide.guide_id == guide_id)
            .order_by(Guide.registered_at.desc())
            .limit(1)
        )
        result = self.db.execute(stmt).scalar()
        return result
