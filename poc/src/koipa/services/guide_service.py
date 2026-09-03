"""Guide service — 가이드 문서 버전 이력 관리.

⚠ 모듈 docstring 정정(2026-09): 이 파일이 스스로 "FUN-002"라 부르던 요건은 정본
요구사항 추적표(FUN-003/004/005/022/023/024)에 존재하지 않는 번호였다 — 가이드
업로드·버전관리는 계약 요건이 아니라 부가 기능이다. RAG 인덱싱(RagIndexer 경유 ES
적재 + alias 스왑)은 같은 판에서 폐기했다. 유사문서 검색이 등급 판정에 관여하지 않는
선택 보완 기능이었고, 이 서비스가 그 유일한 소비자였다. 남는 것은 메타 기록뿐이다:

- 업로드 요청 수신 (guide_id·version·effective_date·change_summary·doc_type·filename)
- guides 테이블에 버전 이력 저장 (PG 가용 시 영속, 미가용 시 in-memory 폴백)
- 파일 본문(content_bytes)은 추출·색인·저장 어디에도 쓰이지 않는다 — 받되 버린다.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from koipa.schemas.guide import GuideUploadResponse, GuideVersionItem, GuideVersionList

logger = logging.getLogger(__name__)


@dataclass
class _GuideRecord:
    # tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진, 전역 가이드 네임스페이스).
    guide_id: str
    version: str
    effective_date: Optional[str]
    change_summary: Optional[str]
    registered_at: str


class GuideService:
    """In-memory 가이드 메타 저장소.

    스레드 안전: 단일 워커 PoC 가정. 다중 워커 운영 시 PG로 이전.
    """

    _instance: "GuideService | None" = None

    def __init__(self):
        # tenant 제거: 격리는 KL 포털 전담 — guide_id 단독으로 in-memory 키.
        self._guides: dict[str, list[_GuideRecord]] = defaultdict(list)
        self._current_training_version: dict[str, str] = {}

    @classmethod
    def get_instance(cls) -> "GuideService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_singleton(cls) -> None:
        """테스트 격리용 — singleton state 초기화."""
        cls._instance = None

    def _persist_guide(self, rec: _GuideRecord, doc_type: Optional[str], filename: Optional[str]) -> None:
        """guides 테이블에 버전 이력 저장 (best-effort).

        tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진, 전역 가이드 네임스페이스).
        """
        try:
            from koipa.db import session_scope  # noqa: PLC0415
            from koipa.repositories.guide_repo import GuideRepo  # noqa: PLC0415
            with session_scope() as db:
                GuideRepo(db).create(
                    guide_id=rec.guide_id,
                    version=rec.version,
                    effective_date=rec.effective_date,
                    change_summary=rec.change_summary,
                    doc_type=doc_type,
                    filename=filename,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("guide persist to DB skipped: %s", exc)

    def list_versions_from_db(
        self, guide_id: str
    ) -> Optional["GuideVersionList"]:
        """DB에서 버전 이력 조회. DB 미가용 시 None.

        tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진, 전역 가이드 네임스페이스).
        """
        try:
            from koipa.db import session_scope  # noqa: PLC0415
            from koipa.repositories.guide_repo import GuideRepo  # noqa: PLC0415
            with session_scope() as db:
                rows = GuideRepo(db).list_versions(guide_id)
                if not rows:
                    return None
                return GuideVersionList(
                    guide_id=guide_id,
                    current_training_version=GuideRepo(db).latest_training_version(
                        guide_id
                    ),
                    versions=[
                        GuideVersionItem(
                            version=r.version,
                            effective_date=r.effective_date,
                            change_summary=r.change_summary,
                            registered_at=r.registered_at.isoformat() if r.registered_at else "",
                        )
                        for r in rows
                    ],
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("guide list_versions_from_db failed: %s", exc)
            return None

    def upload(
        self,
        *,
        guide_id: str,
        version: str,
        effective_date: Optional[str],
        change_summary: Optional[str],
        content_bytes: bytes,
        actor_user_id: str,
        doc_type: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> GuideUploadResponse:
        logger.debug(
            "guide upload enter: guide_id=%s version=%s bytes=%d actor=%s",
            guide_id, version, len(content_bytes or b""), actor_user_id,
        )
        rec = _GuideRecord(
            guide_id=guide_id,
            version=version,
            effective_date=effective_date,
            change_summary=change_summary,
            registered_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        self._guides[guide_id].append(rec)
        self._persist_guide(rec, doc_type=doc_type, filename=filename)

        logger.info(
            "guide registered: %s v%s by %s (filename=%s)",
            guide_id, version, actor_user_id, filename,
        )

        return GuideUploadResponse(
            guide_id=guide_id,
            version=version,
            triggers_retraining=False,  # PoC: 항상 false. 운영은 effective_date·diff로 판단.
        )

    def list_versions(
        self, guide_id: str
    ) -> Optional[GuideVersionList]:
        logger.debug("guide list_versions enter: guide_id=%s", guide_id)
        # DB 우선 — 가용 시 영속 데이터 반환 (재시작 후에도 유지)
        db_result = self.list_versions_from_db(guide_id)
        if db_result is not None:
            return db_result
        # DB 미가용 폴백: in-memory
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

