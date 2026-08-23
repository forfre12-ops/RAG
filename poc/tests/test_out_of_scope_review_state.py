"""'검수 대상 아님' — 확정도 폐기도 아니면서 검수 큐에서 빠지는 제3의 종결.

왜(2026-08-18, WP-B2). 종전 상태는 둘뿐이었다.

    deferred   보류 — 나중에 다시 볼 것
    discarded  폐기 — 문서를 버림

"등급을 정할 일도 아니고 버릴 것도 아닌데 이번 검수 범위가 아닌" 항목을 표현할 수 없었다.
그래서 그런 문서가 화면 상단 '등급 미확정 N건'에 영원히 남아, 검수자에게 끝나지 않는
할 일로 보였다.

⚠ **이 상태는 학습에 대해 아무 말도 하지 않는다.** 콘솔 status 를 읽는 학습 경로가 아직
  없기 때문이다(B3). 그래서 이름도 '학습 제외' 가 아니라 '검수 대상 아님' 이다 — 화면이
  근거 없는 효과를 주장하게 두지 않는다.

전이표가 서비스·스키마·API 화이트리스트·화면 네 계층에 중복돼 있어, 한 곳만 고치면
나머지가 어긋난다. 이 시험이 네 계층을 함께 잠근다.
"""

from __future__ import annotations

import json

import pytest

from koipa.api.golden import _render_specledger_gold_console_html
from koipa.schemas.golden import ProxyGoldCandidateDecisionRequest
from koipa.services.proxy_gold_candidate_service import (
    OUT_OF_SCOPE_STATUS,
    QUEUE_EXCLUDED_STATUSES,
    ProxyGoldCandidateService,
)


def _candidate(root, doc_id: str, grade: str = "S2") -> None:
    (root / f"{doc_id}_검토문서.md").write_text("# 검토 문서\n" + "가" * 120, encoding="utf-8")
    (root / f"{doc_id}.metadata.json").write_text(json.dumps({
        "doc_id": doc_id, "intended_label": grade, "document_origin": "synthetic",
        "document_type": "검토 문서", "candidate_status": "proposed",
        "requires_manual_audit": True, "claim_scope": "synthetic proxy only",
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def svc(tmp_path):
    for i in range(1, 4):
        _candidate(tmp_path, f"CAND-00{i}")
    s = ProxyGoldCandidateService(tmp_path)
    s.decide(doc_id="CAND-003", action="exclude", reason="이번 회차 범위 밖",
             actor_id="지재원관리자")
    return s


# ── 1계층: 서비스 ────────────────────────────────────────────────────────────

def test_exclude_moves_the_candidate_out_of_scope(svc):
    c = svc.get_candidate("CAND-003")
    assert c["status"] == OUT_OF_SCOPE_STATUS


def test_exclude_never_fixes_a_grade(svc):
    """등급을 정하지 않는 전이다 — 남으면 '확정 아님인데 등급이 있는' 레코드가 생긴다."""
    c = svc.get_candidate("CAND-003")
    assert c["final_grade"] is None and c["grade_fixed"] is False


def test_exclude_requires_a_reason(svc):
    with pytest.raises(ValueError, match="reason is required"):
        svc.decide(doc_id="CAND-001", action="exclude", reason="  ", actor_id="지재원관리자")


def test_exclude_is_reversible(svc):
    """잘못 눌렀을 때 되돌릴 수 없으면 검수자가 손댈 수 없다."""
    out = svc.decide(doc_id="CAND-003", action="reopen", reason="다시 본다",
                     actor_id="지재원관리자")
    assert out["status"] == "proposed"
    assert "CAND-003" in [c["doc_id"] for c in svc.list_candidates()["candidates"]]


def test_out_of_scope_leaves_the_queue_but_not_the_ledger(svc, tmp_path):
    ids = [c["doc_id"] for c in svc.list_candidates()["candidates"]]
    assert "CAND-003" not in ids
    assert [c["doc_id"] for c in svc.list_candidates(status=OUT_OF_SCOPE_STATUS)["candidates"]] \
        == ["CAND-003"]
    raw = (tmp_path / "candidate_decisions.jsonl").read_text(encoding="utf-8")
    assert '"action": "exclude"' in raw


def test_it_stops_counting_as_unfinished_work(svc):
    """이게 핵심이다 — 상단 '등급 미확정' 이 안 줄면 이 상태를 만든 의미가 없다."""
    s = svc.list_candidates()["summary"]
    assert s["total"] == 3               # 원장 전량은 그대로
    assert s["unfixed"] == 2             # 검수해야 할 것만
    assert s["out_of_scope"] == 1        # 별도 카운터


def test_deferred_is_not_excluded_from_the_queue(tmp_path):
    """보류는 '나중에 볼 것' 이다 — 큐에서 빼면 영영 안 본다."""
    _candidate(tmp_path, "CAND-009")
    s = ProxyGoldCandidateService(tmp_path)
    s.decide(doc_id="CAND-009", action="defer", reason="자료 부족", actor_id="지재원관리자")
    assert "deferred" not in QUEUE_EXCLUDED_STATUSES
    assert [c["doc_id"] for c in s.list_candidates()["candidates"]] == ["CAND-009"]


def test_out_of_scope_is_out_of_the_quality_pool(svc):
    assert svc.list_candidates()["summary"]["quality"]["documents"] == 2


# ── 2계층: 스키마 ────────────────────────────────────────────────────────────

def test_schema_accepts_exclude_with_a_reason():
    ProxyGoldCandidateDecisionRequest(action="exclude", reason="범위 밖")


def test_schema_rejects_exclude_without_a_reason():
    with pytest.raises(ValueError, match="사유가 필요"):
        ProxyGoldCandidateDecisionRequest(action="exclude", reason="")


def test_schema_rejects_a_grade_on_exclude():
    with pytest.raises(ValueError, match="action=change"):
        ProxyGoldCandidateDecisionRequest(action="exclude", reason="범위 밖", grade="S2")


# ── 3계층: API 화이트리스트 ─────────────────────────────────────────────────

def test_api_status_whitelist_accepts_the_new_status():
    """빠지면 화면 필터가 422 로 죽는다."""
    import inspect

    from koipa.api import golden
    src = inspect.getsource(golden.proxy_gold_candidate_list)
    assert '"out_of_scope"' in src


# ── 4계층: 화면 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("needle,why", [
    ('<option value="out_of_scope">', "상태 필터에 없다"),
    ("out_of_scope:'검수 대상 아님'", "pill 라벨이 없어 영문 코드가 화면에 뜬다"),
    ("option('exclude','검수 대상 아님')", "결정 select 에 없다"),
    ("c.status==='out_of_scope'", "이 상태에서 재검토 되돌리기가 안 뜬다"),
    ("'exclude'].includes(action)", "사유 필수 검사에 빠졌다"),
    ('<option value="exclude">', "원장 필터에 없다"),
    ("exclude:'검수 대상 아님'}", "원장 이력 라벨이 없다"),
    ("metric('검수 대상 아님'", "KPI 카드가 없다"),
])
def test_console_wires_the_new_state_everywhere(needle, why):
    assert needle in _render_specledger_gold_console_html(), why
