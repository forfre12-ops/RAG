"""정적 콘솔 화면(admin.html·index.html)의 상단 바를 console_nav 값으로 다시 박는다.

왜(2026-08-20). 콘솔 5면의 상단이 세 갈래로 갈라져 있었다 — 동적 3면은 `header.top`,
정적 2면은 `nav.nav` 였고, 로고는 base64 PNG·인라인 SVG 근사본·외부 PNG 파일 셋이었다.
사용자 지시로 **골든셋 검수·서명 화면의 header.top 을 기준**으로 합쳤다.

정적 HTML 은 파이썬을 못 부르므로 같은 마크업을 파일에 박아 넣어야 한다. 손으로 옮기면
다시 갈라지므로 이 스크립트가 대신 넣는다. 넣는 자리는 주석 표식 사이다:

    <!-- console-header:start ... -->   …마크업…   <!-- console-header:end -->
    /* console-header:css:start */      …CSS…      /* console-header:css:end */

표식이 없으면(최초 1회) 종전 `nav.nav` 블록을 찾아 표식째 갈아 끼운다.

    python scripts/sync_console_header.py          # 다시 박는다
    python scripts/sync_console_header.py --check  # 어긋나면 1 로 종료(시험·CI 용)

⚠ 이 스크립트는 **화면별 부속**(admin 의 헬스 표시, index 의 준비상태·구역 이동 링크)을
  헤더 오른쪽에 그대로 유지한다. 그 요소들은 JS 가 id 로 잡고 있어 없어지면 화면이 죽는다
  (admin.html 의 #health/#health-txt → checkHealth, index 의 #nav-status → app.js).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.console_nav import HEADER_CSS, NAV_CSS, header_html  # noqa: E402

STATIC = _SRC / "koipa" / "api" / "static"

MARK_START = "<!-- console-header:start — koipa/console_nav.py 가 만든다. 손으로 고치지 말 것 (scripts/sync_console_header.py) -->"
MARK_END = "<!-- console-header:end -->"
CSS_START = "/* console-header:css:start — koipa/console_nav.py HEADER_CSS. 손으로 고치지 말 것 */"
CSS_END = "/* console-header:css:end */"

# 화면별 부속 — 헤더 오른쪽 끝. JS 가 id 로 잡는 것은 반드시 남긴다.
TRAILING = {
    # title 은 종전에 "헬스체크 — 클릭" 이었으나 onclick 도 리스너도 없다(실측). 실제로
    # 누르는 곳은 본문의 [헬스체크 실행] 버튼이라, 사실대로 고쳐 둔다.
    "admin.html": (
        '<span id="health" class="health-pill" title="서버 연결 상태">'
        '<span class="dot"></span>'
        '<span id="health-txt" role="status" aria-live="polite">연결 확인</span>'
        "</span>"
    ),
    # 구역 이동 링크는 이 화면 안에서만 쓰는 목차다 — 공용 메뉴 4개 뒤에 둔다.
    # href="#sec-parse" 는 tests/test_demo_static_assets.py 가 존재를 잠근다.
    # [2026-08-24] 파싱·분류 시연을 이 화면의 입력·결과로 합치면서 앵커가 「파일 직접 업로드」
    # 자리로 옮겨갔다 — 메뉴 이름도 그 자리 이름으로 바꾼다(옛 이름은 없는 구역을 가리켰다).
    "index.html": (
        '<span id="nav-status" class="nav-status warming">'
        '<span class="dot"></span><span>warmup…</span></span>'
        '<a class="nav-link" href="#s1">시연</a>'
        '<a class="nav-link" href="#sec-ops">운영·반영</a>'
        '<a class="nav-link" href="#s3">법령</a>'
        '<a class="nav-link" href="#sec-parse">문서 업로드</a>'
    ),
}

PRODUCT = {"admin.html": "관리자 콘솔", "index.html": "등급 시연"}
CURRENT = {"admin.html": "admin", "index.html": "demo"}

# CSS 를 넣을 곳. admin 은 자체 인라인 <style>, index 는 외부 styles.css 를 쓴다.
CSS_TARGET = {"admin.html": "admin.html", "index.html": "styles.css"}

# 최초 1회 교체용 — 표식이 아직 없을 때 걷어낼 종전 상단 블록.
_OLD_HEADER = re.compile(
    r"(?:<!--\s*=+\s*NAVBAR\s*=+\s*-->\s*)?<(nav|header)\s+class=\"nav\">.*?</\1>",
    re.S,
)


def _header_block(page: str) -> str:
    body = header_html(PRODUCT[page], CURRENT[page], trailing=TRAILING[page])
    # console_nav.py 와 같은 목록임을 시험이 문자열로 확인한다(test_console_nav.py).
    note = "<!-- console-nav: koipa/console_nav.py 와 같은 목록. 한쪽만 고치면 시험이 잡는다 -->"
    return f"{MARK_START}\n{note}\n{body}\n{MARK_END}"


def _css_block() -> str:
    return f"{CSS_START}\n{HEADER_CSS}\n{NAV_CSS}\n{CSS_END}"


def _put(text: str, start: str, end: str, block: str, *, fallback) -> str:
    """표식 사이를 갈아 끼운다. 표식이 없으면 fallback 으로 자리를 잡는다."""
    if start in text and end in text:
        head, rest = text.split(start, 1)
        _, tail = rest.split(end, 1)
        return head + block + tail
    return fallback(text, block)


def _install_markup(page: str, text: str) -> str:
    def fallback(t: str, block: str) -> str:
        m = _OLD_HEADER.search(t)
        if not m:
            raise SystemExit(f"{page}: 종전 상단 블록을 못 찾았다 — 표식을 손으로 넣을 것")
        return t[: m.start()] + block + t[m.end() :]

    return _put(text, MARK_START, MARK_END, _header_block(page), fallback=fallback)


def _install_css(text: str) -> str:
    def fallback(t: str, block: str) -> str:
        if "</style>" in t:  # 인라인 <style> 을 쓰는 화면
            i = t.rindex("</style>")
            return t[:i] + "\n" + block + "\n" + t[i:]
        return t.rstrip() + "\n\n" + block + "\n"  # 외부 스타일시트

    return _put(text, CSS_START, CSS_END, _css_block(), fallback=fallback)


def sync(*, check: bool) -> int:
    bad = 0
    for page in ("admin.html", "index.html"):
        targets: dict[Path, str] = {}
        src = STATIC / page
        targets[src] = _install_markup(page, src.read_text(encoding="utf-8"))
        css_file = STATIC / CSS_TARGET[page]
        base = targets.get(css_file, (css_file.read_text(encoding="utf-8")))
        targets[css_file] = _install_css(base)

        for path, new in targets.items():
            old = path.read_text(encoding="utf-8")
            if old == new:
                continue
            if check:
                print(f"어긋남: {path}")
                bad += 1
            else:
                path.write_text(new, encoding="utf-8", newline="")
                print(f"고침: {path}")
    if check and not bad:
        print("정적 2면 상단이 console_nav 와 일치한다")
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="고치지 않고 어긋남만 알린다")
    raise SystemExit(sync(check=ap.parse_args().check))
