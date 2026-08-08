"""POST /confirm + /relabel — 관리자 분류 확정 + 능동학습 입력."""

from __future__ import annotations

import logging

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from lloydk.api._rbac import require_role
from lloydk.schemas.confirm import (
    ConfirmRequest,
    ConfirmResponse,
    RelabelRequest,
    RelabelResponse,
    ReviewQueueResponse,
)
from lloydk.services.confirm_service import (
    ConfirmService,
    RelabelService,
    list_review_queue,
    load_review_evidence,
    resolve_review_statuses,
    to_confirm_response,
    to_relabel_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["confirm"])


def resolve_actor_user_id(client_user_id: str, auth: dict) -> tuple[str, bool]:
    """corrected_by 로 기록될 actor 신원을 인증 principal 로 확정 — 위조 차단(순수).

    corrected_by(=actor.user_id)는 promotion_service 에서 label_source='human_review' 의
    labeler_id 로 흘러 평가진실(locked_gold_eval 승급) 경로에 기록된다. 클라이언트가 보낸
    user_id 를 그대로 쓰면 reviewer 로 인증한 호출자가 actor.user_id='타인' 을 새겨 위조 신원이
    사람검수 라벨러로 남는다(_is_machine_reviewer 는 머신 접두사만 막아 사람형 위조는 통과).

    정책: jwt sub(서명=위조불가)가 있으면 그것이 권위(override). api_key(공유키)는 개별 신원이
    없고, 신뢰된 KL 포털이 actor.user_id 로 실 검수자를 전파하는 설계 채널이므로 클라 값을 유지한다
    (G1 /admin/model/activate 와 동일 원칙: jwt=claims.sub, api_key=신원 없음).

    Returns (effective_user_id, overridden) — overridden=True 면 클라 값과 인증 신원이 달라
    덮어썼음(위조 시도 또는 클라 버그 신호).
    """
    claims = auth.get("claims")
    authed = getattr(claims, "sub", None) if claims is not None else None
    if authed:
        return authed, (authed != client_user_id)
    return client_user_id, False


def _bind_authenticated_actor(actor, auth: dict) -> None:
    """클라이언트가 보낸 actor.user_id 를 인증 principal 로 바인딩(위조 차단, in-place)."""
    effective, overridden = resolve_actor_user_id(actor.user_id, auth)
    if overridden:
        logger.warning(
            "actor.user_id 위조 차단: client=%r → 인증 sub=%r (corrected_by 는 인증 신원으로 기록)",
            actor.user_id, effective,
        )
    actor.user_id = effective


# 공개 별칭 — 다른 라우터(promotion/documents/guide/synthesis/training/golden)가 동일 신원
# 바인딩을 재사용한다(#13: body actor.user_id 가 아니라 인증 principal 로 감사 신원 확정).
bind_authenticated_actor = _bind_authenticated_actor


@router.post("/confirm", response_model=ConfirmResponse)
def confirm(
    req: ConfirmRequest,
    auth: dict = Depends(require_role("admin", "reviewer", "kl_backend")),
):
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 확정.
    # [신원 무결성] corrected_by 가 human_review 라벨러로 흐르므로 클라 자칭이 아닌 인증 신원으로.
    _bind_authenticated_actor(req.actor, auth)
    result = ConfirmService().confirm(req)
    return to_confirm_response(result)


@router.get("/review-queue", response_model=ReviewQueueResponse)
def review_queue(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str = Query(
        default="pending",
        description="pending(기본=needs_review+needs_second_review) | needs_review | needs_second_review | all",
    ),
    auth: dict = Depends(require_role("admin", "reviewer", "kl_backend")),
):
    """검수 대기(승인 대기) 분류 목록 — DB에 쌓인 needs_review 를 서버측에서 조회(FUN-024).

    admin 콘솔의 세션-only 큐를 대체·보강한다: 브라우저 세션과 무관하게 실제 대기 건을 FIFO 로
    반환해, 검수자가 페이지를 열면 '승인 대기 문서'를 바로 본다. DB 미가용 시 items=[] +
    warnings(빈 큐와 조회실패 구분). 확정/재라벨은 기존 /confirm·/relabel 로.
    """
    statuses = resolve_review_statuses(status)
    items, total, warnings = list_review_queue(limit=limit, offset=offset, statuses=statuses)
    return ReviewQueueResponse(items=items, total=total, limit=limit, offset=offset, warnings=warnings)


@router.get(
    "/review-queue/{classification_id}/evidence",
    summary="검수 1건의 근거 — '왜 이 등급인가' (저장분 조회, 재추론 없음)",
    description=(
        "분류 시점에 tb_classification_evidence 로 적재된 근거를 그대로 반환한다(FUN-023 근거 "
        "출력 · FUN-024 검수자 UI). 재분류하지 않는다 — /classify/explain 은 문서를 다시 "
        "분류하므로 (1) 큐가 가진 300자 미리보기로는 원문과 다른 결과가 나오고 (2) 그 사이 "
        "모델이 바뀌면 화면의 등급과 근거가 어긋난다. 저장분은 그 등급을 실제로 만든 근거다. "
        "근거가 0건이면 evidence=[] 와 함께 그 사실을 warnings 로 알린다(무음 처리하지 않음)."
    ),
)
def review_item_evidence(
    classification_id: UUID,
    auth: dict = Depends(require_role("admin", "reviewer", "kl_backend")),
) -> dict:
    return load_review_evidence(classification_id)


@router.post("/relabel", response_model=RelabelResponse)
def relabel(
    req: RelabelRequest,
    auth: dict = Depends(require_role("admin", "reviewer")),
):
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 relabel.
    _bind_authenticated_actor(req.actor, auth)
    result = RelabelService().relabel(req)
    return to_relabel_response(result)
