"""GET /healthz — 헬스체크 + 데모 콘솔용 backend 정보.

데모 콘솔(/demo)이 상단 상태 배지·warmup 폴링·LLM vs BERT 시간 경쟁 비교
시 운영 vs noop 분기에 사용. 운영 라우터에는 영향 없음.

status 값:
  ok       : 모든 구성 요소가 기대 상태로 동작 중.
  degraded : 일부 구성 요소가 비정상 — 요청은 처리되지만 정확도/기능 저하 가능.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from lloydk.config import settings

router = APIRouter(tags=["health"])
_START = time.time()

# app.py lifespan이 _warmup_models 완료 후 True로 설정.
# uptime 기반 추정 대신 실제 startup 완료 여부를 나타냄.
STARTUP_COMPLETE: bool = False


def _check_model() -> dict:
    """InferencePipeline 로드 상태 확인.

    - classifier_model_dir 설정 + 로드 실패 → degraded (모델 기대했는데 rule-fallback 중)
    - classifier_model_dir 미설정 → rule_fallback (의도된 상태)
    - 정상 로드 → loaded
    """
    model_expected = bool(getattr(settings, "classifier_model_dir", ""))
    try:
        from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415
        svc = ClassifyService.get_instance()
        model_loaded = svc.inference._model is not None
    except Exception:
        return {"status": "unknown", "degraded": model_expected}

    if model_loaded:
        return {"status": "loaded", "degraded": False}
    if model_expected:
        return {"status": "degraded", "degraded": True}
    return {"status": "rule_fallback", "degraded": False}


@router.get("/healthz")
def healthz():
    model_check = _check_model()
    overall = "degraded" if model_check["degraded"] else "ok"

    return {
        "status": overall,
        "model_version": "poc",
        "uptime_sec": int(time.time() - _START),
        # 데모 콘솔 표시용 추가 필드 — 운영에는 무해.
        "deploy_profile": getattr(settings, "deploy_profile", "unknown"),
        "embedding_provider": getattr(settings, "embedding_provider", "unknown"),
        "llm_provider": getattr(settings, "llm_provider", "unknown"),
        "vector_backend": getattr(settings, "vector_backend", "unknown"),
        "reranker_provider": getattr(settings, "reranker_provider", "noop"),
        # warmup 완료 여부 — lifespan의 _warmup_models 완료 후 True.
        # app.py startup이 끝나야 True가 되므로 uptime 기반 추정보다 정확.
        "warmup_done": STARTUP_COMPLETE,
        "checks": {
            "model": model_check["status"],
        },
    }
