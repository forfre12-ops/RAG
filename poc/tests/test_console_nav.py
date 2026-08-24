"""콘솔 화면 간 이동 링크가 **모든 화면에서 같은 목록**인지 잠근다.

왜(2026-08-17). 살아 있는 콘솔 화면이 9면인데 서로 오갈 방법이 거의 없었다.

    manage.html         nav 5개가 전부 같은 페이지 앵커(#overview 등)
    actual-intake.html  헤더에 <a> 가 아예 없었다 (D1 로 manage 모달에 흡수돼 화면 자체가 사라짐)
    review/signoff      _nav_html 에 링크 0개
    정적 3면            자기들끼리만 링크, 골든 콘솔로는 0개(역방향도 0개)

검수자가 관리 화면에서 실문서 수집으로 가려면 주소를 직접 쳐야 했다.

목록은 `koipa/console_nav.py` 한 곳에서 정한다. 그런데 **정적 HTML 3면은 파이썬을 못 부르므로
같은 목록을 손으로 넣는다** — 그래서 한쪽만 고쳐지는 것이 실재하는 위험이고, 이 시험이 그것을 막는다.

⚠ review/signoff 는 네비 **대상**이 될 수 없다. job_id 가 필요하고 golden_html_url_secret 이
  설정되면 ?t= HMAC 토큰까지 있어야 열린다(golden.py `_verify_html_token` 실패 시 403).
  고정 링크로 걸면 403 이 난다 — 그 두 화면에서는 나가는 링크만 둔다. 그것도 잠근다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from koipa.console_nav import CONSOLE_LINKS, nav_bar_html

_STATIC = Path(__file__).resolve().parents[1] / "src" / "koipa" / "api" / "static"
# [D3 2026-08-18] parse_demo.html 은 리다이렉트 스텁이 됐다 — 네비를 넣을 화면이 아니다.
# 파일은 남는다: 그 주소가 KL 사용설명서·배포 스크립트·가이드에 인쇄돼 있다.
_STATIC_PAGES = ("admin.html", "index.html")


def _hrefs(html: str) -> list[str]:
    return re.findall(r'class="cnav-link"[^>]*href="([^"]+)"', html)


def _labels(html: str) -> list[str]:
    return re.findall(r'class="cnav-link(?:\s+is-current)?"[^>]*>([^<]+)<', html)


def test_link_list_is_not_empty():
    # [2026-08-24] 3 → 2. 「검증문서 후보 관리」를 메뉴에서 뺐다(console_nav 주석 참조).
    # 하한을 2 로 둔다 — 남은 둘(관리자 콘솔·등급 시연)은 화면의 종류를 가르는 축이라
    # 그보다 줄면 메뉴가 화면 사이 이동 수단으로서 뜻을 잃는다.
    assert len(CONSOLE_LINKS) >= 2, CONSOLE_LINKS
    for key, label, href in CONSOLE_LINKS:
        assert key and label and href.startswith("/"), (key, label, href)


@pytest.mark.parametrize("page", _STATIC_PAGES)
def test_static_page_carries_the_same_link_list(page):
    """정적 HTML 의 손으로 넣은 목록이 파이썬 목록과 같아야 한다."""
    html = (_STATIC / page).read_text(encoding="utf-8")
    assert "console-nav" in html, f"{page}: 공통 네비가 없다"
    want = {label for _, label, _ in CONSOLE_LINKS}
    got = set(_labels(html))
    assert want == got, f"{page}: 링크 목록 불일치 — 없는 것 {want - got} · 여분 {got - want}"


@pytest.mark.parametrize("page", _STATIC_PAGES)
def test_static_page_hrefs_match(page):
    html = (_STATIC / page).read_text(encoding="utf-8")
    want = {href for key, _, href in CONSOLE_LINKS}
    got = set(_hrefs(html))
    # 현재 화면은 <span> 이라 href 가 없다 — 부분집합이면 된다
    assert got <= want, f"{page}: 목록에 없는 링크 {got - want}"
    assert len(got) >= len(want) - 1, f"{page}: 링크가 모자란다 {want - got}"


def test_dynamic_screens_carry_nav():
    """golden.py 의 세 화면과 review/signoff 의 _nav_html 에 링크가 들어갔는지."""
    from koipa.api.golden import (
        _render_console_login_html,
        _render_specledger_gold_console_html,
    )
    from koipa.golden_review_html import _nav_html

    # [D1 2026-08-17] actual-intake 는 후보 관리의 업로드 모달로 흡수돼 화면이 사라졌다.
    for name, html in (
        ("manage", _render_specledger_gold_console_html()),
        ("login", _render_console_login_html()),
        ("review", _nav_html("검수", "full-train", "review")),
    ):
        got = _hrefs(html)
        assert got, f"{name}: 화면 간 링크가 없다"
        assert set(got) <= {href for _, _, href in CONSOLE_LINKS}, (name, got)


def test_review_and_signoff_are_not_nav_targets():
    """job_id·HMAC 토큰이 필요한 화면을 고정 링크로 걸면 403 이 난다."""
    for _, _, href in CONSOLE_LINKS:
        assert "review.html" not in href, href
        assert "signoff.html" not in href, href
        assert "{job" not in href and "jobs/" not in href, href


def test_current_screen_is_marked_and_not_a_link():
    """[2026-08-24] 대상을 manage → admin 으로 바꿨다.

    「검증문서 후보 관리」가 메뉴에서 빠져 CONSOLE_LINKS 에 'manage' 키가 없다. 종전 판은
    그 키를 dict 로 꺼내 써서 **문자열 grep 에 안 걸리고 돌려야 KeyError 로 나왔다.**
    지금은 목록에 실제로 있는 키를 쓴다.
    """
    key, _, href = CONSOLE_LINKS[0]
    html = nav_bar_html(key)
    assert 'is-current' in html
    assert f'href="{href}"' not in html, "현재 화면이 자기 자신 링크를 갖고 있다"


def test_parse_demo_is_a_stub_that_points_at_the_merged_section():
    """스텁이 제 일을 하는지 — 인쇄된 주소를 따라온 사람이 막히면 안 된다."""
    html = (_STATIC / "parse_demo.html").read_text(encoding="utf-8")
    assert "http-equiv" in html and "refresh" in html
    assert "./index.html#sec-parse" in html
    assert "console-nav" not in html, "스텁에 네비를 넣으면 목록을 두 곳에서 관리하게 된다"


def test_static_pages_have_no_section_jump_menu_in_the_header():
    """[2026-08-24 사용자 지시] 상단 바에 **같은 화면 안 구역 이동** 링크를 두지 않는다.

    종전 index.html 헤더에는 cnav 4개(화면 사이 이동) 바로 뒤에 `nav-link` 4개
    (시연·운영·반영·법령·문서 업로드 = 같은 페이지 앵커)가 붙어 있었다. 생김새가 같은데
    한쪽은 화면을 바꾸고 한쪽은 스크롤만 하니, 어느 것이 화면 이동인지 구별되지 않았다.
    되살아나면(sync_console_header.TRAILING 에 다시 넣으면) 이 시험이 잡는다.
    """
    for name in _STATIC_PAGES:
        html = (_STATIC / name).read_text(encoding="utf-8")
        header = html.split("<header", 1)[1].split("</header>", 1)[0]
        assert 'class="nav-link"' not in header, f"{name}: 상단 바에 구역 이동 링크가 되살아났다"
