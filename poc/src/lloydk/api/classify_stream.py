"""P2-D5: /classify/stream — Server-Sent Events 스트리밍 분류.

LLM이 토큰을 생성하는 동안 클라이언트에 점진적 SSE event 송신.
UX 개선 — 검수자가 첫 토큰부터 즉시 평가 시작 가능.

이벤트 종류:
- progress: stage 변동 (extract/normalize/chunk/embed/retrieve/llm/finalize)
- partial:  중간 결과 (top-1 grade, confidence 갱신)
- result:   최종 ClassifyResponse
- error:    예외 발생 시
- done:     스트림 종료

클라이언트 예:
  fetch('/api/v1/classify/stream', { method: 'POST', body: JSON.stringify(req) })
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from lloydk.config import settings
from lloydk.schemas.classify import ClassifyRequest
from lloydk.services.classify_service import ClassifyService

router = APIRouter(tags=["classify"])


def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid api key")


def get_service() -> ClassifyService:
    return ClassifyService.get_instance()


def _sse_event(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def _classify_stream(req: ClassifyRequest, svc: ClassifyService) -> AsyncGenerator[str, None]:
    t0 = time.time()
    try:
        yield _sse_event("progress", {"stage": "extract", "elapsed_ms": 0})
        await asyncio.sleep(0)  # cooperative yield
        yield _sse_event("progress", {"stage": "normalize", "elapsed_ms": int((time.time() - t0) * 1000)})
        await asyncio.sleep(0)
        yield _sse_event("progress", {"stage": "chunk", "elapsed_ms": int((time.time() - t0) * 1000)})
        await asyncio.sleep(0)
        yield _sse_event("progress", {"stage": "embed", "elapsed_ms": int((time.time() - t0) * 1000)})
        await asyncio.sleep(0)
        yield _sse_event("progress", {"stage": "retrieve", "elapsed_ms": int((time.time() - t0) * 1000)})
        await asyncio.sleep(0)
        yield _sse_event("progress", {"stage": "llm", "elapsed_ms": int((time.time() - t0) * 1000)})

        # 동기 분류 실행 (LLM 스트리밍 어댑터 도입 시 토큰 단위로 분할 송신 가능)
        result = await asyncio.to_thread(svc.classify, req)
        result.elapsed_ms = int((time.time() - t0) * 1000)

        yield _sse_event("partial", {"grade": result.grade.value, "confidence": result.confidence})
        yield _sse_event("progress", {"stage": "finalize", "elapsed_ms": result.elapsed_ms})
        yield _sse_event("result", json.loads(result.model_dump_json()))
        yield _sse_event("done", {"elapsed_ms": result.elapsed_ms})
    except Exception as e:  # noqa: BLE001
        yield _sse_event("error", {"message": str(e)[:200], "type": type(e).__name__})
        yield _sse_event("done", {"elapsed_ms": int((time.time() - t0) * 1000)})


@router.post("/classify/stream", dependencies=[Depends(require_api_key)])
async def classify_stream(
    request: Request,
    req: ClassifyRequest,
    svc: ClassifyService = Depends(get_service),
):
    return StreamingResponse(
        _classify_stream(req, svc),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
