import time
from fastapi import APIRouter

router = APIRouter(tags=["health"])
_START = time.time()


@router.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "model_version": "poc",
        "uptime_sec": int(time.time() - _START),
    }
