"""Guide service — FUN-002 가이드 문서 업로드·버전 관리 + RAG 인덱싱.

흐름 (W5):
- 업로드 파일 수신 (multipart)
- 텍스트 디코딩 → RagIndexer.index_guide(...) → ES 적재 + alias 스왑
- 메타 기록 (in-memory, 운영은 PG 'guides' 테이블 신설 권장)
- ES 미가용 또는 임베딩 실패 시 indexed=False + warnings (best-effort)
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from lloydk.modules.m4_training.rag_indexer import RagIndexer
from lloydk.schemas.guide import GuideUploadResponse, GuideVersionItem, GuideVersionList

logger = logging.getLogger(__name__)


@dataclass
class _GuideRecord:
    guide_id: str
    version: str
    effective_date: Optional[str]
    change_summary: Optional[str]
    registered_at: str
    indexed: bool
    embedding_vector_count: int
    index_name: Optional[str] = None
    alias: Optional[str] = None
    model: Optional[str] = None


class GuideService:
    """In-memory 가이드 메타 저장소 + RAG 인덱서 결합.

    스레드 안전: 단일 워커 PoC 가정. 다중 워커 운영 시 PG로 이전.
    """

    _instance: "GuideService | None" = None

    def __init__(self, indexer: RagIndexer | None = None):
        self._guides: dict[str, list[_GuideRecord]] = defaultdict(list)
        self._current_training_version: dict[str, str] = {}
        self._indexer = indexer  # None이면 upload 시점에 lazy 생성 (테스트 주입 용이)

    @classmethod
    def get_instance(cls) -> "GuideService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_singleton(cls) -> None:
        """테스트 격리용 — singleton state 초기화."""
        cls._instance = None

    def _get_indexer(self) -> RagIndexer:
        if self._indexer is None:
            self._indexer = RagIndexer()
        return self._indexer

    def upload(
        self,
        *,
        guide_id: str,
        version: str,
        effective_date: Optional[str],
        change_summary: Optional[str],
        content_bytes: bytes,
        actor_user_id: str,
        tenant_id: str = "default",
        doc_type: Optional[str] = None,
    ) -> GuideUploadResponse:
        text = _decode_best_effort(content_bytes)
        indexer = self._get_indexer()
        result = indexer.index_guide(
            guide_id=guide_id,
            version=version,
            tenant_id=tenant_id,
            text=text,
            doc_type=doc_type,
            effective_date=effective_date,
        )

        rec = _GuideRecord(
            guide_id=guide_id,
            version=version,
            effective_date=effective_date,
            change_summary=change_summary,
            registered_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            indexed=result.indexed,
            embedding_vector_count=result.vector_count,
            index_name=result.index_name,
            alias=result.alias,
            model=result.model,
        )
        self._guides[guide_id].append(rec)

        if result.warnings:
            logger.info(
                "guide upload partial: %s v%s by %s — warnings=%s",
                guide_id, version, actor_user_id, result.warnings,
            )
        else:
            logger.info(
                "guide indexed: %s v%s by %s → %s (%d vectors)",
                guide_id, version, actor_user_id, result.index_name, result.vector_count,
            )

        return GuideUploadResponse(
            guide_id=guide_id,
            version=version,
            indexed=rec.indexed,
            embedding_vector_count=rec.embedding_vector_count,
            triggers_retraining=False,  # PoC: 항상 false. 운영은 effective_date·diff로 판단.
        )

    def list_versions(self, guide_id: str) -> Optional[GuideVersionList]:
        records = self._guides.get(guide_id)
        if not records:
            return None
        return GuideVersionList(
            guide_id=guide_id,
            current_training_version=self._current_training_version.get(guide_id),
            versions=[
                GuideVersionItem(
                    version=r.version,
                    effective_date=r.effective_date,
                    change_summary=r.change_summary,
                    registered_at=r.registered_at,
                )
                for r in records
            ],
        )


def _decode_best_effort(data: bytes) -> str:
    """업로드 바이트를 텍스트로 디코딩. UTF-8 우선 → CP949 폴백 → 무손실 latin-1."""
    for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace")

