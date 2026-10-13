"""KL 설치자가 API 키를 어디서 찾는지 — 설치 완료 안내와 번들 README 가 위치·확인 명령을 알려야 한다.

배경(2026-09-29). 설치 로그 4,000줄의 맨 끝에 "API_KEY 는 KL 포털이 X-API-Key 헤더에 넣는 값이다" 한 줄이
있을 뿐 파일 위치·확인 명령이 없었고, 번들 README 에는 API 키 언급이 없었다. 값 자체는 어디에도 적지 않는다
(키·토큰을 화면에 적거나 입력받지 않는 것이 원칙이다) — 위치와 확인 명령만 적는다.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
SETUP = (_POC / "scripts" / "setup.sh").read_text(encoding="utf-8")
README = (_POC / "docs" / "BUNDLE_README.md").read_text(encoding="utf-8")
BASH = shutil.which("bash")


def _completion_block() -> str:
    start = SETUP.index("# 안내문에 적을 .env 의 절대 경로")
    end = SETUP.index('\nDONE\nif [ "$_autologin_on" = "1" ]', start)
    return SETUP[start:end] + "\nDONE\n"


def _render(env_file: str) -> str:
    script = (
        "set -uo pipefail\n"
        f'ENV_FILE="{env_file}"\nBUNDLE="/srv/koipa-bundle"\nAPI_PORT=8000\nNODE=customer\n'
        'NODE_LABEL="고객사 운영 노드"\nPROFILE=onprem-local\nCRT_COMPOSE="docker compose"\n'
        + _completion_block()
    )
    return subprocess.run([BASH, "-c", script], capture_output=True, encoding="utf-8", check=True).stdout


@pytest.mark.skipif(BASH is None, reason="bash 가 없다")
def test_completion_message_names_the_env_path_and_a_check_command():
    out = _render(".env")
    assert "/srv/koipa-bundle/.env" in out, out
    assert "sudo grep '^API_KEY=' /srv/koipa-bundle/.env" in out, out
    assert "X-API-Key" in out and "/api/v1" in out


@pytest.mark.skipif(BASH is None, reason="bash 가 없다")
def test_completion_message_respects_an_absolute_env_file():
    out = _render("/etc/koipa/.env")
    assert "sudo grep '^API_KEY=' /etc/koipa/.env" in out, out


def test_bundle_readme_tells_where_the_api_key_is():
    assert "## KL 포털 연동에 필요한 값" in README
    section = README.split("## KL 포털 연동에 필요한 값", 1)[1].split("\n## ", 1)[0]
    assert "X-API-Key" in section
    assert "`.env`" in section and "API_KEY=" in section
    assert "sudo grep '^API_KEY=' .env" in section


def test_no_key_value_is_written_into_the_guidance():
    """안내는 위치만 적는다 — 64자리 16진 값(키·토큰 모양)이 문서·안내문에 없어야 한다."""
    for name, text in (("setup.sh 완료 안내", _completion_block()), ("BUNDLE_README", README)):
        assert not re.search(r"\b[0-9a-f]{64}\b", text), f"{name} 에 키 모양의 값이 있다"
