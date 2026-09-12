"""폐기한 후보는 검수 큐와 품질 집계에서 빠진다 — 원장에서는 빠지지 않는다.

왜(2026-08-17, WP-B1).

    목록    list_candidates 가 status 없이 불리면 폐기까지 돌려줬다.
            검수가 끝난 항목이 계속 할 일로 보인다.
    품질    _quality 는 등급 유무만 보고 status 를 안 봤다. discard 는 final_grade 만
            None 으로 되돌리고 proposed_grade(intended_label)는 남기므로, **골든셋에서
            뺀 문서가 그 골든셋의 건강도(길이누출·등급노출·등급균형·실문서비율)를
            계속 좌우했다.**

두 가지를 동시에 지켜야 한다.

    빼는 것   기본 목록 · 품질 모수
    남기는 것  원장(candidate_decisions.jsonl) · summary.total · by_status · 명시 조회
"""

from __future__ import annotations

import json

import pytest

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService


def _candidate(root, doc_id: str, grade: str, *, chars: int = 100) -> None:
    (root / f"{doc_id}_검토문서.md").write_text("# 검토 문서\n" + "가" * chars, encoding="utf-8")
    (root / f"{doc_id}.metadata.json").write_text(json.dumps({
        "doc_id": doc_id, "intended_label": grade, "document_origin": "synthetic",
        "document_type": "검토 문서", "candidate_status": "proposed",
        "requires_manual_audit": True, "claim_scope": "synthetic proxy only",
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def svc(tmp_path):
    for i, g in enumerate(("TS", "S1", "S2", "S3"), start=1):
        _candidate(tmp_path, f"CAND-00{i}", g)
    s = ProxyGoldCandidateService(tmp_path)
    s.decide(doc_id="CAND-004", action="discard", reason="중복 문서",
             actor_id="지재원관리자")
    return s


def test_discarded_is_gone_from_the_default_queue(svc):
    ids = [c["doc_id"] for c in svc.list_candidates()["candidates"]]
    assert "CAND-004" not in ids
    assert len(ids) == 3


def test_discarded_is_still_retrievable_by_explicit_status(svc):
    """원장 보존 — 목록에서 뺀 것이지 없앤 것이 아니다."""
    out = svc.list_candidates(status="discarded")
    assert [c["doc_id"] for c in out["candidates"]] == ["CAND-004"]


def test_kpi_totals_stay_on_the_full_ledger(svc):
    """상단 숫자가 필터마다 흔들리면 무엇을 세는 값인지 알 수 없다."""
    out = svc.list_candidates()
    assert out["summary"]["total"] == 4          # 원장 전량
    assert out["total"] == 3                     # 목록에 실린 수
    assert out["summary"]["discarded"] == 1
    assert "discarded" in out["summary"]["by_status"]


def test_response_says_the_list_dropped_discarded(svc):
    """두 숫자가 다른 이유가 응답에 있어야 화면이 설명할 수 있다."""
    assert svc.list_candidates()["listed_excludes_discarded"] is True
    assert svc.list_candidates(status="discarded")["listed_excludes_discarded"] is False


def test_quality_pool_drops_discarded(svc):
    q = svc.list_candidates()["summary"]["quality"]
    assert q["documents"] == 3, "폐기 문서가 품질 모수에 남아 있다"


def test_discarded_cannot_skew_length_leakage(tmp_path):
    """폐기 문서가 길이누출 지표를 좌우하지 못한다.

    길이만으로 등급을 맞히는 비율은 골든셋의 건강도 지표다. 폐기한 문서가 섞이면
    쓰지도 않을 문서 때문에 셋이 나쁘다/좋다는 판단이 흔들린다.
    """
    for i, (g, n) in enumerate([("TS", 100), ("S1", 200), ("S2", 300), ("S3", 400)], start=1):
        _candidate(tmp_path, f"KEEP-00{i}", g, chars=n)
    # 길이-등급 대응을 정반대로 깨뜨리는 문서들을 넣고 폐기한다.
    for i, (g, n) in enumerate([("S3", 100), ("S2", 200), ("S1", 300), ("TS", 400)], start=1):
        _candidate(tmp_path, f"DROP-00{i}", g, chars=n)
    s = ProxyGoldCandidateService(tmp_path)
    for i in range(1, 5):
        s.decide(doc_id=f"DROP-00{i}", action="discard", reason="중복", actor_id="지재원관리자")

    q = s.list_candidates()["summary"]["quality"]
    assert q["documents"] == 4, "폐기 4건이 품질 모수에 남았다"


def test_the_ledger_itself_is_untouched(svc, tmp_path):
    """빼는 것은 조회일 뿐 기록이 아니다."""
    lines = [json.loads(x) for x in
             (tmp_path / "candidate_decisions.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(e.get("doc_id") == "CAND-004" and e.get("action") == "discard" for e in lines)
