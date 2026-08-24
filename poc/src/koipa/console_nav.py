"""콘솔 화면 간 이동 링크 — 한 곳에서 정한다.

왜(2026-08-17). 화면끼리 오갈 방법이 거의 없어 검수자가 주소를 직접 쳐야 했다.

살아 있는 화면 (2026-08-24 실측 6면):

    동적 4면 (golden.py html_router)
      /api/v1/golden/candidates/login.html
      /api/v1/golden/candidates/manage.html
      /api/v1/golden/jobs/{job_id}/review.html      signoff 와 같은 화면을 준다
      /api/v1/golden/jobs/{job_id}/signoff.html

    정적 2면 (/demo · /console StaticFiles)
      admin.html · index.html
      (parse_demo.html 은 index.html#sec-parse 로 보내는 리다이렉트 스텁이다)

⚠ review/signoff 는 **네비 대상이 될 수 없다.** job_id 가 필요하고, golden_html_url_secret
  이 설정돼 있으면 ?t= HMAC 토큰까지 있어야 열린다(golden.py:742-748·759-765). 고정 링크로
  걸면 403 이 난다. 그래서 그 두 화면에서는 **나가는 링크만** 둔다.

⚠ manage.html 은 포털 JWT 쿠키로 열린다(공유 API Key 거부).
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
# [2026-08-24] 그중 한 항목을 뺐다(4 → 3, 사유는 바로 아래). 8/20 의 원칙("메뉴 이름 =
# 화면 이름")은 그대로다 — 뺀 항목이 그 원칙을 지키지 못하는 유일한 항목이었다.
# [2026-08-24 두 번째] 하나 더 뺐다(3 → 2, 사유는 manage 자리 주석). 남은 둘은 **화면의
# 종류**로 갈린다 — 운영자가 일하는 곳(관리자 콘솔) · 제품을 보여주는 곳(등급 시연).
CONSOLE_LINKS: tuple[tuple[str, str, str], ...] = (
    # [2026-08-24] 「검증문서 검수 목록」을 뺐다(4항목 → 3항목). 사용자 지적:
    # "골든셋 후보관리, 골든셋 검수는 메뉴를 하나로 빼야하는거 아니야? 여기저기 들어가있으니
    # 찾기도 힘드네."
    #
    # 이 메뉴는 **화면 사이 이동**이다(그래서 화면 이름과 같은 이름을 쓴다 — 61f1a94f).
    # 그런데 이 항목만 목적지가 `/console/admin.html#gold-jobs-card` 로, 다른 화면이 아니라
    # 「관리자 콘솔」과 **같은 화면의 내부 앵커**였다. 메뉴 4개 중 2개가 같은 화면을 가리키니
    # 관리자는 검수 목록을 찾을 때마다 둘 중 어느 것을 눌러야 하는지 판단해야 했다.
    #
    # 이름이 하는 일과 다른 문제는 8/23 에 이미 손봤다 — 종전 「골든셋 검수」는 눌러도 검수를
    # 할 수 없었고(잡 목록으로 갈 뿐), 그래서 「검증문서 검수 목록」으로 고쳤다. 그때 고친 것은
    # 이름이고, 전역 메뉴에 화면 내부 이동이 들어와 있는 구조는 그대로였다. 이제 그 구조를
    # 없앤다 — 검수 목록은 관리자 콘솔 「검증문서」 탭의 **첫 카드**다(golden_jobs.js order:1).
    #
    # 남은 3항목은 각각 서로 다른 화면 하나를 가리킨다(manage=포털 화면 · admin · demo).
    # 검수·서명 화면(review/signoff)은 여전히 메뉴에 걸 수 없다 — job_id 와 ?t= HMAC 토큰이
    # 있어야 열리고 고정 링크는 403 이다(golden.py:750·780). 되살릴 때는 이 줄을 다시 넣되,
    # 그때는 같은 화면을 두 번 가리키는 문제를 어떻게 풀지 함께 정할 것:
    #     ("signoff", "검증문서 검수 목록", "/console/admin.html#gold-jobs-card"),
    #
    # [D1 2026-08-17] '실문서 수집' 은 별도 화면이 아니라 이 화면의 업로드 모달이 됐다.
    # 두 화면이 같은 API(/golden/candidates/upload)·같은 필드를 쓰는데 화면만 둘이었다.
    # [2026-08-24] 목적지를 login.html 로 바꿨다. manage.html 을 쿠키 없이 열면 401 JSON 한
    # 줄이라 되돌아갈 길이 없었고, 관리자 콘솔 카드는 이미 login.html 을 가리켜 진입점이
    # 두 갈래였다. login.html 은 세션이 살아 있으면 앵커까지 들고 그대로 통과한다.
    # [2026-08-24] 「검증문서 후보 관리」를 뺐다(3항목 → 2항목, 사용자 판단).
    #
    # 셋 중 이 항목만 성격이 달랐다. 두 가지다:
    #   ① **로그인을 거쳐야 하는 화면이다.** 포털 JWT 쿠키를 요구해 세션이 없으면 로그인
    #      화면이 뜬다. 나머지 둘은 그냥 열린다. 같은 줄에 있으면서 누를 때 일어나는 일이
    #      다르면 관리자는 "메뉴를 눌렀는데 로그인?"으로 읽는다.
    #   ② **특정 업무 화면이다.** 남은 둘은 "운영자가 일하는 곳 / 제품을 보여주는 곳"이라
    #      화면의 종류로 갈리는데, 이 항목만 그 층위 아래에 있었다.
    #
    # 길이 끊기지 않는다 — 진입 버튼이 **쓸 자리에 이미 있다**:
    #     static/admin.html 「검증문서 현황」 카드 안 [후보 관리 화면 열기 ↗]
    #     ("후보를 개별로 열어 등급을 지정하거나, 실문서를 새로 넣으려면 아래 화면을 씁니다")
    # 돌아오는 길도 그대로다 — 후보 관리·로그인 화면의 상단 바는 이 목록으로 그려지므로
    # 거기서 「관리자 콘솔」이 보인다. 메뉴에서 빠지는 것은 **그리로 가는 길**뿐이고 그건
    # 위 버튼이 맡는다.
    #
    # 되살릴 때는 이 줄을 다시 넣으면 된다:
    #     ("manage", "검증문서 후보 관리", "/api/v1/golden/candidates/login.html#candidates"),
    ("admin", "관리자 콘솔", "/console/admin.html"),
    # [D3 2026-08-18] 시연은 별도 화면이 아니라 분류 콘솔 안의 구역이 됐다.
    # parse_demo.html 은 그 구역으로 보내는 스텁으로만 남는다(인쇄된 주소 보호).
    ("demo", "등급 시연", "/console/index.html#sec-parse"),
    # [2026-08-24] 로그인을 메뉴에서 뺐다(사용자 지시). 8/21 에 넣었던 이유는 "쿠키 없는
    # 브라우저로 열면 화면은 뜨는데 전부 401 인데 login.html 주소가 어디에도 없다" 였다.
    # 그 사정은 그대로다 — 화면(login.html)은 살아 있고 주소로 직접 열린다. 다만 223 콘솔은
    # 토큰이 프리필돼 있어 시연 동선에서 이 메뉴를 쓸 일이 없고, 메뉴에 있으면 관리자가
    # "로그인부터 해야 하나" 로 읽는다. 되살릴 때는 이 줄을 다시 넣으면 된다:
    #     ("login", "로그인", "/api/v1/golden/candidates/login.html"),
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