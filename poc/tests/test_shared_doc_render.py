"""검수 화면도 후보 관리 화면과 **같은 렌더러**로 문서를 그린다.

왜(사용자 지적 2026-08-18). 후보 관리에만 '읽기 좋게 보기' 가 있었고 검수·서명 화면은
원문을 그대로 흘려 놓아 읽기 어려웠다. 같은 문서를 보는 두 화면인데 한쪽만 읽을 만했다.

렌더러(mdToHtml)는 한국어 실문서를 전제로 만들어졌다 — 그래서 복사하지 않고 공용 모듈로
뽑았다. 복사해 두면 한쪽만 고쳐져 같은 문서가 다르게 보인다.

⚠ 원문이 정본이다. 렌더링은 서식만 입히고 내용을 바꾸지 않으며, 화면에 그 사실을 적는다.
"""

from __future__ import annotations

import pytest

from koipa.console_doc import DOC_CSS, DOC_RENDER_JS
from koipa.golden_review_html import render_signoff_html

_TEXT = "【주문】 원고의 청구를 기각한다. 【이유】 피고가 관리한 정보는 비밀로 유지되었다."


@pytest.fixture(scope="module")
def signoff() -> str:
    return render_signoff_html(
        [{"doc_id": "a", "label": "S2", "text": _TEXT}],
        job_id="J", post_url="/p", min_per_grade=5,
        pending=[{"doc_id": "b", "label": "TS", "text": _TEXT}])


def test_renderer_lives_in_one_place():
    assert "function mdToHtml" in DOC_RENDER_JS
    assert ".docbody.md" in DOC_CSS


def test_signoff_pulls_the_shared_renderer_not_a_copy():
    import inspect

    from koipa import golden_review_html
    src = inspect.getsource(golden_review_html)
    assert "from koipa.console_doc import DOC_CSS, DOC_RENDER_JS" in src
    assert "function mdToHtml" not in src, "렌더러를 복사해 두면 두 화면이 갈라진다"


def test_manage_and_signoff_use_the_same_function(signoff):
    from koipa.api.golden import _render_specledger_gold_console_html
    manage = _render_specledger_gold_console_html()
    assert "function mdToHtml" in manage
    assert "function mdToHtml" in signoff


def test_both_card_kinds_are_rendered(signoff):
    """서명 카드와 보기 전용 카드 둘 다 — 한쪽만 하면 목록 안에서 모양이 갈린다."""
    assert signoff.count("mdToHtml(r.text)") == 2


def test_raw_view_is_available_and_named_as_the_source_of_truth(signoff):
    assert "읽기 좋게" in signoff and "원문 그대로" in signoff
    assert "판단 근거는 원문 기준입니다" in signoff


def test_placeholder_is_substituted(signoff):
    """치환이 빠지면 화면에 자리표시자가 그대로 뜬다."""
    assert "__DOC_RENDER_JS__" not in signoff


def test_view_toggle_is_wired_per_document(signoff):
    """카드가 여럿이라 전환이 문서 단위여야 한다 — 전역이면 다른 카드까지 바뀐다."""
    assert "data-id=" in signoff and "CSS.escape(id)" in signoff
    assert "data-doc" in signoff
