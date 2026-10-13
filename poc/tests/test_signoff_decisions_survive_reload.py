"""검수 중간 결정이 탭을 닫아도 남는다.

왜(2026-08-17). 결정 상태(DEC)는 브라우저 메모리에만 있었다. 전달본은 120건이고 한 번에
끝나지 않는다 — 60건 하다 창을 닫으면 **60건을 다시 눌러야 했다.**

확인한 것(오해 정정): 필터 전환은 안전하다. render() 가 grid 를 다시 그리지만 card() 가
DEC 를 읽어 checked·등급·메모를 복원한다. 잃는 것은 **새로고침·탭 닫기**뿐이다.

그래서 잡 단위로 브라우저에 남긴다. 서버로 보내지 않는다 — 제출 전 결정은 아직 서명이
아니고, 서버에 반쯤 된 검수를 남기면 '누가 서명했나' 가 흐려진다.

⚠ 저장이 실패해도(사생활 보호 모드 등) 검수는 계속돼야 한다 — 예외를 삼킨다.
"""

from __future__ import annotations

from koipa.golden_review_html import render_signoff_html

_RECORDS = [{"doc_id": "d1", "label": "S2", "text": "본 문서는 내부 관리 기준을 정한다." * 3}]


def _html(post_url: str = "/api/v1/golden/jobs/J/signoff") -> str:
    return render_signoff_html(_RECORDS, job_id="J", post_url=post_url)


def test_decisions_are_stored_per_job():
    """잡마다 따로 남아야 한다 — 다른 회차의 결정이 섞이면 안 된다."""
    html = _html()
    assert "const DEC_KEY='koipa.signoff.'+POST_URL;" in html


def test_every_kind_of_decision_change_is_saved():
    """등급·거부·승인만 남기고 메모를 빠뜨리면 메모만 사라진다."""
    html = _html()
    assert html.count("decSave()") >= 4, "저장 호출이 빠진 변경 경로가 있다"


def test_restored_decisions_are_announced_not_silent():
    """조용히 복원하면 검수자가 '내가 한 것인지' 확신할 수 없다."""
    html = _html()
    assert 'id="restored"' in html
    assert "복원했습니다" in html
    assert "지우고 새로 시작" in html, "복원이 원치 않는 경우 빠져나갈 길이 있어야 한다"


def test_storage_failure_does_not_break_the_screen():
    """사생활 보호 모드에서 localStorage 가 던진다 — 거기서 화면이 죽으면 검수가 막힌다."""
    html = _html()
    seg = html[html.index("function decSave()"):html.index("function decClear()")]
    assert seg.count("catch") >= 2, "저장·복원 양쪽에 예외 처리가 있어야 한다"


def test_only_real_decisions_are_restored():
    """메모만 있고 결정이 없는 항목까지 세면 '복원 N건' 이 부풀려진다."""
    html = _html()
    assert "if(o[k]&&o[k].decision)" in html


def test_partial_review_is_not_sent_to_the_server():
    """제출 전 결정은 아직 서명이 아니다 — 서버에 반쯤 된 검수를 남기지 않는다."""
    html = _html()
    seg = html[html.index("function decSave()"):html.index("function decClear()")]
    assert "fetch(" not in seg


def test_filter_switch_still_repaints_from_the_decision_state():
    """이미 있던 보장 — card() 가 DEC 를 읽어야 필터를 눌러도 선택이 남는다."""
    html = _html()
    card = html[html.index("function card(r)"):html.index("function decCount()")]
    assert "const d=DEC[r.id]||{};" in card
    assert card.count("checked") >= 3
    assert "d.note" in card, "메모도 다시 그려야 한다"
