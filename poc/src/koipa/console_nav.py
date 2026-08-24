"""콘솔 화면 간 이동 링크 — 한 곳에서 정한다.

살아 있는 화면 (2026-08-24 실측 6면):
    동적 4면 (golden.py html_router)
        candidates/login.html · candidates/manage.html
        jobs/{job_id}/review.html · jobs/{job_id}/signoff.html   (같은 화면을 준다)
    정적 2면 (/demo · /console StaticFiles)
        admin.html · index.html   (parse_demo.html 은 index.html#sec-parse 리다이렉트 스텁)

⚠ review/signoff 는 네비 대상이 될 수 없다 — job_id 와 ?t= HMAC 토큰이 있어야 열려
  고정 링크는 403 이다(golden.py:742-748·759-765). 그 두 화면에는 나가는 링크만 둔다.
⚠ manage.html 은 포털 JWT 쿠키로 열린다(공유 API Key 거부). 쿠키 없이 누르면 401 이 정상이다.
⚠ 정적 화면은 파이썬을 못 부르므로 같은 목록을 손으로 넣는다 — 여기를 고치면 그 파일들도
  같이 고쳐야 한다(tests/test_console_nav.py 가 잠근다).
"""
from __future__ import annotations

import base64
import html as _html
from functools import lru_cache
from pathlib import Path

# (키, 표시이름, 절대경로) — 절대경로인 이유: 동적(/api/v1/...)·정적(/console/...) 화면의
# 깊이가 달라 상대경로로는 양쪽에서 같은 문자열을 쓸 수 없다.
# 규칙: 메뉴 이름 = 화면 이름. 각 항목은 서로 다른 화면 하나를 가리킨다(같은 화면의 내부 앵커 금지).
CONSOLE_LINKS: tuple[tuple[str, str, str], ...] = (
    # 뺀 항목 — 되살리려면 그 줄을 다시 넣는다:
    #   ("signoff", "검증문서 검수 목록", "/console/admin.html#gold-jobs-card")
    #       같은 화면의 내부 앵커여서 뺐다. 검수 목록은 관리자 콘솔 「검증문서」 탭 첫 카드다
    #       (golden_jobs.js order:1).
    #   ("manage", "검증문서 후보 관리", "/api/v1/golden/candidates/login.html#candidates")
    #       로그인이 필요한 업무 화면이라 층위가 달랐다. 진입은 admin.html 「검증문서 현황」 카드의
    #       [후보 관리 화면 열기 ↗] 버튼이 맡는다.
    ("admin", "관리자 콘솔", "/console/admin.html"),
    # [D3 2026-08-18] 시연은 별도 화면이 아니라 분류 콘솔 안의 구역이 됐다.
    # parse_demo.html 은 그 구역으로 보내는 스텁으로만 남는다(인쇄된 주소 보호).
    ("demo", "등급 시연", "/console/index.html#sec-parse"),
    # 뺀 항목: ("login", "로그인", "/api/v1/golden/candidates/login.html")
    #   화면은 살아 있고 주소로 직접 열린다. 223 콘솔은 토큰이 프리필돼 시연 동선에 필요 없다.
)


def nav_links_html(current: str = "", *, css_class: str = "cnav-link") -> str:
    """화면 간 이동 링크 묶음. `current` 와 같은 키는 현재 화면 표시(링크 아님)."""
    out = []
    for key, label, href in CONSOLE_LINKS:
        safe = _html.escape(label)
        if key == current:
            out.append(f'<span class="{css_class} is-current" aria-current="page">{safe}</span>')
        else:
            out.append(f'<a class="{css_class}" href="{href}">{safe}</a>')
    return "".join(out)


NAV_CSS = (
    ".cnav{display:flex;gap:2px;align-items:center}"
    ".cnav-link{font-size:12.5px;color:#71717a;text-decoration:none;padding:4px 9px;"
    "border:1px solid transparent;border-radius:2px;white-space:nowrap}"
    ".cnav-link:hover{color:#111;border-color:#dededb}"
    ".cnav-link.is-current{color:#111;font-weight:700;border-color:#dededb;background:#fafafa}"
)


def nav_bar_html(current: str = "") -> str:
    """`<div class="cnav">…</div>` 한 덩어리. 헤더 안에 그대로 넣는다."""
    return f'<div class="cnav">{nav_links_html(current)}</div>'


# ── 상단 바 ───────────────────────────────────────────────────────────────────
# 갈라져 있던 5면의 상단을 검수·서명 화면 header.top 기준으로 합치고, 값을 여기 한 곳에 둔다.
# ⚠ 정적 화면(static/admin.html·index.html)은 header_html() 출력을 손으로 박아 넣는다.
#   어긋나면 tests/test_console_nav.py 가 잡는다. 고친 뒤 scripts/sync_console_header.py 재실행.

_LOGO_PATH = Path(__file__).with_name("api") / "static" / "koipa_logo_mark.png"


@lru_cache(maxsize=1)
def logo_data_uri() -> str:
    """로고 data URI. 파일이 없으면 빈 문자열 — 마크만 비고 나머지는 정상 렌더.

    왜 인라인인가: 폐쇄망이라 외부 이미지를 못 쓰고, 화면마다 상대경로 깊이가 달라
    (`/console/...` vs `/api/v1/golden/...`) 같은 `src` 문자열을 쓸 수 없다. 감리 증적으로
    HTML 한 장만 따로 저장될 때도 로고가 깨지면 안 된다(프로젝트 규칙: 로고 base64 인라인).
    """
    try:
        return "data:image/png;base64," + base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
    except OSError:
        return ""


_UPLOAD_PROGRESS_PATH = Path(__file__).with_name("api") / "static" / "upload_progress.js"


@lru_cache(maxsize=1)
def upload_progress_js() -> str:
    """업로드 진행 오버레이 소스. 파이썬이 렌더하는 화면에 그대로 인라인한다.

    링크(<script src>)로 걸지 않는 이유: 그 화면들은 /api/v1/... 아래라 정적 마운트와 경로
    깊이가 다르고, 콘솔 마운트가 꺼진 프로파일에서는 404 가 된다. 원본은 정적 파일 한 벌이다.
    """
    try:
        return _UPLOAD_PROGRESS_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


# 색을 변수(var(--line) 등)로 쓰지 않고 리터럴로 박는다 — 정적 2면과 동적 3면이 서로 다른
# 토큰 이름을 쓰고 있어, 변수로 두면 같은 CSS 가 화면마다 다른 색으로 나온다.
# 값은 골든 검수 화면이 실제로 렌더하던 것 그대로다(--line #e1e1de · --paper #fff ·
# --ink #111111 · --border-strong #cfcfcb).
#
# ⚠ 전부 `.top` 하위로 스코프한다. admin.html:27 과 styles.css:71 이 `.brand` 를
#   **flex 컨테이너**로 이미 정의해 놓았는데(우리 `.brand` 는 기관명 텍스트다) 스코프가
#   없으면 그쪽이 이겨 기관명이 가늘게 나온다(2026-08-20 실측).
HEADER_CSS = (
    ".top{height:84px;border-bottom:1px solid #e1e1de;display:flex;align-items:center;"
    "padding:0 34px;gap:18px;background:#fff;position:sticky;top:0;z-index:50}"
    ".top .mark{width:38px;height:38px;display:flex;align-items:center;justify-content:center;"
    "color:#1b4ea8}"
    ".top .mark img{width:100%;height:100%;object-fit:contain}"
    ".top .brand-mark{width:34px;height:34px;display:grid;place-items:center;flex-shrink:0;"
    "background:#fff;border-radius:7px;padding:4px;border:1px solid rgba(0,0,0,.08)}"
    ".top .brand{font-size:17px;font-weight:900;letter-spacing:1px;color:#111;"
    "display:inline;text-decoration:none;white-space:nowrap}"
    ".top .divider{height:23px;border-left:1px solid #cfcfcb}"
    ".top .product{font-size:14px;font-weight:800;letter-spacing:.8px;color:#71717a;"
    "white-space:nowrap}"
    ".top .spacer{flex:1}"
    # 후보 관리 화면의 건수 표시는 SHELL_CSS 에서 `margin:auto` 다. flex 에서 auto
    # 마진은 남는 공간을 스페이서보다 **먼저** 먹어서, 그대로 두면 메뉴가 가운데로
    # 밀린다. 상단 안에서만 0 으로 되돌린다.
    ".top .topmid{margin:0}"
    # 메뉴가 3→4 로 늘어 좁은 폭에서 기관명과 부딪힌다. 기관명·화면이름을 먼저 접는다 —
    # 메뉴는 마지막까지 남긴다(사용자 지시: 4개 메뉴가 최상단에 있어야 한다).
    # [2026-08-24] `.top .nav-link` 를 뺐다. 등급 시연 화면의 구역 이동 목차를 가리키던
    # 규칙인데, 그 목차 자체가 상단에서 빠졌다(sync_console_header.py, 사용자 지시).
    # 다섯 화면 어디에도 그 클래스를 쓰는 요소가 없어 죽은 선택자로만 남아 있었다
    # (실측 2026-08-24: static/*.html · golden.py 에 `class="nav-link"` 0건).
    # 목차를 되살리면 이 선택자도 같이 되살릴 것 — 좁은 폭에서 그것부터 접어야
    # 공용 메뉴 4개가 화면 밖으로 밀리지 않는다.
    "@media(max-width:900px){.top{padding:0 16px;gap:10px}"
    ".top .brand,.top .divider{display:none}}"
    "@media(max-width:640px){.top .product{display:none}}"
    "@media print{.top{display:none}}"
)

BRAND_NAME = "한국지식재산보호원"


def header_html(product: str, current: str = "", *, trailing: str = "") -> str:
    """화면 다섯 면이 공유하는 상단 바.

    `product` 는 화면 이름, `current` 는 CONSOLE_LINKS 의 키(현재 화면 표시),
    `trailing` 은 화면별 부속(상태 표시 등)이며 오른쪽 끝에 붙는다.

    [2026-08-24] **메뉴에 있는 화면이면 화면 이름을 적지 않는다.** 종전에는 왼쪽 `product`
    와 메뉴의 현재 항목이 **같은 글자를 두 번** 보여 줬다(관리자 콘솔·등급 시연). 메뉴가
    현재 화면을 진하게 표시하므로 왼쪽 이름은 중복이다. 메뉴에 없는 화면(검수·서명,
    로그인)만 이름을 적는다 — 그 화면들은 메뉴에 걸 수 없어(job_id·?t= 토큰) 자기 이름을
    스스로 밝혀야 한다.

    메뉴를 화면 이름 **바로 뒤**(왼쪽)에 두는 이유: 오른쪽 끝에 두면 부속 위젯에 밀려
    화면마다 위치가 달라진다. 왼쪽 고정이면 다섯 화면에서 눈이 같은 자리를 본다.
    """
    logo = logo_data_uri()
    mark = (
        f'<span class="mark"><span class="brand-mark">'
        f'<img src="{logo}" alt="{BRAND_NAME}"></span></span>'
        if logo
        else '<span class="mark"></span>'
    )
    in_menu = any(key == current for key, _, _ in CONSOLE_LINKS)
    name = (
        ""
        if (in_menu or not product)
        else f'<span class="divider"></span><span class="product">{_html.escape(product)}</span>'
    )
    return (
        '<header class="top">'
        + mark
        + f'<span class="brand">{BRAND_NAME}</span>'
        + name
        + nav_bar_html(current)
        + '<span class="spacer"></span>'
        + trailing
        + "</header>"
    )