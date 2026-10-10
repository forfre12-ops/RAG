"""DR staging용 비동기 분류 배치 스모크.

이 스크립트는 실제 사용자 문서를 사용하지 않는다. 명시적으로 ``--allow-write``를
주었을 때에만 비식별 테스트 문서 두 건을 ``/classify/batch``에 등록하고, 응답에
포함된 job URL을 짧게 폴링하여 완료 상태와 결과 개수를 확인한다.

운영 API 인증 방식은 환경마다 다르므로 API 키를 인자로 받는다. JWT-only 환경에서는
동일한 검증을 운영자가 포털 테스트 자격으로 수행해야 하며, 이 도구가 토큰을 저장하거나
발급하지는 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


_DOCUMENTS = [
    {
        "doc_id": "dr-smoke-async-001",
        "content": "DR 스모크 전용 비식별 문서입니다. 외부 전송이나 실제 업무 정보는 포함하지 않습니다.",
    },
    {
        "doc_id": "dr-smoke-async-002",
        "content": "비동기 배치 상태 조회와 부분 실패 보고를 확인하기 위한 시험 입력입니다.",
    },
]
_TERMINAL = {"done", "partial", "failed"}


class SmokeFailure(RuntimeError):
    """HTTP 계약이나 배치 종결 상태가 기대와 다를 때 사용한다."""


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def run_smoke(
    *,
    url: str,
    api_key: str,
    timeout_seconds: float,
    poll_seconds: float,
    client: Any = None,
) -> dict[str, Any]:
    """비식별 2건을 등록하고, 작업이 종결될 때까지 폴링한다.

    ``client``는 테스트에서만 주입한다. 실제 실행에서는 필요한 시점에 httpx를 가져와
    CLI 자체를 import하는 점검도 의존성 초기화로 실패하지 않게 한다.
    """
    owns_client = client is None
    if client is None:
        import httpx  # noqa: PLC0415

        client = httpx.Client(timeout=timeout_seconds)

    base = url.rstrip("/")
    started = time.monotonic()
    try:
        health = client.get(f"{base}/api/v1/healthz")
        if health.status_code != 200:
            raise SmokeFailure(f"health returned HTTP {health.status_code}")

        created = client.post(
            f"{base}/api/v1/classify/batch",
            headers=_headers(api_key),
            json={"documents": _DOCUMENTS},
        )
        if created.status_code != 202:
            raise SmokeFailure(f"batch submit returned HTTP {created.status_code}: {created.text[:300]}")
        submitted = created.json()
        status_url = submitted.get("status_url")
        job_id = submitted.get("job_id")
        if not isinstance(status_url, str) or not status_url.startswith("/") or not job_id:
            raise SmokeFailure("batch submit response has no usable job_id/status_url")

        while True:
            if time.monotonic() - started > timeout_seconds:
                raise SmokeFailure(f"job {job_id} did not finish within {timeout_seconds:.1f}s")
            status_response = client.get(f"{base}{status_url}", headers=_headers(api_key))
            if status_response.status_code != 200:
                raise SmokeFailure(f"job poll returned HTTP {status_response.status_code}: {status_response.text[:300]}")
            status = status_response.json()
            state = status.get("status")
            if state in _TERMINAL:
                break
            time.sleep(poll_seconds)

        total = status.get("total")
        completed = status.get("completed")
        failed = status.get("failed")
        if total != len(_DOCUMENTS) or not isinstance(completed, int) or not isinstance(failed, int):
            raise SmokeFailure(f"job {job_id} returned invalid counters: {status!r}")
        if completed + failed != total:
            raise SmokeFailure(f"job {job_id} counters do not add up: completed={completed}, failed={failed}, total={total}")
        # 이 스모크의 두 문서는 정상 처리돼야 한다. partial/failed가 202를 반환했다는
        # 사실만으로 DR 경로가 정상이라고 하면 안 된다.
        if state != "done" or completed != total or failed != 0:
            raise SmokeFailure(
                f"job {job_id} did not finish cleanly: status={state}, "
                f"completed={completed}, failed={failed}, total={total}"
            )

        return {
            "job_id": str(job_id),
            "status": state,
            "total": total,
            "completed": completed,
            "failed": failed,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        if owns_client:
            client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DR staging async-batch smoke")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("KOIPA_DR_API_KEY", ""),
        help="API-key 배포에서는 KOIPA_DR_API_KEY 환경변수로도 제공할 수 있습니다.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="비식별 스모크 배치 등록을 명시적으로 허용합니다.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.allow_write:
        print("[async-batch-smoke] REFUSED: pass --allow-write to register the two DR-only documents", file=sys.stderr)
        return 2
    if args.timeout_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("--timeout-seconds and --poll-seconds must be positive")

    try:
        result = run_smoke(
            url=args.url,
            api_key=args.api_key,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must turn every failure into a non-zero exit.
        print(f"[async-batch-smoke] FAIL: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
