"""콘솔 5면의 상단이 한 벌인가 — 마크업·메뉴·CSS·마운트 지점.

왜(2026-08-20). 상단이 세 갈래로 갈라져 있었다(실측).

    header.top   골든셋 검수·서명 · 후보 관리 · 로그인
    nav.nav      관리자 콘솔(admin.html) · 등급 시연(index.html)
    로고 3종     base64 PNG · 인라인 SVG 근사본 · 외부 PNG 파일
    기관명 옆    .product / .brand-sub / .brand-url — 클래스도 문구도 제각각

사용자 지시로 **검수·서명 화면의 header.top 을 기준**으로 합쳤다. 정적 2면은 파이썬을
못 부르므로 같은 마크업을 파일에 박아 넣는다 — 그래서 손으로 고치면 다시 갈라진다.
이 시험이 그 어긋남을 잡는다.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from koipa.api.golden import _render_console_login_html, _render_specledger_gold_console_html
from koipa.console_nav import CONSOLE_LINKS, HEADER_CSS, header_html
from koipa.golden_review_html import _nav_html

_POC = Path(__file__).resolve().parents[1]
STATIC = _POC / "src" / "koipa" / "api" / "static"

# [2026-08-21] 「로그인」 추가 — 쿠키 없는 브라우저에서 콘솔 전 기능이 401 인데
# 어느 화면에도 login.html 주소가 없었다(시연장에서 멈추는 자리).
# [2026-08-24] 「로그인」을 뺐다(사용자 지시). login.html 화면은 살아 있고 주소로 열린다 —
# 메뉴에서만 뺀 것이다. 목록의 정본은 console_nav.CONSOLE_LINKS 이고 이 상수는 그 사본이라,
# 아래 test_menu_labels_match_the_single_source 가 둘이 어긋나면 잡는다.
# [2026-08-24] 「검증문서 검수 목록」도 뺐다(사용자 지시, 4항목 → 3항목). 그 항목만 목적지가
# 다른 화면이 아니라 「관리자 콘솔」과 **같은 화면의 내부 앵커**(#gold-jobs-card)여서, 이
# 메뉴가 지키는 원칙("메뉴 하나 = 서로 다른 화면 하나", 61f1a94f)을 깨는 유일한 항목이었다.
# 검수 목록은 이제 관리자 콘솔 「검증문서」 탭의 첫 카드다(golden_jobs.js order:1).
# [2026-08-24] 「검증문서 후보 관리」도 뺐다(3항목 → 2항목, 사용자 판단). 그 항목만
# 포털 로그인을 거쳐야 열리고(다른 둘은 그냥 열린다) 특정 업무 화면이라 층위가 달랐다.
# 진입은 관리자 콘솔 「검증문서 현황」 카드의 [후보 관리 화면 열기 ↗] 버튼이 맡는다.
LABELS = ["관리자 콘솔", "등급 시연"]


def _screens() -> dict[str, str]:
    """다섯 화면의 렌더 결과(정적 2면은 파일 내용)."""
    return {
        "signoff": _nav_html("골든셋 검수 · 서명", "full-train", "서명"),
        "manage": _render_specledger_gold_console_html(),
        "login": _render_console_login_html(),
        "admin.html": (STATIC / "admin.html").read_text(encoding="utf-8"),
        "index.html": (STATIC / "index.html").read_text(encoding="utf-8"),
    }


def _header_of(html: str) -> str:
    m = re.search(r'<header class="top">.*?</header>', html, re.S)
    assert m, "header.top 이 없다"
    return m.group(0)


# ── 골격 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", list(_screens()))
def test_every_screen_has_the_same_header_skeleton(name):
    head = _header_of(_screens()[name])
    for frag in (
        '<span class="mark"><span class="brand-mark"><img src="data:image/png;base64,',
        '<span class="brand">한국지식재산보호원</span>',
        '<div class="cnav">',
        '<span class="spacer"></span>',
    ):
        assert frag in head, f"{name}: {frag!r} 이 상단에 없다"
    # [2026-08-24] `.divider`·`.product` 를 공통 골격에서 뺐다. 메뉴가 현재 화면을 진하게
    # 표시하므로 왼쪽에 같은 이름을 또 적는 것은 중복이다(console_nav.header_html).
    # 메뉴에 **없는** 화면(검수·서명, 로그인)만 자기 이름을 밝힌다 — 아래 시험이 그것을 잡는다.
    # manage 는 메뉴에서 빠졌으므로(console_nav 3→2) 자기 이름을 밝히는 쪽이다.
    in_menu = name in {"admin.html", "index.html"}
    if in_menu:
        assert '<span class="product">' not in head, f"{name}: 메뉴에 있는 화면인데 이름을 또 적는다"
    else:
        assert '<span class="product">' in head, f"{name}: 메뉴에 없는 화면인데 이름이 없다"


@pytest.mark.parametrize("name", list(_screens()))
def test_old_header_skeletons_are_gone(name):
    """옛 골격이 남아 있으면 화면마다 상단이 두 개가 된다.

    ⚠ `nav.nav` 문자열 자체를 금지하면 안 된다 — 후보 관리 화면의 **왼쪽 사이드바 목차**가
    같은 클래스를 쓴다(golden.py). 옛 상단만 가리키는 표지를 본다.
    """
    html = _screens()[name]
    assert html.count('<header class="top">') == 1, f"{name}: 상단이 한 개가 아니다"
    for dead in ('class="nav-inner"', 'class="brand-name"', 'class="brand-sep"',
                 'class="brand-sub"', 'class="brand-url"', 'class="nav-right"',
                 'class="nav-actions"'):
        assert dead not in html, f"{name}: 옛 상단 조각 {dead} 가 남아 있다"


@pytest.mark.parametrize("name", list(_screens()))
def test_every_menu_item_is_in_the_top_bar(name):
    """사용자 지시: 공용 메뉴가 다섯 화면 전부의 최상단에 있어야 한다.

    [2026-08-24] 종전 이름은 test_four_menu_items_are_in_the_top_bar 였다. 개수(4)를 이름에
    박아 두면 항목이 바뀔 때마다 이름이 거짓이 된다 — 이 시험이 실제로 잠그는 것은 개수가
    아니라 "LABELS 전부가 상단에 있다"이고, LABELS 는 아래
    test_menu_labels_match_the_single_source 가 console_nav.CONSOLE_LINKS 와 묶어 둔다.
    """
    head = _header_of(_screens()[name])
    for label in LABELS:
        assert label in head, f"{name}: 메뉴 「{label}」 이 상단에 없다"


def test_menu_labels_match_the_single_source():
    assert [label for _, label, _ in CONSOLE_LINKS] == LABELS


@pytest.mark.parametrize("name", list(_screens()))
def test_menu_sits_before_the_spacer(name):
    """메뉴가 스페이서 뒤로 가면 화면별 부속에 밀려 위치가 달라진다."""
    head = _header_of(_screens()[name])
    assert head.index('<div class="cnav">') < head.index('<span class="spacer">')


# ── 화면별 부속이 살아 있는가 — 지우면 JS 가 죽는다 ──────────────────────────

def test_screen_specific_widgets_survived():
    s = _screens()
    # admin.html:checkHealth 가 이 둘을 id 로 잡는다. 없으면 배포 프로파일 배너까지 죽는다.
    assert 'id="health"' in _header_of(s["admin.html"])
    assert 'id="health-txt"' in _header_of(s["admin.html"])
    # [2026-08-24] index 의 프로파일 칩(#nav-status)은 뺐다 — 이 화면에만 있던 요소이고
    # 배포 주체·화면 종류는 deploy_badge.js 가 두 콘솔에 같은 모양으로 붙인다.
    assert 'id="nav-status"' not in _header_of(s["index.html"])
    # golden.py 의 인라인 JS 가 무가드로 잡는다 — 없으면 KPI·품질·원장 렌더가 통째로 멈춘다.
    assert 'id="topCount"' in _header_of(s["manage"])
    # [2026-08-24] 앵커 **대상**만 잠근다. 종전에는 `href="#sec-parse"` 를 찾았는데, 그 링크는
    # 상단 바의 구역 이동 메뉴 4개 중 하나였고 사용자 지시로 그 메뉴가 빠졌다
    # (scripts/sync_console_header.py). 주소가 살아 있어야 한다는 취지는 그대로다 —
    # 사용설명서·배포 가이드·parse_demo.html 스텁이 이 주소를 쓴다. 들어오는 링크가 있는지는
    # tests/test_demo_static_assets.py 가 admin.html 쪽에서 따로 잠근다.
    assert 'id="sec-parse"' in s["index.html"]


# ── 정적 2면이 console_nav 와 어긋나지 않는가 ────────────────────────────────

def test_static_pages_match_the_generator():
    """`scripts/sync_console_header.py --check` 가 통과해야 한다."""
    r = subprocess.run(
        [sys.executable, str(_POC / "scripts" / "sync_console_header.py"), "--check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(_POC),
    )
    assert r.returncode == 0, f"정적 화면이 console_nav 와 어긋난다:\n{r.stdout}{r.stderr}"


def test_static_pages_carry_the_header_css():
    """정적 화면은 파이썬을 못 부르므로 CSS 도 박혀 있어야 한다."""
    admin = (STATIC / "admin.html").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")
    for blob in (admin, styles):
        assert HEADER_CSS in blob


def test_header_css_is_scoped_so_page_brand_rules_do_not_win():
    """admin.html·styles.css 는 `.brand` 를 flex 컨테이너로 이미 정의했다.

    스코프가 없으면 그쪽이 이겨 기관명이 가늘게 나온다(2026-08-20 실측).
    """
    assert ".top .brand{" in HEADER_CSS
    # `.brand{` 가 나오는 곳은 전부 `.top .brand{` 여야 한다(스코프 없는 정의 0건).
    assert HEADER_CSS.count(".brand{") == HEADER_CSS.count(".top .brand{")
    assert HEADER_CSS.count(".product{") == HEADER_CSS.count(".top .product{")


# ── 배지·앵커가 새 골격을 안다 ───────────────────────────────────────────────

def test_deploy_badge_mounts_into_the_unified_header():
    """셀렉터가 .nav-inner 뿐이면 배지가 헤더 밖 body 맨 위로 떨어진다."""
    js = (STATIC / "deploy_badge.js").read_text(encoding="utf-8")
    assert "header.top" in js


def test_admin_console_opens_the_tab_for_a_hash_anchor():
    """검수 목록 카드는 기본 탭에 없다 — 주소에 해시를 달고 오면 해시 처리가 탭을 열어야 한다.

    [2026-08-24] 종전에는 앵커 이름을 CONSOLE_LINKS["signoff"] 에서 뽑아 썼다. 그 메뉴 항목이
    없어졌으므로(4항목 → 3항목: 그 항목만 다른 화면이 아니라 이 화면의 내부 앵커를 가리켰다)
    이름을 직접 적는다. **잠그는 것은 그대로다** — 이 기계장치는 메뉴가 없어져도 필요하다:
    이 카드는 기본 탭(ops)이 아닌 「검증문서」 탭(review)에 있어서, 인쇄물·북마크·화면 안
    링크가 이 주소를 달고 오면 대상이 display:none 인 채로 도착한다(2026-08-20 실측).
    """
    admin = (STATIC / "admin.html").read_text(encoding="utf-8")
    jobs = (STATIC / "golden_jobs.js").read_text(encoding="utf-8")
    assert "function gotoHash()" in admin
    assert "hashchange" in admin
    assert "window.gotoHash" in jobs, "런타임 삽입 카드라 mount 뒤에 한 번 더 불러야 한다"

    anchor = "gold-jobs-card"
    assert f'id="{anchor}"' in jobs, f"#{anchor} 를 만드는 곳이 없다"
    assert 'data-pane="review"' in jobs, f"#{anchor} 가 기본 탭에 있으면 이 시험의 전제가 깨진다"


def test_signoff_screen_is_not_a_fixed_link_target():
    """검수 화면은 job_id·?t= 가 있어야 열린다 — 메뉴가 그 주소를 직접 가리키면 403 이다."""
    for _, _, href in CONSOLE_LINKS:
        assert "signoff.html" not in href and "review.html" not in href


def test_logo_is_the_same_inline_png_everywhere():
    """로고가 화면마다 다르면(외부 파일·SVG 근사본) 통일이 아니다."""
    srcs = set()
    for html in _screens().values():
        srcs.update(re.findall(r'<img src="(data:image/png;base64,[^"]+)"', _header_of(html)))
    assert len(srcs) == 1, f"로고 src 가 {len(srcs)}종이다"
    for html in _screens().values():
        assert "koipa_logo_mark.png" not in _header_of(html), "외부 파일 참조가 남아 있다"


def test_sync_script_is_importable_and_idempotent():
    spec = importlib.util.spec_from_file_location(
        "_sync_console_header", _POC / "scripts" / "sync_console_header.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.sync(check=True) == 0


def test_header_html_escapes_the_product_name():
    assert "<b>" not in header_html("<b>x</b>")


def test_browser_tab_titles_share_one_shape():
    """탭 제목도 양식이 하나여야 한다 — `한국지식재산보호원 | 화면이름`.

    종전에는 다섯 화면이 전부 달랐다(실측): 기관명만 / `KOIPA AI … | Koipa` /
    기관명 없음 셋. 탭을 여러 개 열어 두면 어느 탭이 무슨 화면인지 알 수 없었다.
    """
    import koipa.golden_review_html as grh

    titles = {
        "signoff": grh.render_signoff_html(
            [{"doc_id": "x", "proposed_grade": "S3", "text": "t"}], post_url="/x", job_id="j"
        ),
        "manage": _render_specledger_gold_console_html(),
        "login": _render_console_login_html(),
        "admin.html": (STATIC / "admin.html").read_text(encoding="utf-8"),
        "index.html": (STATIC / "index.html").read_text(encoding="utf-8"),
    }
    for name, html in titles.items():
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        assert m, f"{name}: title 이 없다"
        assert m.group(1).startswith("한국지식재산보호원 | "), f"{name}: {m.group(1)!r}"
