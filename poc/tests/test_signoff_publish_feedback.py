"""미리보기로 제출했을 때 검수자가 다음에 무엇을 할지 안다.

왜(2026-08-17). 서명 화면의 [라이브 반영](publish)은 기본 해제다 — 서버 기본값과 맞춘
의도된 설계다. 그런데 체크하지 않고 제출하면 결과가 "미리보기(라이브 무변경)" 한 마디로
끝났다. 검수자가 **처음부터 다시 해야 하는지** 알 수 없다.

코드상 다시 할 필요가 없다. 제출 핸들러는 DEC(결정 상태)를 비우지 않고 render() 를 다시
부르지도 않는다 — 라디오 선택이 그대로 남는다. 체크만 하고 다시 제출하면 된다.
그 사실을 화면이 말해 준다.

⚠ 이 시험이 잠그는 것은 두 가지다: 안내 문구가 있는가, 그리고 **그 안내가 사실인가**
  (제출 뒤 상태를 지우는 코드가 들어오면 안내가 거짓말이 된다).
"""

from __future__ import annotations

from koipa.golden_review_html import render_signoff_html

_RECORDS = [{"doc_id": "d1", "label": "S2", "text": "본 문서는 내부 관리 기준을 정한다." * 3}]


def _html() -> str:
    return render_signoff_html(_RECORDS, job_id="J", post_url="/p")


def _submit_handler(html: str) -> str:
    """제출 버튼 핸들러 본문(요청 보내고 결과 그리는 구간)."""
    body = html.split("document.getElementById('submit').addEventListener")[1]
    return body.split("this.disabled=false")[0]


def test_preview_result_tells_the_reviewer_the_next_step():
    html = _html()
    assert "미리보기" in html
    assert "[라이브 반영]" in html and "다시 제출" in html


def test_preview_result_says_the_decisions_are_still_there():
    """이 한 줄이 없으면 검수자는 120건을 다시 누른다."""
    assert "결정은 그대로 남아 있습니다" in _html()


def test_submit_does_not_wipe_the_decisions():
    """안내가 사실이어야 한다 — 제출 뒤 상태를 지우면 위 문구가 거짓말이 된다."""
    h = _submit_handler(_html())
    assert "DEC={}" not in h and "DEC = {}" not in h
    assert "render()" not in h, "다시 그리면 라디오 선택이 지워질 수 있다"


def test_publish_requested_but_not_applied_is_not_reported_as_success():
    """publish=true 인데 반영이 안 됐으면 조용히 성공처럼 보이면 안 된다(기존 보장 유지)."""
    html = _html()
    assert "라이브 반영 요청됨" in html and "미반영" in html


def test_publish_checkbox_still_defaults_to_unchecked():
    """서버 기본값(GoldenSignoffRequest.publish=False)과 맞춘 것 — 체크 상태로 바꾸지 않는다."""
    html = _html()
    i = html.index('id="publish"')
    tag = html[html.rindex("<input", 0, i):html.index(">", i) + 1]
    assert "checked" not in tag
