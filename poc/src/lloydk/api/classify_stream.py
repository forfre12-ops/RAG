"""P2-D5: /classify/stream — Server-Sent Events 스트리밍 분류.

A3(2026-05-29): 가짜 stage yield 제거. ClassifyService.classify(on_stage=...)
콜백을 큐로 받아 실제 단계 진입 시점에 progress 이벤트 송신.

이벤트 종류:
- progress: 실제 stage 진입 (extract/normalize/embed/retrieve/llm/persist/finalize)
- partial:  중간 결과 (top-1 grade, confidence)
- result:   최종 ClassifyResponse
- error:    예외
- done:     스트림 종료
"""

import asyncio
import json
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException
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
    stage_queue: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_stage(stage: str) -> None:
        # 워커 스레드에서 호출됨 → 메인 루프 큐로 thread-safe 전달
        loop.call_soon_threadsafe(stage_queue.put_nowait, stage)

    async def runner():
        try:
            result = await asyncio.to_thread(svc.classify, req, on_stage=on_stage)
            return ("ok", result)
        except Exception as e:  # noqa: BLE001
            return ("err", e)
        finally:
            loop.call_soon_threadsafe(stage_queue.put_nowait, None)  # 종료 신호

    task = asyncio.create_task(runner())

    try:
        # stage 이벤트를 실시간으로 yield
        while True:
            stage = await stage_queue.get()
            if stage is None:
                break
            yield _sse_event("progress", {
                "stage": stage,
                "elapsed_ms": int((time.time() - t0) * 1000),
            })

        kind, payload = await task
        if kind == "err":
            yield _sse_event("error", {
                "message": str(payload)[:200],
                "type": type(payload).__name__,
            })
            yield _sse_event("done", {"elapsed_ms": int((time.time() - t0) * 1000)})
            return

        result = payload
        result.elapsed_ms = int((time.time() - t0) * 1000)
        yield _sse_event("partial", {
            "grade": result.label.value if hasattr(result.label, "value") else str(result.label),
            "confidence": float(result.confidence),
        })
        yield _sse_event("result", json.loads(result.model_dump_json()))
        yield _sse_event("done", {"elapsed_ms": result.elapsed_ms})
    except asyncio.CancelledError:
        task.cancel()
        raise


@router.post("/classify/stream", dependencies=[Depends(require_api_key)])
async def classify_stream(
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
