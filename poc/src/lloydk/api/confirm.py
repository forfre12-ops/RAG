"""POST /confirm + /relabel — 관리자 분류 확정 + 능동학습 입력."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from lloydk.api._rbac import require_role
from lloydk.schemas.confirm import (
    ConfirmRequest,
    ConfirmResponse,
    RelabelRequest,
    RelabelResponse,
)
from lloydk.services.confirm_service import (
    ConfirmService,
    RelabelService,
    to_confirm_response,
    to_relabel_response,
)

router = APIRouter(tags=["confirm"])


@router.post(
    "/confirm",
    response_model=ConfirmResponse,
    dependencies=[Depends(require_role("admin", "reviewer", "kl_backend"))],
)
def confirm(req: ConfirmRequest):
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 확정.
    result = ConfirmService().confirm(req)
    return to_confirm_response(result)


@router.post(
    "/relabel",
    response_model=RelabelResponse,
    dependencies=[Depends(require_role("admin", "reviewer"))],
)
def relabel(req: RelabelRequest):
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 relabel.
    result = RelabelService().relabel(req)
    return to_relabel_response(result)
