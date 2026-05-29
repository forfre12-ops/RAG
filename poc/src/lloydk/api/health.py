"""GET /healthz — 헬스체크 + 데모 콘솔용 backend 정보.

데모 콘솔(/demo)이 상단 상태 배지·warmup 폴링·LLM vs BERT 시간 경쟁 비교
시 운영 vs noop 분기에 사용. 운영 라우터에는 영향 없음.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from lloydk.config import settings

router = APIRouter(tags=["health"])
_START = time.time()


@router.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "model_version": "poc",
        "uptime_sec": int(time.time() - _START),
        # 데모 콘솔 표시용 추가 필드 — 운영에는 무해.
        "deploy_profile": getattr(settings, "deploy_profile", "unknown"),
        "embedding_provider": getattr(settings, "embedding_provider", "unknown"),
        "llm_provider": getattr(settings, "llm_provider", "unknown"),
        "vector_backend": getattr(settings, "vector_backend", "unknown"),
        "reranker_provider": getattr(settings, "reranker_provider", "noop"),
        # warmup 완료 여부 — lifespan 의 _warmup_models 가 끝나면 True.
        # uptime ≥ 2s 면 warmup 끝났다고 본다 (lite-noapi 에서는 즉시 반환).
        "warmup_done": (time.time() - _START) >= 2.0,
    }
