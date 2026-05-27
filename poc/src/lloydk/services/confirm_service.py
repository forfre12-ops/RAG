"""Confirm/Relabel service — corrections + classifications.status 업데이트.

설계:
- /confirm: classification_id 또는 doc_id 최신 분류를 'confirmed'로 마크.
  보정 없으면 corrections에 direction='confirm'으로 1건 기록.
- /relabel: 새 corrections 1건 (direction은 등급 order로 자동 산출).
  classification.status='corrected'로 갱신.
- 모두 best-effort — DB 미가용 / classification 미존재 시 silent 성공 + warning.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.repositories import ClassifyRepo
from lloydk.schemas.confirm import (
    ConfirmRequest,
    ConfirmResponse,
    RelabelRequest,
    RelabelResponse,
)

logger = logging.getLogger(__name__)

RETRAIN_THRESHOLD_DEFAULT = 10  # underclass 누적 >= 이면 URGENT_RETRAIN (init.sql v_active_learning_status)


@dataclass
class ConfirmResult:
    confirmation_id: uuid.UUID
    confirmed_at: str
    persisted: bool
    warnings: list[str]


@dataclass
class RelabelResult:
    relabel_id: uuid.UUID
    queue_size: int
    retrain_threshold: int
    persisted: bool
    warnings: list[str]


class ConfirmService:
    """관리자 분류 확정 — staging → confirmed."""

    def confirm(self, req: ConfirmRequest) -> ConfirmResult:
        logger.debug(
            "confirm enter: inference_id=%s doc_id=%s confirmed_label=%s actor=%s",
            req.inference_id, req.doc_id, req.confirmed_label, req.actor.user_id,
        )
        warns: list[str] = []
        new_id = uuid.uuid4()
        confirmed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            with session_scope() as db:
                repo = ClassifyRepo(db)
                cls = self._find_classification(repo, req)
                if cls is None:
                    logger.info(
                        "confirm: classification not found (audit only) — inference_id=%s doc_id=%s",
                        req.inference_id, req.doc_id,
                    )
                    warns.append("classification not found in DB — confirmation recorded in audit only")
                    return ConfirmResult(new_id, confirmed_at, persisted=False, warnings=warns)

                # status 갱신
                cls.status = "confirmed"

                # confirmed_label이 predicted와 다르면 사실상 relabel — 보정으로 기록
                target_level_id = repo.level_id_by_code(req.confirmed_label)
                if target_level_id is None:
                    warns.append(f"unknown confirmed_label {req.confirmed_label!r}")
                    return ConfirmResult(new_id, confirmed_at, persisted=True, warnings=warns)

                if target_level_id != cls.predicted_level_id:
                    # 자동으로 corrections 기록 (direction은 order로 산출)
                    repo.add_correction(
                        classification_id=cls.classification_id,
                        original_level_id=cls.predicted_level_id,
                        corrected_level_id=target_level_id,
                        corrected_by=req.actor.user_id,
                        reason=req.note or "confirmed with different label",
                    )
                else:
                    # 같은 등급 확정 — confirm 방향
                    repo.add_correction(
                        classification_id=cls.classification_id,
                        original_level_id=cls.predicted_level_id,
                        corrected_level_id=cls.predicted_level_id,
                        corrected_by=req.actor.user_id,
                        reason=req.note,
                    )
                logger.info(
                    "confirm done: classification_id=%s confirmation_id=%s label=%s",
                    cls.classification_id, new_id, req.confirmed_label,
                )
                return ConfirmResult(new_id, confirmed_at, persisted=True, warnings=warns)
        except SQLAlchemyError as exc:
            logger.warning("confirm persistence failed: %s", exc)
            warns.append(f"persistence skipped: {type(exc).__name__}")
            return ConfirmResult(new_id, confirmed_at, persisted=False, warnings=warns)

    @staticmethod
    def _find_classification(repo: ClassifyRepo, req: ConfirmRequest):
        if req.inference_id is not None:
            return repo.get(req.inference_id)
        try:
            doc_uuid = uuid.UUID(req.doc_id)
        except (ValueError, TypeError):
            return None
        recent = repo.list_recent_for_doc(doc_uuid, limit=1)
        return recent[0] if recent else None


class RelabelService:
    """오분류 수정 — 새 corrections + classification.status='corrected'."""

    def relabel(self, req: RelabelRequest) -> RelabelResult:
        logger.debug(
            "relabel enter: inference_id=%s doc_id=%s %s→%s actor=%s",
            req.inference_id, req.doc_id, req.original_label, req.corrected_label, req.actor.user_id,
        )
        warns: list[str] = []
        new_id = uuid.uuid4()
        try:
            with session_scope() as db:
                repo = ClassifyRepo(db)
                cls = self._find_classification(repo, req)
                if cls is None:
                    logger.info(
                        "relabel: classification not found (audit only) — inference_id=%s doc_id=%s",
                        req.inference_id, req.doc_id,
                    )
                    warns.append("classification not found in DB — relabel queued in audit only")
                    return RelabelResult(new_id, 0, RETRAIN_THRESHOLD_DEFAULT, persisted=False, warnings=warns)

                orig_id = repo.level_id_by_code(req.original_label)
                corr_id = repo.level_id_by_code(req.corrected_label)
                if orig_id is None or corr_id is None:
                    warns.append("unknown grade code")
                    return RelabelResult(new_id, 0, RETRAIN_THRESHOLD_DEFAULT, persisted=False, warnings=warns)

                repo.add_correction(
                    classification_id=cls.classification_id,
                    original_level_id=orig_id,
                    corrected_level_id=corr_id,
                    corrected_by=req.actor.user_id,
                    reason=req.reason,
                )
                cls.status = "corrected"

                queue_size = len(repo.unconsumed_corrections())
                logger.info(
                    "relabel done: classification_id=%s relabel_id=%s queue_size=%d",
                    cls.classification_id, new_id, queue_size,
                )
                return RelabelResult(
                    relabel_id=new_id,
                    queue_size=queue_size,
                    retrain_threshold=RETRAIN_THRESHOLD_DEFAULT,
                    persisted=True,
                    warnings=warns,
                )
        except SQLAlchemyError as exc:
            logger.warning("relabel persistence failed: %s", exc)
            warns.append(f"persistence skipped: {type(exc).__name__}")
            return RelabelResult(new_id, 0, RETRAIN_THRESHOLD_DEFAULT, persisted=False, warnings=warns)

    @staticmethod
    def _find_classification(repo: ClassifyRepo, req: RelabelRequest):
        if req.inference_id is not None:
            return repo.get(req.inference_id)
        try:
            doc_uuid = uuid.UUID(req.doc_id)
        except (ValueError, TypeError):
            return None
        recent = repo.list_recent_for_doc(doc_uuid, limit=1)
        return recent[0] if recent else None


def to_confirm_response(result: ConfirmResult) -> ConfirmResponse:
    return ConfirmResponse(confirmation_id=result.confirmation_id, confirmed_at=result.confirmed_at)


def to_relabel_response(result: RelabelResult) -> RelabelResponse:
    return RelabelResponse(
        relabel_id=result.relabel_id,
        queue_size=result.queue_size,
        retrain_threshold=result.retrain_threshold,
    )
