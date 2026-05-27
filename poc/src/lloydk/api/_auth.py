"""공용 X-API-Key 의존성 — 라우터에서 import해 일관 적용."""

from __future__ import annotations

from fastapi import Header, HTTPException

from lloydk.config import settings


def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid api key")
