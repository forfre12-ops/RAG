"""preflight_host.sh — 컨테이너 런타임이 없어도 번들에 rpms/ 가 있으면 차단하지 않는다.

배경(2026-09-29, Rocky 8.10 백지 호스트 리허설). `setup.sh` 는 0단계에서 preflight_host.sh 를 부르고
차단(FAIL)이 나오면 그 자리에서 죽는다. 그런데 preflight 는 "런타임 없음"을 무조건 차단으로 셌다.
그 결과 도커가 없는 호스트 — 번들이 rpms/ 를 싣는 바로 그 경우 — 에서는 setup.sh 2단계의
"번들 rpms/ 로 오프라인 설치" 코드에 **도달할 수 없었다**. INSTALL.md §0-2 는 "사전 설치를 요구하지
않는다"고 약속하는데, 이전 리허설은 전부 RPM 을 손으로 먼저 깔고 setup.sh 를 돌려 이 경로를 못 봤다.
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


def _runtime_block() -> str:
    start = _TEXT.index('head_ "2. 컨테이너 런타임"')
    end = _TEXT.index("# ── 3. SELinux", start)
    return _TEXT[start:end]


def _run(cwd: Path) -> subprocess.CompletedProcess:
    """런타임 검사 블록만 뽑아, docker·podman 이 PATH 에 없는 호스트처럼 돌린다(bash 내장 명령만 쓴다)."""
    script = (
        "set -uo pipefail\n"
        "fail=0; warn=0\n"
        "ok()   { printf 'OK:%s\\n' \"$*\"; }\n"
        "note() { printf 'NOTE:%s\\n' \"$*\"; }\n"
        "warning() { printf 'WARN:%s\\n' \"$*\"; warn=$((warn+1)); }\n"
        "bad()  { printf 'FAIL:%s\\n' \"$*\"; fail=$((fail+1)); }\n"
        "head_() { printf '%s\\n' \"$*\"; }\n"
        "PATH=/nonexistent\n"
        f"{_runtime_block()}\n"
        'echo "RESULT fail=$fail warn=$warn"\n'
    )
    return subprocess.run([BASH, "-c", script], cwd=cwd, capture_output=True, encoding="utf-8")


def test_no_runtime_but_bundle_has_rpms_warns_instead_of_blocking(tmp_path: Path):
    (tmp_path / "rpms").mkdir()
    (tmp_path / "rpms" / "docker-ce-29.8.1-1.el8.x86_64.rpm").write_bytes(b"x")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "RESULT fail=0 warn=1" in result.stdout, result.stdout
    assert "번들 rpms/ 로 설치한다" in result.stdout
    assert "FAIL:" not in result.stdout


def test_no_runtime_and_no_rpms_still_blocks(tmp_path: Path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "RESULT fail=1 warn=0" in result.stdout, result.stdout
    assert "번들에 rpms/ 도 없다" in result.stdout


def test_empty_rpms_dir_does_not_count_as_a_runtime_source(tmp_path: Path):
    """rpms/ 폴더만 있고 .rpm 이 없으면 setup.sh 도 설치하지 못한다 — 차단해야 한다."""
    (tmp_path / "rpms").mkdir()
    result = _run(tmp_path)
    assert "RESULT fail=1" in result.stdout, result.stdout


def test_setup_runs_runtime_install_after_the_preflight():
    """setup.sh 의 순서 전제: 0단계 점검 → 2단계 런타임 설치. 이 순서가 바뀌면 위 수정의 근거도 바뀐다."""
    setup = (PREFLIGHT.parent / "setup.sh").read_text(encoding="utf-8")
    assert setup.index("preflight_host.sh") < setup.index("localinstall")
    assert "번들의 rpms/ 로 오프라인 설치" in setup
