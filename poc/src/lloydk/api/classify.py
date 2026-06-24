import time
from fastapi import APIRouter, Depends, Request

from lloydk.api._jwt_auth import require_auth
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
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 분류.
    result = svc.classify(req)
    result.elapsed_ms = int((time.time() - t0) * 1000)
    return result
