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

import html as _html

# (키, 표시이름, 절대경로)
# 절대경로인 이유: 동적 화면(/api/v1/...)과 정적 화면(/console/...)의 깊이가 달라
# 상대경로로는 양쪽에서 같은 문자열을 쓸 수 없다.
CONSOLE_LINKS: tuple[tuple[str, str, str], ...] = (
    ("manage", "후보 관리", "/api/v1/golden/candidates/manage.html"),
    # [D1 2026-08-17] '실문서 수집' 은 별도 화면이 아니라 후보 관리의 업로드 모달이 됐다.
    # 두 화면이 같은 API(/golden/candidates/upload)·같은 필드를 쓰는데 화면만 둘이었다.
    ("admin", "거버넌스", "/console/admin.html"),
    ("demo", "분류 시연", "/console/parse_demo.html"),
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
