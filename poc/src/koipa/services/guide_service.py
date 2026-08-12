"""Guide service — FUN-002 가이드 문서 업로드·버전 관리 + RAG 인덱싱.

흐름 (W5):
- 업로드 파일 수신 (multipart)
- 포맷 자동 감지 → extract() 텍스트 추출 (HWP/PDF/DOCX/TXT 지원)
  이전: _decode_best_effort() 로 바이트 직접 디코딩 → HWP/PDF 깨짐
  변경: m2_preprocess.extractor.extract() 경유 → 실제 파서 사용
- RagIndexer.index_guide(...) → ES 적재 + alias 스왑
- 메타 기록 (in-memory, 운영은 PG 'guides' 테이블 신설 권장)
- ES 미가용 또는 임베딩 실패 시 indexed=False + warnings (best-effort)
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from koipa.modules.m2_preprocess.extractor import extract as _extract_file
from koipa.modules.m4_training.rag_indexer import RagIndexer
from koipa.schemas.guide import GuideUploadResponse, GuideVersionItem, GuideVersionList

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
    # tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진, 전역 가이드 네임스페이스).
    index_name: Optional[str] = None
    alias: Optional[str] = None
    model: Optional[str] = None


class GuideService:
    """In-memory 가이드 메타 저장소 + RAG 인덱서 결합.

    스레드 안전: 단일 워커 PoC 가정. 다중 워커 운영 시 PG로 이전.
    """

    _instance: "GuideService | None" = None

    def __init__(self, indexer: RagIndexer | None = None):
        # tenant 제거: 격리는 KL 포털 전담 — guide_id 단독으로 in-memory 키.
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
                    indexed=rec.indexed,
                    embedding_vector_count=rec.embedding_vector_count,
                    index_name=rec.index_name,
                    alias=rec.alias,
                    model=rec.model,
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
        text = _extract_text(content_bytes, filename=filename or "guide.txt")
        indexer = self._get_indexer()
        result = indexer.index_guide(
            guide_id=guide_id,
            version=version,
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
        self._persist_guide(rec, doc_type=doc_type, filename=filename)

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


def _extract_text(data: bytes, filename: str = "guide.txt") -> str:
    """파일 바이트 → 텍스트. 포맷 자동 감지(HWP/PDF/DOCX/TXT).

    임시파일 경유로 extract() 를 호출해 실제 파서를 탄다.
    추출 실패(라이브러리 없음, 스캔본 등)는 빈 문자열 반환 + 로그 — indexed=False 처리.
    평문 포맷(txt/md)은 extract() 내부에서 UTF-8/CP949 자동 처리.
    """
    suffix = Path(filename).suffix or ".txt"
    tmp_path: Optional[str] = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        result = _extract_file(tmp_path)
        if result.error:
            logger.warning("guide extract warning: %s (file=%s)", result.error, filename)
        return result.text or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("guide extract failed: %s (file=%s)", exc, filename)
        return ""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

