"""POST /confirm + /relabel — 관리자 분류 확정 + 능동학습 입력."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from lloydk.api._jwt_auth import resolve_effective_tenant
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
def confirm(request: Request, req: ConfirmRequest):
    # 보안: actor.tenant_id를 인증 컨텍스트에 결속(객체 수준 권한 = IDOR/BOLA 차단).
    # 불일치 시 403. 이후 분류 조회는 이 유효 tenant로만 스코프된다.
    eff_tenant = resolve_effective_tenant(request, req.actor.tenant_id)
    result = ConfirmService().confirm(req, tenant_id=eff_tenant)
    return to_confirm_response(result)


@router.post(
    "/relabel",
    response_model=RelabelResponse,
    dependencies=[Depends(require_role("admin", "reviewer"))],
)
def relabel(request: Request, req: RelabelRequest):
    # 보안: 위와 동일 — 교차테넌트 분류를 relabel하지 못하도록 tenant 결속.
    eff_tenant = resolve_effective_tenant(request, req.actor.tenant_id)
    result = RelabelService().relabel(req, tenant_id=eff_tenant)
    return to_relabel_response(result)
