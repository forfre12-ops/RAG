import time
from fastapi import APIRouter, Depends, Request

from lloydk.api._jwt_auth import require_auth, resolve_effective_tenant
from lloydk.api.rate_limit import limiter
from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse
from lloydk.services.classify_service import ClassifyService

router = APIRouter(tags=["classify"])


def get_service() -> ClassifyService:
    return ClassifyService.get_instance()


@router.post("/classify", response_model=ClassifyResponse, dependencies=[Depends(require_auth)])
@limiter.limit("60/minute")
def classify(request: Request, req: ClassifyRequest, svc: ClassifyService = Depends(get_service)):
    t0 = time.time()
    # 보안: body의 tenant_id를 인증 컨텍스트에 결속(객체 수준 권한). 인증 tenant와
    # 불일치하면 403. 이후 모든 doc 조회는 이 유효 tenant로 스코프된다.
    req.tenant_id = resolve_effective_tenant(request, req.tenant_id)
    result = svc.classify(req)
    result.elapsed_ms = int((time.time() - t0) * 1000)
    return result
