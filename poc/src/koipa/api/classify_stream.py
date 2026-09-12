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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from koipa.api._jwt_auth import require_auth
from koipa.api.rate_limit import limiter
from koipa.schemas.classify import ClassifyRequest
from koipa.services.classify_service import ClassifyService

router = APIRouter(tags=["classify"])


def get_service() -> ClassifyService:
    return ClassifyService.get_instance()


def _sse_event(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


# #32: stage 큐 await를 무한 대기 대신 주기적으로 깨워 disconnect를 폴링하기 위한 간격.
_DISCONNECT_POLL_SEC = 0.5


async def _classify_stream(
    request: Request, req: ClassifyRequest, svc: ClassifyService
) -> AsyncGenerator[str, None]:
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
            # #32: disconnect 감지(협조적 취소). 큐 get을 timeout으로 감싸 주기적으로
            # request.is_disconnected()를 폴링한다. 클라이언트가 끊겼으면 진행 중인
            # task를 cancel하고 제너레이터를 조기 종료해 더 이상 yield하지 않는다.
            # (to_thread의 동기 svc.classify 자체는 인터럽트 불가하나, 다운스트림 yield·
            #  SSE 송신을 즉시 멈춰 점유를 줄인다.)
            try:
                stage = await asyncio.wait_for(stage_queue.get(), timeout=_DISCONNECT_POLL_SEC)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    task.cancel()  # 진행 중 작업 취소 신호(스레드는 못 끊어도 후속 점유 차단)
                    return
                continue  # 아직 연결 유지 → 다음 stage 대기 계속
            if stage is None:
                break
            # 큐에서 stage를 꺼낸 직후에도 한 번 더 disconnect를 확인.
            if await request.is_disconnected():
                task.cancel()
                return
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


@router.post("/classify/stream", dependencies=[Depends(require_auth)])
# #32: 스트림은 요청당 워커 스레드(to_thread)를 점유하므로 default(120/min)보다 빡빡한
# 명시 한도를 부여한다(/classify 60·/explain 30과 정렬). slowapi는 request 파라미터 필요 —
# 핸들러에 이미 존재.
@limiter.limit("30/minute")
async def classify_stream(
    request: Request,
    req: ClassifyRequest,
    svc: ClassifyService = Depends(get_service),
):
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 스트림 분류.
    return StreamingResponse(
        _classify_stream(request, req, svc),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
