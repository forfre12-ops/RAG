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
    async def _check(auth_context: dict = Depends(require_auth)) -> dict:
        actor_role = auth_context.get("actor_role", "system")
        if actor_role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"forbidden: role '{actor_role}' not in {list(allowed_roles)}",
            )
        return auth_context
    return _check
