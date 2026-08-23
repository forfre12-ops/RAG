"""검증라벨 승급 서비스 — 교정(corrections) → 검증 DocumentLabel.

[Option 2 / 비모수 안전 레버] 고객사(enable_training=False)에서 교정은 재학습으로 흐르지
못해 사실상 '쓰기 전용'이다. 이 서비스는 admission 게이트(사람검수 + 분류 finalized)를 통과한
교정을 **사람 승인**으로 검증 DocumentLabel로 승급한다. 승급 즉시 서빙
(ClassifyService._get_verified_label)이 모델을 스킵하고 그 등급을 반환한다.

안전 속성:
  · exact-match(동일 doc_id / 동일 file_hash)만 적용 → 다른 문서로 전파 없음(폭발반경=1문서).
  · 가중치·재학습·deploy gate와 무관 → 죽음의 나선 경로와 무관.
  · admission 게이트가 머신/미확정 교정을 거부. dual-review도 admission을 통해 자동 존중된다:
    고등급 1인 동의 교정은 분류 status가 'needs_second_review'라 admissible 아님 → 2차 검수로
    status가 confirmed/corrected가 되기 전엔 승급 불가.

best-effort — DB 미가용/문서 부재/비-admissible는 예외 대신 명시 status로 응답한다.
"""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from koipa.db import session_scope
from koipa.db.models import Classification, ClassificationLevel, Correction
from koipa.modules.m6_evaluation.corrections_rebuild import _correction_admissible
from koipa.repositories import ClassifyRepo
from koipa.schemas.common import Grade
from koipa.schemas.promotion import (
    PendingPromotionItem,
    PromoteRequest,
    PromoteResponse,
)

logger = logging.getLogger(__name__)


def _emit_promote_audit_skip() -> None:
    """[P1] 검증라벨 승급 감사 기록 실패를 가시화 — 무음 fail-open → 메트릭.

    승급은 서빙 등급을 override하는 보안 민감 행위인데, 감사 기록이 best-effort로 무음 실패하면
    컴플라이언스 감사 갭이 안 보였다. 기존 CLASSIFY_VERIFIED_LABEL_AUDIT_SKIP_TOTAL(#4
    VerifiedLabelAuditSkip 알람이 감시)를 증가시켜 skip을 알람으로 승격한다. best-effort(프로메테우스
    미가용 무시).
    """
    try:
        from koipa.api.prom_metrics import (  # noqa: PLC0415
            CLASSIFY_VERIFIED_LABEL_AUDIT_SKIP_TOTAL,
        )

        CLASSIFY_VERIFIED_LABEL_AUDIT_SKIP_TOTAL.inc()
    except Exception:  # noqa: BLE001
        pass


def _emit_promote_success(grade: str) -> None:
    """[P1] 검증라벨 승급 성공을 가시화 — 서빙 override 발동률의 성공측(실패측 AUDIT_SKIP과 대칭).

    promote()가 사람 승인으로 서빙 등급을 override할 때 등급별로 증가. 기존엔 실패측만 카운터가
    있어 발동률이 실패면에서만 그래프화됐다(가시성 비대칭). best-effort(프로메테우스 미가용 무시).
    """
    try:
        from koipa.api.prom_metrics import VERIFIED_LABEL_PROMOTED_TOTAL  # noqa: PLC0415

        VERIFIED_LABEL_PROMOTED_TOTAL.labels(grade=grade or "unknown").inc()
    except Exception:  # noqa: BLE001
        pass


# get_verified_document_label 최우선 출처(=서빙 override 1순위). 승급은 사람 검수 결정이므로
# 항상 'human_review'로 기록 — 기존 llm_judge_* 검증라벨이 있어도 사람 결정이 우선한다.
_LABEL_SOURCE = "human_review"


@dataclass
class PromoteResult:
    doc_id: str
    status: str  # promoted | already_promoted | not_promotable | mismatch
    promoted_label: "Grade | None"
    labeler_id: str | None
    warnings: list


def _try_uuid(value: object) -> "uuid.UUID | None":
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _grade_ok(code: str | None) -> bool:
    """proposed code가 Grade enum으로 표현 가능한가 — 서빙 응답(label: Grade) 보호."""
    if not code:
        return False
    try:
        Grade(code)
        return True
    except ValueError:
        return False


class PromotionService:
    """교정 → 검증라벨 승급 (사람 승인 게이트 유지)."""

    def list_pending(self, *, limit: int = 100) -> list[PendingPromotionItem]:
        """승급 대기 큐 — admission 통과 교정이 있으나 같은 등급의 검증라벨이 아직 없는 문서.

        문서별 '최신 admissible 교정'을 제안 등급으로 한다(corrections_rebuild와 동일 규칙:
        corrected_at desc, correction_id desc 정렬에서 첫 admissible). 이미 같은 등급으로
        검증된 문서는 제외(멱등). DB 미가용/오류 → 빈 리스트(best-effort).
        """
        items: list[PendingPromotionItem] = []
        try:
            with session_scope() as db:
                repo = ClassifyRepo(db)
                code_by_id = {
                    lv.level_id: lv.level_code
                    for lv in db.execute(select(ClassificationLevel)).scalars()
                }
                rows = db.execute(
                    select(Correction, Classification.doc_id, Classification.status)
                    .join(
                        Classification,
                        Correction.classification_id == Classification.classification_id,
                    )
                    .order_by(
                        Correction.corrected_at.desc(),
                        Correction.correction_id.desc(),
                    )
                ).all()
                # 문서별 최신 admissible 교정 1건 선택(머신/미확정은 건너뛰되 더 옛 human이 승계).
                chosen: dict = {}  # doc_id -> (Correction, status)
                for corr, doc_id, status in rows:
                    if doc_id in chosen:
                        continue
                    if not _correction_admissible(corr.corrected_by, status):
                        continue
                    chosen[doc_id] = (corr, status)

                for doc_id, (corr, _status) in chosen.items():
                    proposed = code_by_id.get(corr.corrected_level_id)
                    if not _grade_ok(proposed):
                        continue
                    dl = repo.get_document_label(doc_id)
                    cur_code = None
                    if dl is not None and dl.is_verified:
                        cur_code = code_by_id.get(dl.level_id)
                        if cur_code == proposed:
                            continue  # 이미 같은 등급으로 검증됨 → 큐에서 제외
                    items.append(
                        PendingPromotionItem(
                            doc_id=str(doc_id),
                            proposed_label=Grade(proposed),
                            corrected_by=corr.corrected_by,
                            corrected_at=(
                                corr.corrected_at.isoformat()
                                if corr.corrected_at is not None
                                else ""
                            ),
                            reason=corr.reason,
                            current_verified_label=(
                                Grade(cur_code) if _grade_ok(cur_code) else None
                            ),
                            classification_id=str(corr.classification_id),
                        )
                    )
                    if len(items) >= limit:
                        break
        except SQLAlchemyError as exc:
            logger.debug("list_pending skipped (db): %s", exc)
        return items

    def promote(self, req: PromoteRequest) -> PromoteResult:
        """단일 문서의 최신 admissible 교정을 검증 DocumentLabel로 승급(사람 승인)."""
        doc_uuid = _try_uuid(req.doc_id)
        if doc_uuid is None:
            return PromoteResult(
                req.doc_id, "not_promotable", None, None, ["doc_id is not a valid UUID"]
            )
        warns: list[str] = []
        result: PromoteResult
        try:
            with session_scope() as db:
                repo = ClassifyRepo(db)
                if not repo.document_exists(doc_uuid):
                    return PromoteResult(
                        req.doc_id, "not_promotable", None, None, ["document not found"]
                    )
                code_by_id = {
                    lv.level_id: lv.level_code
                    for lv in db.execute(select(ClassificationLevel)).scalars()
                }
                corr, _status = self._latest_admissible(db, doc_uuid)
                if corr is None:
                    return PromoteResult(
                        req.doc_id, "not_promotable", None, None,
                        [
                            "no admissible human correction for this document "
                            "(machine / unfinalized / needs_second_review excluded)"
                        ],
                    )
                proposed = code_by_id.get(corr.corrected_level_id)
                if not _grade_ok(proposed):
                    return PromoteResult(
                        req.doc_id, "not_promotable", None, None,
                        [f"proposed grade {proposed!r} not representable as Grade"],
                    )
                # 승급 직전 등급 변경 가드 — 큐에서 본 등급과 현재 최신 교정 등급이 다르면 거부.
                if req.expected_label is not None and req.expected_label.value != proposed:
                    return PromoteResult(
                        req.doc_id, "mismatch", Grade(proposed), corr.corrected_by,
                        [
                            f"expected_label={req.expected_label.value} but latest correction "
                            f"now proposes {proposed} — refusing (re-review the queue)"
                        ],
                    )
                dl = repo.get_document_label(doc_uuid)
                if dl is not None and dl.is_verified and dl.level_id == corr.corrected_level_id:
                    return PromoteResult(
                        req.doc_id, "already_promoted", Grade(proposed), dl.labeler_id,
                        ["verified label already at this grade"],
                    )
                if dl is not None and dl.is_verified and dl.level_id != corr.corrected_level_id:
                    warns.append(
                        f"overriding existing verified label "
                        f"({code_by_id.get(dl.level_id)}→{proposed}, prev labeled_by={dl.labeled_by})"
                    )
                repo.upsert_verified_label(
                    doc_id=doc_uuid,
                    level_id=corr.corrected_level_id,
                    labeled_by=_LABEL_SOURCE,
                    labeler_id=corr.corrected_by,
                    verified_by=req.actor.user_id,
                    confidence=1.0,
                    notes=req.note,
                )
                logger.info(
                    "promote done: doc_id=%s grade=%s labeler=%s verified_by=%s",
                    doc_uuid, proposed, corr.corrected_by, req.actor.user_id,
                )
                result = PromoteResult(
                    req.doc_id, "promoted", Grade(proposed), corr.corrected_by, warns
                )
        except SQLAlchemyError as exc:
            logger.warning("promote persistence failed: %s", exc)
            return PromoteResult(
                req.doc_id, "not_promotable", None, None,
                [f"persistence skipped: {type(exc).__name__}"],
            )

        # 감사 — best-effort(분리 세션, 승급 트랜잭션을 롤백하지 않음).
        self._audit_promote(
            doc_id=req.doc_id,
            level_code=result.promoted_label.value if result.promoted_label else "",
            verified_by=req.actor.user_id,
            actor_role=req.actor.role,
        )
        # [P1 가시성] 승급 성공 발동률 노출(실패측 AUDIT_SKIP과 대칭). 신규 upsert(promoted)만 계상 —
        # already_promoted(무변경)/not_promotable(미승급)은 서빙 override 신규 발동이 아니다.
        if result.status == "promoted":
            _emit_promote_success(result.promoted_label.value if result.promoted_label else "unknown")
        return result

    @staticmethod
    def _latest_admissible(db, doc_uuid):
        """doc의 최신 admissible 교정 (corrected_at desc, correction_id desc). 없으면 (None, None)."""
        rows = db.execute(
            select(Correction, Classification.status)
            .join(
                Classification,
                Correction.classification_id == Classification.classification_id,
            )
            .where(Classification.doc_id == doc_uuid)
            .order_by(
                Correction.corrected_at.desc(),
                Correction.correction_id.desc(),
            )
        ).all()
        for corr, status in rows:
            if _correction_admissible(corr.corrected_by, status):
                return corr, status
        return None, None

    @staticmethod
    def _audit_promote(*, doc_id: str, level_code: str, verified_by: str, actor_role: str) -> None:
        """검증라벨 승급 이벤트를 audit_log에 best-effort 기록(분리 세션)."""
        try:
            from koipa.db import SessionLocal  # noqa: PLC0415
            from koipa.db.models import AuditLog  # noqa: PLC0415

            db = SessionLocal()
            try:
                db.add(
                    AuditLog(
                        request_id=uuid.uuid4(),
                        actor_id=verified_by,
                        actor_role=actor_role,
                        action="verified_label_promoted",
                        target_type="document",
                        target_id=doc_id,
                        payload_hash=None,
                        success=True,
                        occurred_at=dt.datetime.now(dt.timezone.utc),
                    )
                )
                db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning(
                    "promote audit write FAILED — 검증라벨 승급 감사 갭(NFR-SEC-01·컴플라이언스): "
                    "doc_id=%s level=%s by=%s err=%s",
                    doc_id, level_code, verified_by, exc,
                )
                _emit_promote_audit_skip()
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            # fail-open(승급 자체는 진행)이되 무음 금지 — 메트릭+WARNING으로 VerifiedLabelAuditSkip 알람 발화.
            logger.warning("_audit_promote skipped — 승급 감사 미기록(감사 갭): %s", exc)
            _emit_promote_audit_skip()


def to_promote_response(result: PromoteResult) -> PromoteResponse:
    return PromoteResponse(
        doc_id=result.doc_id,
        status=result.status,
        # 검증라벨이 실제 DB에 존재하는 종단상태만 persisted=True(무음 성공 제거).
        persisted=result.status in ("promoted", "already_promoted"),
        promoted_label=result.promoted_label,
        labeler_id=result.labeler_id,
        warnings=list(result.warnings),
    )
