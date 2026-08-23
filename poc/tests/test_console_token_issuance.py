"""검수자별 콘솔 토큰 발급 — 만료를 날짜로 못 박고, 사람마다 파일을 나눈다.

왜(2026-08-17). 사람 검수를 시작하려면 검수자마다 개인 토큰이 있어야 한다. 서명자는
그 토큰의 sub 로 기록되므로(tests/test_signoff_identity_integrity.py), 한 토큰을 여러
명이 쓰면 원장에 같은 이름만 남아 검수 기록이 성립하지 않는다.

두 가지를 고친다.
  1. token.txt 를 매번 덮어써서 두 번째 사람 토큰이 첫 번째를 지웠다 — 사람별 파일로 나눈다.
  2. 만료가 --days 뿐이라 노출 기한을 토큰에 담을 수 없었다 — --until 로 날짜를 못 박는다.
     기한이 지나면 토큰이 스스로 죽는다. '내리는 것을 잊는' 경로를 없앤다.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from argparse import Namespace
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_console_test_login.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("_setup_console_test_login", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _now() -> int:
    return int(dt.datetime(2026, 8, 17, tzinfo=dt.timezone.utc).timestamp())


def test_until_pins_expiry_to_a_calendar_date(script):
    exp = script._expiry(_now(), Namespace(until="2026-12-31", days=30))
    assert exp == int(dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc).timestamp())


def test_until_wins_over_days(script):
    """둘 다 주면 날짜가 이긴다 — 기한은 편의보다 우선한다."""
    n = _now()
    assert script._expiry(n, Namespace(until="2026-12-31", days=3650)) < n + 3650 * 86400


def test_days_still_works_when_no_date_given(script):
    n = _now()
    assert script._expiry(n, Namespace(until="", days=30)) == n + 30 * 86400


def test_past_date_is_refused_instead_of_issuing_a_dead_token(script):
    with pytest.raises(SystemExit, match="이미 지났다"):
        script._expiry(_now(), Namespace(until="2026-01-01", days=30))


@pytest.mark.parametrize("sub,expected", [
    ("hong.gd", "hong.gd"),
    ("kim-cs_2", "kim-cs_2"),
    ("../../etc/passwd", ".._.._etc_passwd"),
    # 한글은 isalnum() 이 참이라 그대로 남는다 — 파일명으로 문제없다. 공백만 바뀐다.
    ("사람 이름", "사람_이름"),
    ("", "unnamed"),
])
def test_token_filename_cannot_escape_the_directory(script, sub, expected):
    """sub 는 사람이 넣는 값이다 — 경로 문자가 파일명으로 새면 안 된다."""
    assert script._safe_name(sub) == expected


def test_script_writes_a_file_per_person():
    src = _SCRIPT.read_text(encoding="utf-8")
    assert 'OUT / "tokens" / f"{_safe_name(args.sub)}.txt"' in src, (
        "사람별 토큰 파일을 안 만든다 — 두 번째 발급이 첫 번째를 지운다"
    )


def test_guidance_points_at_the_login_screen_not_devtools():
    """검수자에게 개발자도구를 열라고 하면 절차가 성립하지 않는다."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "golden/candidates/login.html" in src
    assert "document.cookie='koipa_access_token=" not in src
