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


def count_needs_second_review_by_grade() -> dict[str, int]:
    """[번들 B] 이중검토 보류(status='needs_second_review') 건수를 등급코드별로 집계.

    high_grade_dual_review ON 시 1인 동의 고등급 교정이 무음 hold 되는데, 운영자가 능동
    쿼리해야만 보였다. 이 함수가 게이지 refresh와 admin 엔드포인트의 단일 소스 — 등급별 보류
    건수를 노출해 '안 보이는 보류큐'를 가시화한다. DB 미가용/오류 → {}(best-effort, 안전).
    """
    from sqlalchemy import func as _f, select  # noqa: PLC0415

    from lloydk.db.models import (  # noqa: PLC0415
        Classification,
        ClassificationLevel,
    )

    out: dict[str, int] = {}
    try:
        with session_scope() as db:
            code_by_id = {
                lv.level_id: lv.level_code
                for lv in db.execute(select(ClassificationLevel)).scalars()
            }
            rows = db.execute(
                select(Classification.predicted_level_id, _f.count())
                .where(Classification.status == "needs_second_review")
                .group_by(Classification.predicted_level_id)
            ).all()
            for level_id, n in rows:
                out[code_by_id.get(level_id, str(level_id))] = int(n)
    except SQLAlchemyError as exc:
        logger.debug("count_needs_second_review_by_grade skipped: %s", exc)
    return out

RETRAIN_THRESHOLD_DEFAULT = 10  # underclass 누적 >= 이면 URGENT_RETRAIN (active_learning.py 단일 진실원)


@dataclass
class ConfirmResult:
    confirmation_id: uuid.UUID
    confirmed_at: str
    persisted: bool
    warnings: list[str]
    second_review_required: bool = False


@dataclass
class RelabelResult:
    relabel_id: uuid.UUID
    queue_size: int
    retrain_threshold: int
    persisted: bool
    warnings: list[str]
    second_review_required: bool = False


def _apply_dual_review_gate(
    repo, cls, corrected_level_id: int, corrected_label: str, *, base_status: str, warns: list[str]
) -> tuple[str, bool]:
    """[C-cons] 고등급 변경 2인검토 게이트.

    settings.high_grade_dual_review=False(기본)면 base_status 그대로(동작 보존).
    True이고 corrected_label이 고등급이면, 이 분류를 그 등급으로 교정한 **고유 검수자가 2인
    이상**일 때만 확정(base_status), 아니면 'needs_second_review'로 보류. correction은 이미
    기록된 뒤 호출되므로 현재 검수자가 집합에 포함된다.

    반환: (적용할 status, second_review_required).
    """
    from lloydk.config import settings  # noqa: PLC0415
    if not getattr(settings, "high_grade_dual_review", False):
        return base_status, False
    high_codes = set(getattr(settings, "high_grade_review_codes", ["TS", "S1"]))
    if corrected_label not in high_codes:
        return base_status, False
    reviewers = repo.distinct_reviewers_for_level(cls.classification_id, corrected_level_id)
    # [C-cons 신뢰도 배선] min_reliability>0이면 저신뢰 검수자 동의는 정족수에서 제외.
    # 같은 db 세션으로 신뢰도 계산(방금 추가한 교정 반영). 이력 없는 신규 검수자는 신뢰(1.0).
    min_rel = float(getattr(settings, "high_grade_review_min_reliability", 0.0))
    if min_rel > 0 and reviewers:
        from lloydk.modules.m6_evaluation.reviewer_trust import (  # noqa: PLC0415
            compute_reviewer_reliability,
        )
        rel = {r["reviewer_id"]: r["reliability"]
               for r in compute_reviewer_reliability(db=repo.db)}
        trusted = {rv for rv in reviewers if rel.get(rv, 1.0) >= min_rel}
        low = reviewers - trusted
        if low:
            warns.append(
                f"low-trust reviewer(s) {sorted(low)} excluded from quorum "
                f"(reliability < {min_rel})"
            )
        reviewers = trusted
    if len(reviewers) >= 2:
        warns.append(
            f"dual-review satisfied: {len(reviewers)} distinct reviewers agree on {corrected_label}"
        )
        return base_status, False
    warns.append(
        f"high-grade change to {corrected_label} needs a second distinct reviewer "
        f"({len(reviewers)}/2) — held as needs_second_review"
    )
    return "needs_second_review", True


def _find_classification_scoped(repo: ClassifyRepo, req):
    """confirm/relabel 공용 분류 조회.

    tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진). 과거의 cross-tenant
    fail-CLOSED 검증은 단일 KL 인증 하에선 불필요하므로 제거한다(상류 KL 포털이
    고객사 경계를 라우팅으로 보장).
    """
    # for_update=True: 조회 행을 잠가 동시 검수 확정을 직렬화(status race·중복 correction 방지).
    if req.inference_id is not None:
        return repo.get(req.inference_id, for_update=True)
    try:
        doc_uuid = uuid.UUID(req.doc_id)
    except (ValueError, TypeError):
        return None
    recent = repo.list_recent_for_doc(doc_uuid, limit=1, for_update=True)
    return recent[0] if recent else None


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

                # corrections 멱등 기록 — 동일 (분류·교정등급·검수자) 중복 방지.
                # 행 잠금(_find_classification_scoped의 for_update)과 함께 동작해
                # 중복 클릭·재시도·동시 확정에도 correction은 1건만 남는다.
                reason = req.note or (
                    "confirmed with different label"
                    if target_level_id != cls.predicted_level_id
                    else None
                )
                if repo.correction_exists(cls.classification_id, target_level_id, req.actor.user_id):
                    warns.append(
                        "idempotent: confirmation already recorded for this classification/label/actor"
                    )
                else:
                    # direction은 add_correction이 등급 order로 자동 산출(confirm/under/over).
                    repo.add_correction(
                        classification_id=cls.classification_id,
                        original_level_id=cls.predicted_level_id,
                        corrected_level_id=target_level_id,
                        corrected_by=req.actor.user_id,
                        reason=reason,
                    )
                # [C-cons] 고등급 확정도 2인검토 통과 전까지 보류(기본 off → 'confirmed' 그대로).
                status, second_required = _apply_dual_review_gate(
                    repo, cls, target_level_id, req.confirmed_label, base_status="confirmed", warns=warns
                )
                cls.status = status
                logger.info(
                    "confirm done: classification_id=%s confirmation_id=%s label=%s status=%s",
                    cls.classification_id, new_id, req.confirmed_label, status,
                )
                return ConfirmResult(
                    new_id, confirmed_at, persisted=True, warnings=warns,
                    second_review_required=second_required,
                )
        except SQLAlchemyError as exc:
            logger.warning("confirm persistence failed: %s", exc)
            warns.append(f"persistence skipped: {type(exc).__name__}")
            return ConfirmResult(new_id, confirmed_at, persisted=False, warnings=warns)

    @staticmethod
    def _find_classification(repo: ClassifyRepo, req: ConfirmRequest):
        return _find_classification_scoped(repo, req)


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

                # 멱등 기록 — 동일 (분류·교정등급·검수자) 중복 방지(행 잠금과 함께).
                if repo.correction_exists(cls.classification_id, corr_id, req.actor.user_id):
                    warns.append(
                        "idempotent: relabel already recorded for this classification/label/actor"
                    )
                else:
                    repo.add_correction(
                        classification_id=cls.classification_id,
                        original_level_id=orig_id,
                        corrected_level_id=corr_id,
                        corrected_by=req.actor.user_id,
                        reason=req.reason,
                    )
                # [C-cons] 고등급 변경은 2인검토 통과 전까지 needs_second_review로 보류(기본 off).
                status, second_required = _apply_dual_review_gate(
                    repo, cls, corr_id, req.corrected_label, base_status="corrected", warns=warns
                )
                cls.status = status

                queue_size = len(repo.unconsumed_corrections())
                logger.info(
                    "relabel done: classification_id=%s relabel_id=%s queue_size=%d status=%s",
                    cls.classification_id, new_id, queue_size, status,
                )
                return RelabelResult(
                    relabel_id=new_id,
                    queue_size=queue_size,
                    retrain_threshold=RETRAIN_THRESHOLD_DEFAULT,
                    persisted=True,
                    warnings=warns,
                    second_review_required=second_required,
                )
        except SQLAlchemyError as exc:
            logger.warning("relabel persistence failed: %s", exc)
            warns.append(f"persistence skipped: {type(exc).__name__}")
            return RelabelResult(new_id, 0, RETRAIN_THRESHOLD_DEFAULT, persisted=False, warnings=warns)

    @staticmethod
    def _find_classification(repo: ClassifyRepo, req: RelabelRequest):
        return _find_classification_scoped(repo, req)


def to_confirm_response(result: ConfirmResult) -> ConfirmResponse:
    return ConfirmResponse(
        confirmation_id=result.confirmation_id,
        confirmed_at=result.confirmed_at,
        persisted=result.persisted,
        warnings=result.warnings,
        second_review_required=result.second_review_required,
    )


def to_relabel_response(result: RelabelResult) -> RelabelResponse:
    return RelabelResponse(
        relabel_id=result.relabel_id,
        queue_size=result.queue_size,
        retrain_threshold=result.retrain_threshold,
        persisted=result.persisted,
        warnings=result.warnings,
        second_review_required=result.second_review_required,
    )
