"""관리자 콘솔의 실제 HTTP 인수 스모크.

정적 파일 서버나 FastAPI TestClient만으로는 ``/console/admin.html``의 실제
마운트·lifespan·브라우저 프록시 경로를 함께 확인할 수 없다. 이 스크립트는 개발 전용
설정으로 로컬 Uvicorn을 잠시 기동해 healthz와 정적 콘솔을 확인하고, 실제 서버를
upstream으로 사용하는 콘솔 E2E의 읽기 전용 화면 전환 시나리오를 실행한다.

문서·검수·학습 데이터를 만들거나 바꾸지 않는다. production/211 증명도 아니다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = ROOT / "tests" / "e2e_console"
API_KEY = "local-console-acceptance-key"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=1.5) as response:  # noqa: S310 - loopback Uvicorn only
        if response.status != 200:
            raise RuntimeError(f"{url}: HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> dict[str, Any]:
    last_error: Exception | None = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Uvicorn exited before /healthz became ready")
        try:
            return _get_json(f"{base_url}/api/v1/healthz")
        except (OSError, URLError, ValueError, RuntimeError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"/healthz did not become ready: {last_error}")


def _assert_console_is_served(base_url: str) -> None:
    with urlopen(f"{base_url}/console/admin.html", timeout=5) as response:  # noqa: S310 - loopback Uvicorn only
        body = response.read().decode("utf-8")
        if response.status != 200 or 'data-product-nav="overview"' not in body:
            raise RuntimeError("/console/admin.html was not served by the application")


def _stop(process: subprocess.Popen[str]) -> tuple[str, bool]:
    stopped_here = process.poll() is None
    if stopped_here:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return (process.stdout.read() if process.stdout else "", stopped_here)


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("SKIP: node is required for console HTTP acceptance")
        return 0

    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "TESTING": "1",
            "API_KEY": API_KEY,
            "API_KEY_TRUST_ACTOR_ROLE_HEADER": "true",
            # A development/dry-run profile keeps this probe separate from a
            # hardened customer image, which must prove its own credentials,
            # models, data, RBAC and deployment hashes on 211.
            "DEPLOY_PROFILE": "dev",
            "POC_MODE": "dryrun",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "koipa.api.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        health = _wait_for_health(base_url, process)
        _assert_console_is_served(base_url)
        e2e_env = env | {"KOIPA_E2E_API_KEY": API_KEY}
        completed = subprocess.run(
            [
                node,
                "run.mjs",
                "--base",
                base_url,
                "--only",
                "every-view-has-visible-content",
                "--scenario-timeout-ms",
                "15000",
            ],
            cwd=E2E_DIR,
            env=e2e_env,
            check=False,
        )
        if completed.returncode:
            return completed.returncode
        print(
            "PASS local console HTTP acceptance "
            f"(profile={health.get('deploy_profile', 'unknown')}, base={base_url})"
        )
        return 0
    finally:
        log, stopped_here = _stop(process)
        if not stopped_here and process.returncode not in (0, -15) and log:
            print("Uvicorn log:\n" + log, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
