"""POST /admin/tenants/{tenant_id}/rotate-api-key — 테넌트 API 키 발급·교체.

접근 제한: admin 역할만 허용.
응답: 생성된 raw key는 이 응답에서 한 번만 노출 — 재조회 불가.
"""
from __future__ import annotations

import secrets
import string

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lloydk.api._jwt_auth import hash_api_key
from lloydk.api._rbac import require_role

router = APIRouter(prefix="/admin", tags=["admin"])

_ADMIN_ONLY = [Depends(require_role("admin"))]

_KEY_ALPHABET = string.ascii_letters + string.digits
_KEY_LENGTH   = 48  # 288 bits — NIST SP 800-132 권장 이상


def _generate_raw_key() -> str:
    return "lk_" + "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_LENGTH))


class RotateApiKeyResponse(BaseModel):
    tenant_id: str
    api_key: str   # raw key — 이 응답에서만 노출
    message: str


class ReloadModelResponse(BaseModel):
    reloaded: bool
    model_dir: str | None
    model_version: str
    model_loaded: bool


@router.post(
    "/model/reload",
    response_model=ReloadModelResponse,
    dependencies=_ADMIN_ONLY,
    summary="서빙 모델 핫리로드 (활성 ModelVersion 적용)",
    description=(
        "활성 ModelVersion(model_uri)을 재해석해 추론 파이프라인을 재구성합니다. "
        "activate_model_version/rollback 후 프로세스 재기동 없이 새 모델을 서빙에 반영 (C-ver). "
        "활성 버전이 없거나 로컬 디렉토리가 아니면 env CLASSIFIER_MODEL_DIR로 폴백."
    ),
)
def reload_model() -> ReloadModelResponse:
    from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415
    info = ClassifyService.get_instance().reload_model()
    return ReloadModelResponse(
        reloaded=bool(info["reloaded"]),
        model_dir=info.get("model_dir"),
        model_version=str(info.get("model_version")),
        model_loaded=bool(info.get("model_loaded")),
    )


@router.post(
    "/tenants/{tenant_id}/rotate-api-key",
    response_model=RotateApiKeyResponse,
    dependencies=_ADMIN_ONLY,
    summary="테넌트 API 키 발급·교체",
    description=(
        "새 API 키를 생성하고 bcrypt hash를 DB에 저장합니다. "
        "raw key는 이 응답에서 한 번만 노출됩니다. 재조회 불가."
    ),
)
def rotate_api_key(tenant_id: str) -> RotateApiKeyResponse:
    try:
        from lloydk.db import session_scope  # noqa: PLC0415
        from lloydk.db.models import Tenant  # noqa: PLC0415
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"DB 미가용: {e}") from e

    raw_key = _generate_raw_key()
    key_hash = hash_api_key(raw_key)

    with session_scope() as db:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' 없음")
        tenant.api_key_hash = key_hash

    return RotateApiKeyResponse(
        tenant_id=tenant_id,
        api_key=raw_key,
        message="API 키가 교체되었습니다. 이 값을 안전한 곳에 저장하세요. 재조회 불가.",
    )
