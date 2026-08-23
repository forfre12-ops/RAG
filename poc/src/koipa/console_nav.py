"""콘솔 화면 간 이동 링크 — 한 곳에서 정한다.

왜(2026-08-17). 살아 있는 콘솔 화면이 **9면**인데 서로 오갈 방법이 거의 없었다.

    동적 5면 (golden.py html_router)
      /api/v1/golden/candidates/login.html          링크 0개 (location.href 로만 이동)
      /api/v1/golden/candidates/manage.html         nav 5개가 **전부 같은 페이지 앵커**(#overview 등)
      /api/v1/golden/candidates/actual-intake.html  헤더에 <a> 가 아예 없다
      /api/v1/golden/jobs/{job_id}/review.html      _nav_html 에 링크 없음
      /api/v1/golden/jobs/{job_id}/signoff.html     동상

    정적 4면 (/demo · /console StaticFiles)
      admin.html · index.html · parse_demo.html      자기들끼리는 링크가 있으나
      admin_preview.html                              골든 콘솔 5면으로는 0개 (역방향도 0개)

즉 검수자가 관리 화면에서 실문서 수집으로 가려면 **주소를 직접 쳐야** 했다.

⚠ review/signoff 는 **네비 대상이 될 수 없다.** job_id 가 필요하고, golden_html_url_secret
  이 설정돼 있으면 ?t= HMAC 토큰까지 있어야 열린다(golden.py:742-748·759-765). 고정 링크로
  걸면 403 이 난다. 그래서 그 두 화면에서는 **나가는 링크만** 둔다.

⚠ manage/actual-intake 는 포털 JWT 쿠키로 열린다(공유 API Key 거부 — golden.py:726·735).
  쿠키가 없는 상태에서 누르면 401 이 나는 것이 정상이다. 링크가 있다고 권한이 생기지 않는다.

정적 파일(admin/index/parse_demo)은 파이썬을 못 부르므로 같은 목록을 손으로 넣는다.
**여기 목록을 고치면 그 세 파일도 같이 고쳐야 한다** — 그것을 시험이 잠근다
(tests/test_console_nav.py).
"""
from __future__ import annotations

import base64
import html as _html
from functools import lru_cache
from pathlib import Path

# (키, 표시이름, 절대경로)
# 절대경로인 이유: 동적 화면(/api/v1/...)과 정적 화면(/console/...)의 깊이가 달라
# 상대경로로는 양쪽에서 같은 문자열을 쓸 수 없다.
# [2026-08-20] 사용자 지시로 4항목이 됐다 — 검수자가 오가는 화면 이름을 그대로 쓴다.
# 종전 3항목(후보 관리 / 거버넌스 / 분류 시연)은 화면 이름과 메뉴 이름이 달라 어느 메뉴가
# 어느 화면인지 눌러 봐야 알 수 있었다.
CONSOLE_LINKS: tuple[tuple[str, str, str], ...] = (
    # 검수 화면 자체(review/signoff)는 job_id 와 ?t= HMAC 토큰이 있어야 열려 고정 링크를
    # 걸 수 없다(golden.py:701·727 에서 403). 그래서 **잡 목록**으로 보낸다 — 거기서 잡을
    # 고르면 서버가 서명한 주소로 들어간다.
    # [2026-08-23] 이름을 실제 도착지에 맞췄다. 종전 「골든셋 검수」는 눌러도 검수를 할 수
    # 없었다 — 잡 목록으로 갈 뿐이고 진짜 검수는 거기서 잡을 골라 한 번 더 들어가야 한다.
    # 이름이 하는 일과 다르면 관리자는 "눌렀는데 왜 아무것도 없지"로 헤맨다.
    ("signoff", "검증문서 검수 목록", "/console/admin.html#gold-jobs-card"),
    # [D1 2026-08-17] '실문서 수집' 은 별도 화면이 아니라 이 화면의 업로드 모달이 됐다.
    # 두 화면이 같은 API(/golden/candidates/upload)·같은 필드를 쓰는데 화면만 둘이었다.
    ("manage", "검증문서 후보 관리", "/api/v1/golden/candidates/manage.html#candidates"),
    ("admin", "관리자 콘솔", "/console/admin.html"),
    # [D3 2026-08-18] 시연은 별도 화면이 아니라 분류 콘솔 안의 구역이 됐다.
    # parse_demo.html 은 그 구역으로 보내는 스텁으로만 남는다(인쇄된 주소 보호).
    ("demo", "등급 시연", "/console/index.html#sec-parse"),
    # [2026-08-21] 로그인을 **메뉴에** 둔다. 쿠키가 없는 브라우저(시크릿 창·새 노트북)로
    # 열면 화면은 200 으로 뜨는데 분석·등록·현황이 전부 401 이고, 어느 화면에도
    # login.html 주소가 없었다(2026-08-21 실측: index.html 안 login.html 0건).
    # 시연장에서 "왜 안 되지" 로 멈추는 자리라 한 번에 갈 수 있게 한다.
    ("login", "로그인", "/api/v1/golden/candidates/login.html"),
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
    ".cnav-link{font-size:12.5px;color:#70757a;text-decoration:none;padding:4px 9px;"
    "border:1px solid transparent;border-radius:2px;white-space:nowrap}"
    ".cnav-link:hover{color:#111;border-color:#dededb}"
    ".cnav-link.is-current{color:#111;font-weight:700;border-color:#dededb;background:#fafafa}"
)


def nav_bar_html(current: str = "") -> str:
    """`<div class="cnav">…</div>` 한 덩어리. 헤더 안에 그대로 넣는다."""
    return f'<div class="cnav">{nav_links_html(current)}</div>'


# ── 상단 바 ───────────────────────────────────────────────────────────────────
# [2026-08-20] 화면 5면의 상단이 세 갈래로 갈라져 있었다(실측):
#     header.top   골든셋 검수·서명 · 후보 관리 · 로그인   로고 3종(base64 PNG / 인라인 SVG)
#     nav.nav      거버넌스(admin.html) · 등급 시연(index.html)
# 기관명 옆 표기도 `.product` / `.brand-sub` / `.brand-url` 셋이었다. 사용자 지시로
# **검수·서명 화면의 header.top 을 기준**으로 합치고, 그 값을 여기 한 곳에 둔다.
#
# ⚠ 정적 화면(static/admin.html·index.html)은 파이썬을 못 부르므로 `header_html()` 의
#   출력을 **손으로 박아 넣는다.** 그 두 파일과 여기가 어긋나면 tests/test_console_nav.py
#   가 잡는다. 값을 고치면 `scripts/sync_console_header.py` 를 다시 돌릴 것.

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
    ".top .product{font-size:14px;font-weight:800;letter-spacing:.8px;color:#9da0a1;"
    "white-space:nowrap}"
    ".top .spacer{flex:1}"
    # 후보 관리 화면의 건수 표시는 SHELL_CSS 에서 `margin:auto` 다. flex 에서 auto
    # 마진은 남는 공간을 스페이서보다 **먼저** 먹어서, 그대로 두면 메뉴가 가운데로
    # 밀린다. 상단 안에서만 0 으로 되돌린다.
    ".top .topmid{margin:0}"
    # 메뉴가 3→4 로 늘어 좁은 폭에서 기관명과 부딪힌다. 기관명·화면이름을 먼저 접는다 —
    # 메뉴는 마지막까지 남긴다(사용자 지시: 4개 메뉴가 최상단에 있어야 한다).
    # `.nav-link` 는 등급 시연 화면의 구역 이동 목차다. 좁은 폭에서 이것부터 접어야
    # 공용 메뉴 4개가 화면 밖으로 밀리지 않는다(다른 화면에는 없으므로 무해하다).
    "@media(max-width:900px){.top{padding:0 16px;gap:10px}"
    ".top .brand,.top .divider,.top .nav-link{display:none}}"
    "@media(max-width:640px){.top .product{display:none}}"
    "@media print{.top{display:none}}"
)

BRAND_NAME = "한국지식재산보호원"


def header_html(product: str, current: str = "", *, trailing: str = "") -> str:
    """화면 5면이 공유하는 상단 바.

    `product` 는 기관명 옆 화면 이름, `current` 는 CONSOLE_LINKS 의 키(현재 화면 표시),
    `trailing` 은 화면별 부속(헬스 표시·배포 배지·건수 등)이며 오른쪽 끝에 붙는다.

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
    return (
        '<header class="top">'
        + mark
        + f'<span class="brand">{BRAND_NAME}</span>'
        + '<span class="divider"></span>'
        + f'<span class="product">{_html.escape(product)}</span>'
        + nav_bar_html(current)
        + '<span class="spacer"></span>'
        + trailing
        + "</header>"
    )
