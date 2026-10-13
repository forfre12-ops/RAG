"""검수자에게 주는 검수·서명 링크에는 ?t= 서명 토큰이 붙어야 한다.

왜(실측 2026-08-17, KL 223).

    GOLDEN_HTML_URL_SECRET   설정됨(64자)
    .../signoff.html         -> 403     ← register_review_signoff_job.py 가 출력하던 주소
    .../signoff.html?t=...   -> 200

즉 "검수자에게 줄 것" 으로 인쇄되던 주소가 **열리지 않는 주소**였다. 사람 검수를 시작할 수
없는 상태였고, 화면이 아니라 링크가 원인이라 콘솔을 아무리 봐도 안 보인다.

원인은 스크립트가 경로를 직접 조립한 것이다. 서버는 이미 서명된 URL 을 응답에 담아 준다
(golden.py:187 이 _signed_html_urls 로 review_url·signoff_url 을 채운다). 조립하지 말고
받아 쓰면 된다.

⚠ 토큰은 job_id 만 서명한다(golden.py:86-93). **신원이 아니다.** 링크를 가진 사람은 누구나
  화면을 열 수 있고, 서명자는 별도로 로그인 쿠키(JWT sub)로 정해진다
  (tests/test_signoff_identity_integrity.py).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "register_review_signoff_job.py"


def _load():
    spec = importlib.util.spec_from_file_location("_register_review_signoff_job", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load()


def test_absolute_url_keeps_the_signature_query(script):
    """서버가 준 절대경로에 호스트만 붙인다 — ?t= 를 잘라내면 403 이다."""
    got = script._abs(
        "http://223.130.156.134:8000/api/v1",
        "/api/v1/golden/jobs/abc/signoff.html?t=55f85adf91ebee29acc2bacd",
    )
    assert got == (
        "http://223.130.156.134:8000/api/v1/golden/jobs/abc/signoff.html"
        "?t=55f85adf91ebee29acc2bacd"
    )
    assert "/api/v1/api/v1" not in got, "base 의 /api/v1 이 중복됐다"


def test_absolute_url_handles_base_without_api_prefix(script):
    got = script._abs("http://h:8000", "/api/v1/golden/jobs/abc/review.html?t=x")
    assert got == "http://h:8000/api/v1/golden/jobs/abc/review.html?t=x"


def test_full_url_passes_through(script):
    url = "https://kl.example/api/v1/golden/jobs/abc/signoff.html?t=x"
    assert script._abs("http://h:8000/api/v1", url) == url


def test_script_uses_the_server_supplied_url_not_a_hand_built_path():
    """경로를 직접 조립하면 토큰이 빠진다 — 응답의 signoff_url 을 쓰는지 고정한다."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert 'info.get("signoff_url")' in src, "서버가 준 서명 URL 을 안 읽는다"
    assert 'info.get("review_url")' in src
    # 출력부가 다시 손조립으로 돌아가지 않게 — 인쇄 라인은 _abs 를 거쳐야 한다.
    printed = [ln for ln in src.splitlines() if "서명 화면   " in ln or "검토 화면   " in ln]
    assert len(printed) == 2, f"안내 출력이 2줄이어야 한다: {printed}"
    for ln in printed:
        assert "_abs(api, " in ln, f"손조립 주소가 남아 있다: {ln.strip()}"


def test_script_warns_when_the_url_is_unsigned():
    """비밀키가 꺼진 서버면 ?t= 가 없다 — 그 자체는 정상이나, 켜진 서버면 403 신호다."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert 'signed = "?t=" in signoff_url' in src
    assert "GOLDEN_HTML_URL_SECRET" in src, "왜 403 이 나는지 화면에 설명이 없다"
