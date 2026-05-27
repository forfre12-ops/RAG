"""Guide service — FUN-002 가이드 문서 업로드·버전 관리.

PoC 스텁: 실제 RAG 인덱서는 W5에서 채움. 본 서비스는
- 업로드 파일 수신 (multipart)
- 메타 기록 (in-memory store, 운영은 PG 'guides' 테이블 신설)
- ES 인덱싱은 W5의 RagIndexer에 위임 (현재는 indexed=False로 응답)
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

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


class GuideService:
    """In-memory 가이드 메타 저장소. 운영은 PG 'guides' 테이블로 교체 예정.

    스레드 안전: 단일 워커 PoC 가정. 다중 워커 운영 시 PG로 이전.
    """

    _instance: "GuideService | None" = None

    def __init__(self):
        # guide_id → list of records (최신이 끝)
        self._guides: dict[str, list[_GuideRecord]] = defaultdict(list)
        self._current_training_version: dict[str, str] = {}

    @classmethod
    def get_instance(cls) -> "GuideService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def upload(
        self,
        *,
        guide_id: str,
        version: str,
        effective_date: Optional[str],
        change_summary: Optional[str],
        content_bytes: bytes,
        actor_user_id: str,
    ) -> GuideUploadResponse:
        # W5 RAG 인덱서 도착 전까지 indexed=False, 청크 수 = 추정
        chunk_count = max(1, len(content_bytes) // 1024)  # 1KB 청크 추정
        rec = _GuideRecord(
            guide_id=guide_id,
            version=version,
            effective_date=effective_date,
            change_summary=change_summary,
            registered_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            indexed=False,  # W5에서 True로 갱신
            embedding_vector_count=chunk_count,
        )
        self._guides[guide_id].append(rec)
        logger.info("guide uploaded: %s v%s by %s", guide_id, version, actor_user_id)
        return GuideUploadResponse(
            guide_id=guide_id,
            version=version,
            indexed=rec.indexed,
            embedding_vector_count=rec.embedding_vector_count,
            triggers_retraining=False,  # PoC: 항상 false. 운영은 effective_date 기준 판단.
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
