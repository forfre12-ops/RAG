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

from koipa.db import session_scope
from koipa.repositories import ClassifyRepo
from koipa.schemas.confirm import (
    ConfirmRequest,
    ConfirmResponse,
    RelabelRequest,
    RelabelResponse,
)

logger = logging.getLogger(__name__)


def _record_live_fnr(predicted_level_id, final_level_id) -> None:
    """정답 확정 시점(confirm/relabel)에 라이브 FNR 카운터를 동반 증가.

    koipa_classify_total(분모)은 정답이 확정된 분류마다 1, koipa_classify_correct_total
    (분자)은 확정 등급이 모델 예측과 일치할 때만 1 증가한다. FnrSpikeOverall =
    100*(1 - correct/total). 서빙 시점엔 정답이 없어 여기(사람 검수 확정)에서만 채운다
    — prom_metrics.py의 계약(둘을 동반 증가). best-effort — 실패해도 검수 흐름 불변.
    """
    try:
        from koipa.api.prom_metrics import (  # noqa: PLC0415
            CLASSIFY_CORRECT_TOTAL,
            CLASSIFY_TOTAL,
        )
        CLASSIFY_TOTAL.inc()
        if predicted_level_id is not None and final_level_id == predicted_level_id:
            CLASSIFY_CORRECT_TOTAL.inc()
    except Exception:  # noqa: BLE001
        pass


def count_needs_second_review_by_grade() -> dict[str, int]:
    """[번들 B] 이중검토 보류(status='needs_second_review') 건수를 등급코드별로 집계.

    high_grade_dual_review ON 시 1인 동의 고등급 교정이 무음 hold 되는데, 운영자가 능동
    쿼리해야만 보였다. 이 함수가 게이지 refresh와 admin 엔드포인트의 단일 소스 — 등급별 보류
    건수를 노출해 '안 보이는 보류큐'를 가시화한다. DB 미가용/오류 → {}(best-effort, 안전).
    """
    from sqlalchemy import func as _f, select  # noqa: PLC0415

    from koipa.db.models import (  # noqa: PLC0415
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


# 검수 대기 상태 — 자동확정되지 않아 사람 판정을 기다리는 분류(승인 대기).
_REVIEW_STATUSES: tuple[str, ...] = ("needs_review", "needs_second_review")


def resolve_review_statuses(status: str | None) -> tuple[str, ...]:
    """status 쿼리값 → 조회할 classification.status 집합.

    pending/all/빈값(기본) = needs_review + needs_second_review. 허용 집합 내 특정값이면
    그것만. 그 외(임의 문자열)는 기본으로 폴백 — 임의 status 주입으로 confirmed 등을 노출하는
    경로를 차단(검수 큐는 '대기'만 보인다).
    """
    s = (status or "").strip().lower()
    if s in ("", "pending", "all"):
        return _REVIEW_STATUSES
    if s in _REVIEW_STATUSES:
        return (s,)
    return _REVIEW_STATUSES


def list_review_queue(
    limit: int = 50,
    offset: int = 0,
    statuses: tuple[str, ...] = _REVIEW_STATUSES,
) -> tuple[list, int, list[str]]:
    """검수 대기(needs_review/needs_second_review) 분류를 DB에서 조회 — 승인 대기 목록(FUN-024).

    admin 콘솔의 세션-only 큐가 못 보던 'DB에 쌓인 needs_review'를 서버측에서 반환한다. FIFO
    (오래된 것 먼저)로 정렬해 백로그가 고이지 않게 한다. 문서 soft-delete(deleted_at) 제외.
    DB 미가용/오류 → ([], 0, [warning]) best-effort — 빈 큐(정상 0건)와 조회실패를 warning 으로 구분.

    Returns (items, total, warnings). items = ReviewQueueItem 리스트, total = 페이지네이션 전 전체.
    """
    from sqlalchemy import func as _f, select  # noqa: PLC0415

    from koipa.db.models import (  # noqa: PLC0415
        Classification,
        ClassificationLevel,
        Document,
    )
    from koipa.schemas.confirm import ReviewQueueItem  # noqa: PLC0415

    items: list[ReviewQueueItem] = []
    try:
        with session_scope() as db:
            code_by_id = {
                lv.level_id: lv.level_code
                for lv in db.execute(select(ClassificationLevel)).scalars()
            }
            cond = (Classification.status.in_(statuses), Document.deleted_at.is_(None))
            total = int(
                db.execute(
                    select(_f.count())
                    .select_from(Classification)
                    .join(Document, Classification.doc_id == Document.doc_id)
                    .where(*cond)
                ).scalar_one()
            )
            rows = db.execute(
                select(Classification, Document.filename, Document.text_preview)
                .join(Document, Classification.doc_id == Document.doc_id)
                .where(*cond)
                .order_by(Classification.classified_at.asc())  # FIFO
                .limit(limit)
                .offset(offset)
            ).all()
            for cls, filename, preview in rows:
                assessment = cls.automation_assessment or {}
                items.append(
                    ReviewQueueItem(
                        classification_id=str(cls.classification_id),
                        doc_id=str(cls.doc_id),
                        filename=filename,
                        grade=code_by_id.get(cls.predicted_level_id, str(cls.predicted_level_id)),
                        confidence=float(cls.confidence),
                        model_version=cls.model_version,
                        status=cls.status,
                        classified_at=cls.classified_at.isoformat() if cls.classified_at else None,
                        text_preview=((preview or "")[:300] or None),
                        review_reason=assessment.get("causal_review_reason"),
                        score_margin=assessment.get("score_margin"),
                    )
                )
            return items, total, []
    except SQLAlchemyError as exc:
        logger.debug("list_review_queue skipped (db): %s", exc)
        return [], 0, [f"db unavailable: {type(exc).__name__}"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("list_review_queue error: %s", exc)
        return [], 0, [f"error: {type(exc).__name__}"]


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
    repo, cls, corrected_level_id: int, corrected_label: str, *,
    base_status: str, warns: list[str], prior_status: str | None = None,
) -> tuple[str, bool]:
    """[C-cons] 고등급 변경 2인검토 게이트.

    settings.high_grade_dual_review=False(기본)면 base_status 그대로(동작 보존).
    True면 아래 두 경우에 **고유 검수자 2인 이상** 동의를 요구(아니면 'needs_second_review' 보류):
      (1) corrected_label 이 고등급(TS/S1)인 상향, 그리고
      (2) 대상 분류가 이미 needs_second_review 로 보류 중인 건의 '해제'(저등급 하향 포함).
    (2)가 없으면 A가 S3→TS 로 보류시킨 건을 B 1인이 S3 confirm 으로 무게이트 해제하는 비대칭
    우회가 생긴다(상향엔 2인·하향 해제엔 1인). prior_status 는 이 트랜잭션에서 status 를 덮어쓰기
    **전**의 원래 값이어야 한다(confirm 은 게이트 전에 'confirmed'로 세팅하므로 반드시 전달).
    correction 은 이미 기록된 뒤 호출되므로 현재 검수자가 집합에 포함된다.

    반환: (적용할 status, second_review_required).
    """
    from koipa.config import settings  # noqa: PLC0415
    if not getattr(settings, "high_grade_dual_review", False):
        return base_status, False
    high_codes = set(getattr(settings, "high_grade_review_codes", ["TS", "S1"]))
    held = prior_status == "needs_second_review"
    if corrected_label not in high_codes and not held:
        return base_status, False
    reviewers = repo.distinct_reviewers_for_level(cls.classification_id, corrected_level_id)
    # [C-cons 신뢰도 배선] min_reliability>0이면 저신뢰 검수자 동의는 정족수에서 제외.
    # 같은 db 세션으로 신뢰도 계산(방금 추가한 교정 반영). 이력 없는 신규 검수자는 신뢰(1.0).
    min_rel = float(getattr(settings, "high_grade_review_min_reliability", 0.0))
    if min_rel > 0 and reviewers:
        from koipa.modules.m6_evaluation.reviewer_trust import (  # noqa: PLC0415
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
    kind = "high-grade change" if corrected_label in high_codes else "resolving a held review"
    warns.append(
        f"{kind} to {corrected_label} needs a second distinct reviewer "
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

                # 게이트가 원래 status(needs_second_review 보류 여부)를 볼 수 있도록 덮어쓰기 전에 포착.
                prior_status = cls.status
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
                    # 정답 확정(신규 교정 1건) — 라이브 FNR 분모/분자 동반 증가(멱등 재클릭 제외).
                    _record_live_fnr(cls.predicted_level_id, target_level_id)
                # [C-cons] 고등급 확정도 2인검토 통과 전까지 보류(기본 off → 'confirmed' 그대로).
                status, second_required = _apply_dual_review_gate(
                    repo, cls, target_level_id, req.confirmed_label,
                    base_status="confirmed", warns=warns, prior_status=prior_status,
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
                    # 정답 확정(신규 교정 1건) — 예측(predicted_level_id) 대비 확정등급으로 FNR 배선.
                    _record_live_fnr(cls.predicted_level_id, corr_id)
                # [C-cons] 고등급 변경은 2인검토 통과 전까지 needs_second_review로 보류(기본 off).
                # relabel 은 게이트 전에 status 를 바꾸지 않으므로 cls.status 가 곧 prior_status.
                status, second_required = _apply_dual_review_gate(
                    repo, cls, corr_id, req.corrected_label, base_status="corrected",
                    warns=warns, prior_status=cls.status,
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


# ── 검수 근거 조회 (FUN-023 근거 출력 · FUN-024 검수자 UI) ──────────────────────
# [실측 2026-08-08] 검수 큐는 등급·신뢰도만 보여 주고 "왜 이 등급인가"가 없었다.
# /classify/explain 이 있지만 검수 화면이 쓸 수 없다 — 그 엔드포인트는 **문서를 다시 분류**하고
# (docstring 1단계), 큐가 가진 본문은 text_preview 300자뿐이라 그 조각으로 돌리면 원문이 아닌
# 다른 입력을 설명하게 된다. 게다가 재분류는 '지금 활성 모델' 기준이라, 그 사이 모델이 바뀌면
# 화면의 등급과 근거가 어긋난다.
#
# 그럴 필요가 없다. 근거는 분류 시점에 이미 저장된다 —
# tb_classification_evidence(excerpt·contribution·evidence_type·rag_ref), classify_service 가
# add_evidence_from_spans 로 기록한다. 실서버 확인: 4건 분류에 13행 적재돼 있었다.
# 저장분을 읽으면 (1) 재추론 비용 0 (2) 모델 불일치 없음 (3) **그 등급을 실제로 만든 근거**다.
def load_review_evidence(classification_id) -> dict:
    """검수 큐 1건의 '왜 이 등급인가' — 저장된 근거를 그대로 돌려준다(재추론 없음).

    반환: {classification_id, recorded{grade·confidence·model_version·alternatives}, evidence[],
           by_type{}, warnings[]}. 근거가 없으면 evidence=[] + 그 사실을 warnings 로 알린다 —
    비어 있는 것을 "근거 없음"으로 조용히 넘기지 않는다(룰 경로 미적재/구버전 분류 구분).
    """
    out: dict = {"classification_id": str(classification_id), "evidence": [], "warnings": []}
    try:
        from sqlalchemy import select  # noqa: PLC0415
        from koipa.db.models import (  # noqa: PLC0415
            Classification, ClassificationEvidence, ClassificationLevel,
        )
        with session_scope() as db:
            row = db.execute(
                select(Classification, ClassificationLevel.level_code)
                .join(ClassificationLevel,
                      ClassificationLevel.level_id == Classification.predicted_level_id)
                .where(Classification.classification_id == classification_id)
            ).first()
            if row is None:
                return {**out, "error": "not_found"}
            cls, level_code = row
            out["doc_id"] = str(cls.doc_id)
            out["recorded"] = {
                "grade": level_code,
                "confidence": float(cls.confidence) if cls.confidence is not None else None,
                "model_version": cls.model_version,
                "classified_at": cls.classified_at.isoformat() if cls.classified_at else None,
                "alternatives": list(cls.alternatives or []),
                "automation_assessment": cls.automation_assessment,
                "rag_used": bool(cls.rag_used),
            }
            evs = db.execute(
                select(ClassificationEvidence)
                .where(ClassificationEvidence.classification_id == classification_id)
                .order_by(ClassificationEvidence.contribution.desc())
            ).scalars().all()
            for e in evs:
                out["evidence"].append({
                    "type": e.evidence_type,
                    "excerpt": e.excerpt,
                    "contribution": float(e.contribution) if e.contribution is not None else None,
                    "start": e.excerpt_start,
                    "end": e.excerpt_end,
                    "rag_similarity": (float(e.rag_similarity)
                                       if e.rag_similarity is not None else None),
                })
    except SQLAlchemyError as exc:
        logger.warning("load_review_evidence failed: id=%s err=%s", classification_id, exc)
        return {**out, "error": "db_unavailable"}
    except Exception:  # noqa: BLE001
        logger.exception("load_review_evidence unexpected failure: id=%s", classification_id)
        return {**out, "error": "lookup_failed"}

    # 유형별 기여도 합 — 화면이 "무엇이 이 등급을 끌어올렸나"를 한눈에 보여줄 수 있게.
    agg: dict[str, float] = {}
    for e in out["evidence"]:
        agg[e["type"]] = round(agg.get(e["type"], 0.0) + (e["contribution"] or 0.0), 3)
    out["by_type"] = dict(sorted(agg.items(), key=lambda kv: -kv[1]))

    if not out["evidence"]:
        # 근거 0 은 결함이 아니라 신호다. 적재 조건이 "룰 증거 span 이 잡혔을 때"
        # (classify_service: `if pred.evidence or pred.rag_context`)이므로, 키워드 앵커 없이
        # 모델 확률만으로 판정된 건은 남길 근거가 없다. 그리고 그런 건이 바로 신뢰도가 낮아
        # 검수로 라우팅되는 건이다 — 실서버 실측(2026-08-08)에서 needs_review 8건은 전부 근거 0,
        # 근거가 있는 건은 confirmed/staging 쪽이었다.
        # 검수자에게는 "왜 비었는지"와 "그럼 무엇을 보고 판단하는지"를 함께 알려야 한다.
        out["warnings"].append(
            "룰 근거(키워드·문맥 앵커)가 잡히지 않은 건입니다 — 모델 확률만으로 판정됐다는 뜻이고, "
            "검수로 넘어온 이유이기도 합니다. 아래 차순위 등급과 신뢰도를 근거로 판단하십시오."
        )
    return out
