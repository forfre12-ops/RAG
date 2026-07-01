"""GET /promotions/pending + POST /promotions/promote — 교정→검증라벨 승급(사람 승인).

승급은 재학습과 무관한 '비모수 안전 레버'다 — enable_training과 무관하게 **항상 등록**한다
(고객사 폐쇄망에서 동작해야 함). RBAC: admin/reviewer만. 서빙 경로는 무변경 — 검증라벨이
생기면 기존 exact-match override(ClassifyService._get_verified_label)가 자동 반영한다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from lloydk.api._rbac import require_role
from lloydk.schemas.promotion import (
    PendingPromotionResponse,
    PromoteRequest,
    PromoteResponse,
)
from lloydk.services.promotion_service import PromotionService, to_promote_response

router = APIRouter(tags=["promotion"])


@router.get(
    "/promotions/pending",
    response_model=PendingPromotionResponse,
    dependencies=[Depends(require_role("admin", "reviewer"))],
)
def pending_promotions(limit: int = Query(default=100, ge=1, le=1000)):
    """승급 대기 큐 — admission 통과 교정이 있으나 같은 등급의 검증라벨이 없는 문서 목록."""
    items = PromotionService().list_pending(limit=limit)
    return PendingPromotionResponse(count=len(items), items=items)


@router.post(
    "/promotions/promote",
    response_model=PromoteResponse,
    dependencies=[Depends(require_role("admin", "reviewer"))],
)
def promote(req: PromoteRequest):
    """단일 문서의 최신 admissible 교정을 검증 DocumentLabel로 승급."""
    result = PromotionService().promote(req)
    return to_promote_response(result)
