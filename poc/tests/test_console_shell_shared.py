"""후보 관리와 검수·서명이 **같은 껍데기**를 쓴다.

왜(사용자 지적 2026-08-18). 색·글꼴·헤더를 맞춘 뒤에도 두 화면이 "너무 이질감" 이었다.
실제로 비교해 보니 머리만 같고 그 아래가 딴 화면이었다.

    manage    header.top -> frame -> [side | main -> hero + summary + section + list]
    signoff   header.top -> signbar -> container (필터 + 카드)

골격 CSS 를 console_shell.SHELL_CSS 한 곳으로 뽑고 둘이 함께 쓰게 했다.
한쪽에서만 고치면 다시 어긋나므로 이 시험이 그것을 잠근다.
"""

from __future__ import annotations

import pytest

from koipa.api.golden import _render_specledger_gold_console_html
from koipa.console_shell import SHELL_CSS
from koipa.golden_review_html import render_signoff_html

_RECORDS = [{"doc_id": "a", "label": "S2", "text": "본 문서는 내부 관리 기준을 정한다." * 3}]


@pytest.fixture(scope="module")
def signoff() -> str:
    return render_signoff_html(_RECORDS, job_id="J", post_url="/p", min_per_grade=5)


@pytest.fixture(scope="module")
def manage() -> str:
    return _render_specledger_gold_console_html()


@pytest.mark.parametrize("rule", [".frame{", ".side{", ".main{", ".hero{", ".section{",
                                  ".sectionTop{", ".secNum{", ".list{", ".btn{", ".top{"])
def test_shell_holds_the_layout_rules(rule):
    assert rule in SHELL_CSS, f"{rule} 이 공용 껍데기에 없다"


@pytest.mark.parametrize("marker", ['class="frame"', 'class="side"', 'class="main"',
                                     'class="hero"', 'class="section"'])
def test_both_screens_use_the_same_skeleton(signoff, manage, marker):
    """한쪽에만 있으면 검수자가 오갈 때 다른 시스템처럼 보인다."""
    assert marker in signoff, f"검수 화면에 {marker} 가 없다"
    assert marker in manage, f"후보 관리 화면에 {marker} 가 없다"


def test_signoff_pulls_the_shell_not_its_own_copy():
    """복사해 두면 갈라진다 — 모듈에서 가져오는지 확인한다."""
    import inspect

    from koipa import golden_review_html
    src = inspect.getsource(golden_review_html)
    assert "from koipa.console_shell import SHELL_CSS" in src
    assert "_TOKENS + SHELL_CSS" in src


def test_old_standalone_layout_is_gone(signoff):
    """옛 골격이 남아 있으면 두 벌이 겹쳐 배치가 어긋난다."""
    assert 'class="signbar"' not in signoff
    assert 'class="lede-row"' not in signoff


def test_signoff_keeps_its_working_parts(signoff):
    """껍데기를 바꾸다 기능을 흘리면 안 된다 — 오늘 넣은 것들이 그대로인지."""
    for k in ('id="who"', 'id="deccount"', 'id="submit"', 'id="publish"',
              'id="preflight"', 'id="restored"', 'id="grid"', "koipa.signoff."):
        assert k in signoff, k


def test_markup_is_balanced(signoff):
    """골격을 손대면 닫는 태그가 어긋나기 쉽다 — 레이아웃이 통째로 무너진다."""
    assert signoff.count("<div") == signoff.count("</div>")
    assert signoff.count("<section") == signoff.count("</section>")
    assert signoff.count("<aside") == signoff.count("</aside>")
@pytest.mark.parametrize("eid", ["preflight", "restored", "result", "deccount", "grid", "submit"])
def test_ids_are_unique(signoff, eid):
    """같은 id 가 둘이면 getElementById 가 첫 것만 잡아 뒤엣것은 죽은 마크업이 된다.

    2026-08-18: 껍데기를 갈면서 새 자리에 넣고 옛 자리를 안 지워 네 쌍이 겹쳤다.
    deccount 는 그 탓에 gate 의 큰 글씨(20px/900)로 등급별 내역이 쏟아지고 있었다.
    """
    marker = 'id="%s"' % eid
    assert signoff.count(marker) == 1, "%s 가 %d회 나온다" % (marker, signoff.count(marker))
