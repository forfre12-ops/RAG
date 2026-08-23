"""서버가 렌더하는 콘솔 화면을 e2e 하니스가 띄울 수 있는 상태인지 본다.

`/api/v1/golden/candidates/manage.html`(검증문서 후보 관리)은 정적 파일이 아니라 파이썬이
문자열로 만들어 내려 주는 화면이다. node 하니스는 파이썬을 못 부르므로 렌더 결과를 파일로
떠서 서빙한다(scripts/dump_console_html.py).

낡은 판을 볼 위험은 **실행 경로에서** 없앴다 — pytest 는 임시 폴더에 그 자리에서 다시 떠서
하니스에 넘긴다(test_e2e_console_scenarios.py 의 rendered_dir 픽스처). 그래서 여기서는
byte 단위 대조를 하지 않는다. 렌더러를 손볼 때마다 남의 커밋을 빨갛게 만들 이유가 없다.

여기서 잠그는 것은 둘뿐이다.
  1. 렌더 함수가 실재하고 화면다운 것을 내놓는가 (이름이 바뀌면 하니스가 통째로 못 뜬다)
  2. 커밋된 편의본이 있는가 (`node run.mjs` 단독 실행이 그것을 쓴다)
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RENDERED = _ROOT / "tests" / "e2e_console" / "lib" / "rendered"

# (떠 둔 파일, 렌더 함수 이름) — scripts/dump_console_html.py 의 TARGETS 와 같아야 한다.
_TARGETS = [("manage.html", "_render_specledger_gold_console_html")]


@pytest.mark.parametrize(("filename", "fn_name"), _TARGETS)
def test_renderer_exists_and_produces_a_screen(filename: str, fn_name: str):
    """렌더 함수 이름이 바뀌거나 빈 문자열을 내면 하니스가 그 화면을 통째로 못 띄운다."""
    from koipa.api import golden as golden_api

    fn = getattr(golden_api, fn_name, None)
    assert callable(fn), f"렌더 함수가 없다: golden.{fn_name} (하니스가 {filename} 을 못 띄운다)"
    html = fn()
    assert isinstance(html, str) and len(html) > 5000, f"{filename}: 렌더 결과가 너무 짧다({len(html)})"
    assert "<script" in html and "</body>" in html, f"{filename}: 화면 골격이 아니다"


@pytest.mark.parametrize(("filename", "_fn"), _TARGETS)
def test_committed_copy_exists_for_standalone_runs(filename: str, _fn: str):
    """`node run.mjs` 단독 실행은 커밋된 편의본을 쓴다 — 없으면 그 화면 시나리오가 전부 죽는다."""
    path = _RENDERED / filename
    assert path.exists(), (
        f"떠 둔 화면이 없다: {path.relative_to(_ROOT)}\n"
        "make console-e2e-snapshot 을 돌려 만들고 커밋할 것."
    )
    assert path.stat().st_size > 5000, f"{filename}: 떠 둔 판이 비었거나 잘렸다"


def test_targets_match_the_dump_script():
    """이 시험과 뜨는 스크립트가 같은 목록을 봐야 한다 — 한쪽만 늘면 잠금이 뚫린다."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_dump_console_html", _ROOT / "scripts" / "dump_console_html.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert list(mod.TARGETS) == _TARGETS, (
        "scripts/dump_console_html.py 의 TARGETS 와 이 시험의 _TARGETS 가 어긋났다 "
        f"({mod.TARGETS} vs {_TARGETS})"
    )
