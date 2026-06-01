"""Role-based access control (RBAC) — FastAPI Dependency.

사용 예:
    from lloydk.api._rbac import require_role

    @router.post("/train", dependencies=[Depends(require_role("admin", "kl_backend"))])
    def train(...): ...
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from lloydk.api._jwt_auth import require_auth


def require_role(*allowed_roles: str):
    """Dependency factory — 허용 역할 목록에 없으면 403.

    require_auth 반환 dict의 'actor_role' 키를 참조.
    api_key 모드에서는 X-Actor-Role 헤더 또는 기본값 'system' 사용.
    """
    allowed = frozenset(allowed_roles)

    async def _check(auth_context: dict = Depends(require_auth)) -> dict:
        # actor_roles(복수, JWT claim) 우선. 없으면 actor_role 단일로 폴백.
        roles = set(auth_context.get("actor_roles") or ())
        if not roles:
            single = auth_context.get("actor_role")
            if single:
                roles = {single}
        if roles.isdisjoint(allowed):
            raise HTTPException(
                status_code=403,
                detail=f"forbidden: roles {sorted(roles)} not in {sorted(allowed)}",
            )
        return auth_context
    return _check
