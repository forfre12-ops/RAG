"""경량 retry 헬퍼 — stdlib only (tenacity 의존 회피).

용도:
- async_classify_service.submit_batch 건별 isolation
- workers/tasks.py classify_async/synthesize_batch retry 경로

설계:
- 동기 호출 가정 (Celery task body, FastAPI sync handler).
- 지수 백오프 (base * 2^attempt) — attempt=0,1,2 → 0/2/4초.
- 호출자가 sleep_fn을 주입 가능 → 테스트는 no-op sleep으로 즉시 진행.
- 마지막 attempt까지 실패하면 마지막 예외 그대로 raise.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def call_with_retry(
    fn: Callable[..., T],
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep_fn: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException], None] | None = None,
    **kwargs: Any,
) -> T:
    """fn을 최대 max_attempts회 호출. 지수 백오프(base_delay * 2^attempt).

    - max_attempts=3 → 1회 본 시도 + 2회 재시도. 요구사항 "최대 2회 retry" 부합.
    - retry_on에 해당하지 않는 예외는 즉시 raise.
    - 마지막 실패 시 마지막 예외 그대로 raise (래핑하지 않음).
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except retry_on as exc:  # noqa: PERF203 - 명시적 retry 루프
            last_exc = exc
            remaining = max_attempts - attempt - 1
            if remaining <= 0:
                logger.warning(
                    "retry exhausted: fn=%s attempts=%d err=%s",
                    getattr(fn, "__name__", repr(fn)), max_attempts, type(exc).__name__,
                )
                raise
            delay = base_delay * (2 ** attempt)
            if on_retry is not None:
                try:
                    on_retry(attempt, exc)
                except Exception:  # noqa: BLE001 - on_retry 부작용 격리
                    logger.exception("on_retry callback raised")
            logger.info(
                "retry: fn=%s attempt=%d/%d delay=%.2fs err=%s",
                getattr(fn, "__name__", repr(fn)),
                attempt + 1, max_attempts, delay, type(exc).__name__,
            )
            sleep_fn(delay)
    # 이 지점은 도달 불가 (위에서 raise되거나 return) — 안전 가드.
    assert last_exc is not None  # noqa: S101
    raise last_exc


def iter_with_partial_failure(
    items: Iterable[Any],
    handler: Callable[[Any], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep_fn: Callable[[float], None] = time.sleep,
    id_of: Callable[[Any], str] = lambda x: getattr(x, "doc_id", str(x)),
) -> tuple[list[T], list[str], list[dict]]:
    """각 item을 handler로 처리. 건별 retry, 영구 실패는 다음 건 진행.

    반환: (results, failed_ids, errors)
    - results: 성공한 건의 handler 반환값 리스트
    - failed_ids: 영구 실패한 item의 id_of(item) 리스트
    - errors: [{"doc_id": ..., "error_type": ..., "message": ..., "attempts": n}]
    """
    results: list[T] = []
    failed_ids: list[str] = []
    errors: list[dict] = []
    for item in items:
        item_id = id_of(item)
        attempts_made = {"n": 0}

        def _wrapped(target_item: Any = item) -> T:
            attempts_made["n"] += 1
            return handler(target_item)

        try:
            res = call_with_retry(
                _wrapped,
                max_attempts=max_attempts,
                base_delay=base_delay,
                retry_on=retry_on,
                sleep_fn=sleep_fn,
            )
            results.append(res)
        except retry_on as exc:
            failed_ids.append(item_id)
            errors.append({
                "doc_id": item_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "attempts": attempts_made["n"],
            })
            logger.error(
                "item permanent failure: doc_id=%s err=%s attempts=%d",
                item_id, type(exc).__name__, attempts_made["n"],
            )
    return results, failed_ids, errors
