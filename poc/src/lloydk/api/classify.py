import time
from uuid import uuid4
from fastapi import APIRouter, Depends, Header, HTTPException

from lloydk.config import settings
from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse
from lloydk.services.classify_service import ClassifyService

router = APIRouter(tags=["classify"])


def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid api key")


def get_service() -> ClassifyService:
    return ClassifyService.get_instance()


@router.post("/classify", response_model=ClassifyResponse, dependencies=[Depends(require_api_key)])
def classify(req: ClassifyRequest, svc: ClassifyService = Depends(get_service)):
    t0 = time.time()
    result = svc.classify(req)
    result.elapsed_ms = int((time.time() - t0) * 1000)
    return result
