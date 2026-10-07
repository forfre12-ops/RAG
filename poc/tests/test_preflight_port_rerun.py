"""preflight_host.sh — 이미 설치된 우리 스택이 쓰는 포트는 재실행이므로 막지 않는다.

배경(2026-09-29, Rocky 8.10 리허설). 설치 완료 안내는 "토큰 만료 전에 setup.sh 를 다시 실행하면 갱신된다"·
"끄려면 CONSOLE_AUTOLOGIN=0 bash setup.sh" 라고 재실행을 안내한다. 그런데 setup.sh 0단계의 preflight 가
API 포트가 이미 쓰이면 무조건 차단(FAIL)해서, 스택이 떠 있는 서버에서 재실행하면 0단계에서 종료 코드 1 로
멈췄다(키·데이터는 보존됐지만 갱신이 안 됐다). 다른 프로세스가 쓰는 포트는 여전히 막아야 한다.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PREFLIGHT = Path(__file__).resolve().parents[1] / "scripts" / "preflight_host.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash 가 없다")

_TEXT = PREFLIGHT.read_text(encoding="utf-8")
PORT = "38123"


def _port_block() -> str:
    start = _TEXT.index('if command -v ss >/dev/null 2>&1 && ss -ltn')
    end = _TEXT.index("# ── 5. 사용자 uid", start)
    return _TEXT[start:end]


def _run(docker_ports_output: str) -> str:
    """ss·docker 를 함수로 대체해 어떤 호스트에서도 같은 결과가 나오게 한다."""
    script = (
        "set -uo pipefail\n"
        "fail=0; warn=0\n"
        "ok()   { printf 'OK:%s\\n' \"$*\"; }\n"
        "bad()  { printf 'FAIL:%s\\n' \"$*\"; fail=$((fail+1)); }\n"
        f"API_PORT={PORT}\n"
        f"ss() {{ printf 'LISTEN 0 4096 127.0.0.1:{PORT} 0.0.0.0:*\\n'; }}\n"
        # 필터가 걸린 docker ps 의 출력을 흉내 낸다(필터에 맞는 컨테이너의 포트 열만 나온다)
        f"docker() {{ printf '%s' '{docker_ports_output}'; }}\n"
        f"{_port_block()}\n"
        'echo "RESULT fail=$fail"\n'
    )
    return subprocess.run([BASH, "-c", script], capture_output=True, encoding="utf-8", check=True).stdout


def test_port_held_by_our_own_stack_is_a_rerun_not_a_block():
    out = _run(f"127.0.0.1:{PORT}->8000/tcp\n")
    assert "RESULT fail=0" in out, out
    assert "재실행으로 본다" in out


def test_port_held_by_someone_else_still_blocks():
    """우리 스택 컨테이너가 그 포트를 게시하지 않으면(다른 프로세스) 차단한다."""
    out = _run("")
    assert "RESULT fail=1" in out, out
    assert "이미 사용 중이다" in out


def test_a_different_port_of_our_stack_does_not_excuse_this_one():
    out = _run("127.0.0.1:35432->5432/tcp\n")
    assert "RESULT fail=1" in out, out


def test_docker_filter_is_the_koipa_airgap_project():
    """compose 프로젝트 이름이 바뀌면 이 예외가 조용히 죽는다 — compose 의 name: 과 같은지 못박는다."""
    compose = (PREFLIGHT.parents[1] / "docker-compose.airgap.yml").read_text(encoding="utf-8")
    assert "\nname: koipa-airgap\n" in "\n" + compose
    assert "label=com.docker.compose.project=koipa-airgap" in _TEXT
