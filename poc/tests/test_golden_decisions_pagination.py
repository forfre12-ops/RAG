"""recent_decisions() 의 offset·action — 보류·폐기 이력 화면 페이징(2026-10-10, 사용자 지적).

왜. 관리자 콘솔이 결정 원장을 limit=300 고정으로 한 번에 받아왔다 — 300건을 넘기면 그
이전 기록은 화면에서 영영 못 본다(더 보기 수단이 없었다). offset 을 추가해 페이지를 넘길
수 있게 한다. 동시에 화면의 결정-종류 필터(보류/폐기/...)도 **클라이언트에서** 걸고
있었는데, 그대로 두면 필터가 "지금 받아온 페이지 안에서만" 걸려 다른 페이지에 있는
같은 종류 결정이 안 보인다 — 그래서 action 도 서버 쪽으로 옮긴다.
"""
from __future__ import annotations

import pytest

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService


@pytest.fixture()
def svc(tmp_path):
    service = ProxyGoldCandidateService(root=tmp_path)
    body = ("이 문서는 검수 시험용 본문입니다. " * 30).encode("utf-8")
    doc_ids = []
    for i in range(5):
        cand = service.create_uploaded_candidate(
            filename=f"p{i}.txt", content=body, actor_id="admin-x",
            document_origin="uploaded_document", source_reference="", authorization_basis="",
        )
        doc_ids.append(cand["doc_id"])
    service._doc_ids = doc_ids  # 시험 편의
    return service


def _decide(service, doc_id, action, reason="사유", grade=None):
    kwargs = {"doc_id": doc_id, "action": action, "reason": reason, "actor_id": "rev-a"}
    if grade:
        kwargs["grade"] = grade
    return service.decide(**kwargs)


def test_offset_moves_the_window_without_duplicating_or_skipping(svc):
    """10건을 만들고 limit=4 로 3페이지 긁으면, 합쳐서 정확히 10건(중복·누락 없이) 나와야 한다."""
    doc_ids = svc._doc_ids
    for i in range(10):
        _decide(svc, doc_ids[i % len(doc_ids)], "change", reason=f"결정{i}", grade="S2")

    seen = []
    for page in range(3):
        out = svc.recent_decisions(limit=4, offset=page * 4)
        seen.extend(e["reason"] for e in out["events"])
    assert out["total"] == 10
    assert len(seen) == 10
    assert len(set(seen)) == 10, "페이지 사이에 중복으로 겹친 레코드가 있다"
    assert set(seen) == {f"결정{i}" for i in range(10)}


def test_offset_past_the_end_returns_empty_not_an_error(svc):
    _decide(svc, svc._doc_ids[0], "change", grade="S2")
    out = svc.recent_decisions(limit=50, offset=1000)
    assert out["events"] == []
    assert out["total"] == 1


def test_action_filter_applies_before_pagination_not_after(svc):
    """결정 종류로 거르면, **전체 범위**에서 그 종류만 모아 페이지를 나눠야 한다 —
    페이지 하나 받아온 뒤 그 안에서만 걸러서는 안 된다(그러면 다른 페이지의 같은 종류가 안 보임)."""
    doc_ids = svc._doc_ids
    # defer 5건과 change 5건을 번갈아 기록해, "최근 페이지" 안에 defer 가 드물게 섞이게 한다.
    for i in range(5):
        _decide(svc, doc_ids[i], "defer", reason=f"보류{i}")
        _decide(svc, doc_ids[i], "change", reason=f"변경{i}", grade="S2")

    # limit=3 으로는 change 가 최근 기록을 많이 차지해, 클라이언트 필터링이었다면
    # 첫 페이지에 defer 가 하나도 안 잡혔을 상황을 재현한다.
    page1 = svc.recent_decisions(limit=3, offset=0, action="defer")
    page2 = svc.recent_decisions(limit=3, offset=3, action="defer")
    all_defer_reasons = [e["reason"] for e in page1["events"]] + [e["reason"] for e in page2["events"]]
    assert page1["total"] == 5, "action 필터가 적용된 뒤의 총건수여야 한다"
    assert all(e["action"] == "defer" for e in page1["events"] + page2["events"])
    assert sorted(all_defer_reasons) == sorted(f"보류{i}" for i in range(5))


def test_by_action_reports_the_unfiltered_distribution(svc):
    """by_action(필터 셀렉트가 보여줄 분포)은 action 필터를 적용하기 전 분포다 —
    필터링 후 집계를 쓰면 고른 종류만 보여 셀렉트 박스 자체가 무의미해진다."""
    doc_ids = svc._doc_ids
    _decide(svc, doc_ids[0], "defer", reason="보류")
    _decide(svc, doc_ids[1], "change", reason="변경", grade="S2")
    _decide(svc, doc_ids[2], "change", reason="변경2", grade="S1")

    filtered = svc.recent_decisions(limit=50, action="defer")
    assert filtered["by_action"] == {"change": 2, "defer": 1}
    assert filtered["total"] == 1
    assert len(filtered["events"]) == 1


def test_no_action_filter_keeps_old_behavior(svc):
    """action 을 안 주면 기존과 동일하게 전체가 나온다(하위호환)."""
    doc_ids = svc._doc_ids
    _decide(svc, doc_ids[0], "defer", reason="보류")
    _decide(svc, doc_ids[1], "change", reason="변경", grade="S2")
    out = svc.recent_decisions(limit=50)
    assert out["total"] == 2
    assert {e["action"] for e in out["events"]} == {"defer", "change"}
