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


@pytest.mark.parametrize("bp", ["max-width:1050px", "max-width:700px"])
def test_both_screens_collapse_at_the_same_breakpoints(signoff, manage, bp):
    """반응형이 한쪽에만 있으면 좁은 화면에서 두 화면이 다시 갈라진다.

    2026-08-19 실측: 껍데기를 공용 모듈로 뽑을 때 @media 를 빠뜨려, manage 는 1050px 에서
    사이드바가 접히는데 검수 화면은 326px 사이드바를 그대로 둔 채 본문만 눌렸다.
    """
    for name, html in (("검수 화면", signoff), ("후보 관리", manage)):
        assert bp in html.replace(" ", ""), f"{name} 에 {bp} 중단점이 없다"


def test_media_rules_come_after_the_base_rules_they_override(manage):
    """반응형은 스타일시트 **맨 뒤**여야 한다 — 앞에 두면 뒤의 기본 규칙이 덮는다.

    실측으로 확인한 두 곳: `.rowhead{display:none}` 은 뒤의 `.rowhead,.candidate{display:grid}`
    에, `.filters input{width:100%}` 는 뒤의 `.filters input{width:170px}` 에 진다.
    """
    css = manage[manage.index("<style>"):manage.index("</style>")]
    media_at = css.index("@media(max-width:700px)")
    for base in (".rowhead,.candidate{", ".filters input{width:170px}"):
        assert css.index(base) < media_at, f"{base} 가 미디어 규칙보다 뒤에 있다"


# ── 접근성 (2026-08-19) ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("attr", ['aria-pressed', 'aria-live="polite"', "aria-label"])
def test_both_screens_carry_the_basic_a11y_hooks(signoff, manage, attr):
    """키보드·보조기기로 쓰는 사람이 화면 상태를 알 수 있어야 한다.

    2026-08-19 이전에는 검수 화면에 aria-* 가 0개였다 — 보기 전환 버튼의 눌림 상태도,
    서명 결과 배너가 떴다는 사실도 소리로 전달되지 않았다.
    """
    for name, html in (("검수 화면", signoff), ("후보 관리", manage)):
        assert attr in html, f"{name} 에 {attr} 가 없다"


def test_view_toggle_reports_pressed_state(signoff, manage):
    """「읽기 좋게 / 원문 그대로」는 토글이다 — 어느 쪽이 켜졌는지 알려야 한다."""
    assert 'aria-pressed="true"' in signoff and 'aria-pressed="false"' in signoff
    assert "setAttribute('aria-pressed'" in signoff, "토글할 때 갱신하지 않는다"
    assert 'aria-pressed="true"' in manage and 'aria-pressed="false"' in manage
    assert "setAttribute('aria-pressed'" in manage


def test_keyboard_focus_is_visible(signoff, manage):
    """초점 표시가 없으면 키보드로 다닐 때 어디 있는지 안 보인다."""
    for name, html in (("검수 화면", signoff), ("후보 관리", manage)):
        assert ":focus-visible" in html, f"{name} 에 초점 표시가 없다"


def test_dropzone_counts_enter_and_leave(manage):
    """dragleave 는 자식 요소로 옮겨갈 때도 난다 — 세지 않으면 강조가 깜빡인다."""
    assert "if(--over<=0)" in manage, "드롭존이 enter/leave 를 세지 않는다"
