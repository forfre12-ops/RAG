"""POST /admin/model/reload — 서빙 모델 핫리로드 (활성 ModelVersion 적용).

접근 제한: admin 역할만 허용.

tenant 제거: 테넌트 API 키 발급·교체(rotate-api-key) 엔드포인트는 제거됨.
인증은 KL 서명 JWT 검증만 사용(tb_tenants·api_key_hash 개념 제거); 격리는 KL 포털 전담.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lloydk.api._rbac import require_role

router = APIRouter(prefix="/admin", tags=["admin"])

_ADMIN_ONLY = [Depends(require_role("admin"))]


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
