"""정적 콘솔 화면(index·admin·parse_demo)이 문법·참조 면에서 성립하는지 본다.

왜(2026-08-19). 검수·서명 화면은 node 로 실행해 보는 시험을 갖췄는데(test_signoff_screen_runs)
정적 콘솔 2,505줄·551줄은 문자열 검사만 있었다. 2026-08-18 에 화면이 통째로 죽은 것을
시험 423건이 못 잡은 것과 같은 구조가 그대로 남아 있었다.

여기서 잡는 것 둘:
  1) 인라인 <script> 가 **문법적으로 성립**하는가 (node --check)
  2) onclick= 같은 인라인 핸들러가 부르는 함수가 **실제로 정의돼 있는가**
     — 없으면 그 버튼은 눌러도 ReferenceError 만 난다. 화면은 멀쩡해 보인다.

⚠ 여기까지가 브라우저 없이 잡을 수 있는 범위다. 레이아웃·실제 클릭 동작은 못 본다.
⚠ type="module" 스크립트는 전역을 만들지 않으므로 정의 목록에서 뺀다 — 인라인 핸들러가
  모듈 안 함수를 부르면 실제로는 동작하지 않기 때문이다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "koipa" / "api" / "static"
PAGES = ["index.html", "admin.html", "parse_demo.html"]

_HANDLER_CALL = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(")
_INLINE = r'<script(?![^>]*type="module")(?![^>]* src=)[^>]*>(.*?)</script>'
_EXTERNAL = r'<script(?![^>]*type="module")[^>]* src="([^"]+)"'


def _no_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


def _inline_js(html: str) -> str:
    """모듈이 아닌 인라인 <script> 만. 모듈은 전역을 만들지 않는다."""
    return "\n;\n".join(re.findall(_INLINE, html, re.S))


def _global_js(html: str, base: Path) -> str:
    """인라인 + 모듈이 아닌 외부 스크립트 — 이 페이지에서 전역이 되는 코드 전부."""
    js = _inline_js(html)
    for src in re.findall(_EXTERNAL, html):
        p = base / src.lstrip("./")
        if p.exists():
            js += "\n;\n" + p.read_text(encoding="utf-8")
    return js


def _handler_names(html: str) -> set[str]:
    names: set[str] = set()
    for h in re.findall(r'\son[a-z]+="([^"]*)"', html):
        names.update(_HANDLER_CALL.findall(h))
    return names


def _missing(names: set[str], js: str) -> list[str]:
    flat = js.replace(" ", "")
    return sorted(n for n in names
                  if f"function {n}(" not in js
                  and f"function{n}(" not in flat
                  and f"{n}=function" not in flat
                  and f"window.{n}=" not in flat)


@pytest.mark.parametrize("page", PAGES)
def test_inline_script_parses(page, tmp_path):
    """문법이 깨지면 <script> 블록이 통째로 실행되지 않는다 — 화면이 조용히 죽는다."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 문법 검사를 건너뛴다")
    js = _inline_js(_no_comments((STATIC / page).read_text(encoding="utf-8")))
    if not js.strip():
        pytest.skip(f"{page} 에 인라인 스크립트가 없다")
    f = tmp_path / "page.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, f"{page} 인라인 JS 문법 오류:\n{r.stderr}"


@pytest.mark.parametrize("page", PAGES)
def test_every_inline_handler_resolves(page):
    """onclick="foo()" 인데 foo 가 없으면 그 버튼은 눌러도 아무 일이 안 난다.

    화면은 멀쩡해 보이므로 사람이 직접 눌러 보기 전까지 아무도 모른다.
    2026-08-19 기준 admin 48개 · index 7개 · parse_demo 0개가 전부 정의를 찾는다.
    """
    html = _no_comments((STATIC / page).read_text(encoding="utf-8"))
    missing = _missing(_handler_names(html), _global_js(html, STATIC))
    assert not missing, f"{page} 의 인라인 핸들러가 정의를 못 찾는다: {missing}"


def test_the_check_would_catch_a_missing_handler():
    """검사가 정말 잡는지 — 없는 이름을 넣어 확인한다(실제 파일은 건드리지 않는다)."""
    html = '<button onclick="definitelyNotDefined()">x</button><script>function a(){}</script>'
    assert _missing(_handler_names(html), _global_js(html, STATIC)) == ["definitelyNotDefined"]
