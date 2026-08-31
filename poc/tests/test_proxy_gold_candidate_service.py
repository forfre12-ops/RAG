from __future__ import annotations

import json

import pytest

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService


def _candidate(root, doc_id="CAND-001", grade="S1"):
    (root / f"{doc_id}_검토문서.md").write_text("# 검토 문서\n" + "가" * 100, encoding="utf-8")
    (root / f"{doc_id}.metadata.json").write_text(json.dumps({
        "doc_id": doc_id,
        "intended_label": grade,
        "document_origin": "synthetic",
        "document_type": "검토 문서",
        "candidate_status": "proposed",
        "requires_manual_audit": True,
        "claim_scope": "synthetic proxy only",
    }, ensure_ascii=False), encoding="utf-8")


def test_approval_is_proxy_only_and_is_audited(tmp_path):
    _candidate(tmp_path)
    svc = ProxyGoldCandidateService(tmp_path)

    before = svc.get_candidate("CAND-001")
    assert before and before["status"] == "proposed" and before["final_grade"] is None

    after = svc.decide(doc_id="CAND-001", action="approve", actor_id="admin-kim")
    assert after and after["status"] == "approved_proxy"
    assert after["final_grade"] == "S1"
    assert after["latest_decision"]["document_origin"] == "synthetic"
    assert after["latest_decision"]["actor_id"] == "admin-kim"
    assert (tmp_path / "candidate_decisions.jsonl").exists()


def test_change_defer_reject_require_reasons_and_latest_decision_wins(tmp_path):
    _candidate(tmp_path, grade="S2")
    svc = ProxyGoldCandidateService(tmp_path)

    with pytest.raises(ValueError, match="reason"):
        svc.decide(doc_id="CAND-001", action="change", grade="S1", actor_id="admin")
    with pytest.raises(ValueError, match="reason"):
        svc.decide(doc_id="CAND-001", action="defer", actor_id="admin")
    with pytest.raises(ValueError, match="reason"):
        svc.decide(doc_id="CAND-001", action="reject", actor_id="admin")

    svc.decide(doc_id="CAND-001", action="change", grade="S1", reason="근거가 S1에 해당", actor_id="admin")
    changed = svc.get_candidate("CAND-001")
    assert changed and changed["status"] == "approved_proxy" and changed["final_grade"] == "S1"
    svc.decide(doc_id="CAND-001", action="defer", reason="추가 근거 확인", actor_id="admin2")
    deferred = svc.get_candidate("CAND-001")
    assert deferred and deferred["status"] == "deferred" and deferred["final_grade"] is None


def test_listing_never_mislabels_synthetic_as_locked_gold(tmp_path):
    _candidate(tmp_path, "CAND-001", "TS")
    _candidate(tmp_path, "CAND-002", "S3")
    result = ProxyGoldCandidateService(tmp_path).list_candidates()
    assert result["total"] == 2
    assert result["summary"]["by_status"] == {"proposed": 2}
    assert {row["document_origin"] for row in result["candidates"]} == {"synthetic"}
    assert all(row["status"] != "locked_gold_eval" for row in result["candidates"])


def test_uploaded_document_starts_ungraded_and_never_becomes_proxy_gold(tmp_path):
    svc = ProxyGoldCandidateService(tmp_path)
    uploaded = svc.create_uploaded_candidate(
        filename="회의록.txt", content=("검토 대상 실제 입력 문서입니다. " * 20).encode("utf-8"),
        actor_id="admin-kim",
    )
    assert uploaded["document_origin"] == "uploaded_document"
    assert uploaded["status"] == "under_review"
    assert uploaded["proposed_grade"] is None and uploaded["final_grade"] is None
    assert uploaded["extraction"]["method"] == "plain"

    fixed = svc.decide(
        doc_id=uploaded["doc_id"], action="change", grade="S1",
        reason="검수 기준상 S1", actor_id="admin-kim",
    )
    assert fixed and fixed["status"] == "grade_fixed_unlocked"
    assert fixed["final_grade"] == "S1"
    assert fixed["status"] != "approved_proxy"

    discarded = svc.decide(
        doc_id=uploaded["doc_id"], action="discard",
        reason="중복 업로드", actor_id="admin-kim",
    )
    assert discarded and discarded["status"] == "discarded"
    summary = svc.summary()
    assert summary["discarded"] == 1 and summary["fixed"] == 0


def test_actual_s2_s3_intake_requires_provenance_and_stays_unlocked(tmp_path):
    svc = ProxyGoldCandidateService(tmp_path)
    payload = ("실제 조직 운영 문서이며 S2 또는 S3 분류 검토를 위한 근거를 포함합니다. " * 20).encode("utf-8")

    # [2026-08-31] 출처는 등록도 등급 확정도 막지 않는다 — 발주처 지시로 게이트를 걷었다.
    # 남은 계약은 "막지 않되 원장에 남긴다" 하나다.
    partial = svc.create_uploaded_candidate(
        filename="operations_partial.txt", content=payload, actor_id="admin",
        document_origin="organization_real", authorization_basis="소유부서 승인",
    )
    assert partial["provenance"]["status"] == "partial"   # 권한만 있고 원천 위치 없음
    fixed = svc.decide(
        doc_id=partial["doc_id"], action="change", grade="S2",
        reason="본문 검토 결과 조직 내부 문서", actor_id="admin",
    )
    assert fixed["final_grade"] == "S2"
    assert svc.get_candidate(partial["doc_id"])["decision_history"][-1][
        "provenance_at_decision"] == "partial"
    # 확정이 아닌 결정은 막지 않는다 — 막으면 검수 큐가 닫히지 않는다.
    deferred = svc.decide(
        doc_id=partial["doc_id"], action="defer",
        reason="출처 확인 후 재검토", actor_id="admin",
    )
    assert deferred and deferred["status"] == "deferred"

    actual = svc.create_uploaded_candidate(
        filename="operations.txt", content=payload, actor_id="admin",
        document_origin="organization_real", source_reference="품질관리/운영절차/2026",
        authorization_basis="소유부서 승인", title="2026 품질관리 운영절차",
    )
    assert actual["document_origin"] == "organization_real"
    assert actual["title"] == "2026 품질관리 운영절차"
    assert actual["is_actual_document"] is True
    assert actual["provenance"]["status"] == "recorded"
    assert actual["status"] == "under_review"

    changed = svc.decide(
        doc_id=actual["doc_id"], action="change", grade="S2",
        reason="S2 등급 근거 검토 완료", actor_id="admin",
    )
    assert changed and changed["status"] == "grade_fixed_unlocked"
    assert changed["status"] != "locked_gold_eval"
    summary = svc.summary()
    assert summary["actual_document_intake"] == 2          # 근거 미완 1 + 완비 1
    assert summary["actual_provenance_recorded"] == 1
    assert summary["actual_grade_fixed_unlocked"] == 1


def test_verified_public_intake_proposes_s3_without_fixing_a_grade(tmp_path):
    svc = ProxyGoldCandidateService(tmp_path)
    item = svc.create_uploaded_candidate(
        filename="public.txt", content=("공개 고시 원문입니다. " * 30).encode("utf-8"), actor_id="admin",
        document_origin="public_real", source_reference="https://public.example.test/notice/1",
        authorization_basis="공개 고시; 내부 검수용 보관",
    )
    assert item["proposed_grade"] == "S3"
    assert item["proposed_grade_basis"] == "public source recorded; human confirmation pending"
    assert item["final_grade"] is None and item["status"] == "under_review"

def test_second_decision_is_not_masked_by_a_coarse_filesystem_clock(tmp_path):
    """[2026-08-31] 같은 mtime 틱 안에 들어온 두 번째 결정이 캐시에 가려지면 안 된다.

    실측(223 · 30회): 등급 확정 직후 보류를 걸면 11회가 옛 상태를 돌려줬다. 그 컨테이너
    파일시스템의 mtime 해상도가 4ms 라, 두 기록이 같은 틱에 떨어지면 캐시 키가 충돌했다.
    원장에는 defer 가 정확히 적혀 있는데 화면만 확정으로 남는다 - 검수자가 방금 누른
    결정이 사라진 것처럼 보인다.

    시계 해상도는 기계마다 다르므로(개발 PC 에서는 재현되지 않았다) **강제로 같게 만들어**
    본다. 캐시 키에 바이트 크기가 들어 있으면 mtime 이 같아도 무효화된다.
    """
    import os

    svc = ProxyGoldCandidateService(tmp_path)
    doc_id = "CAND-001"
    _candidate(tmp_path, doc_id=doc_id)
    ledger = tmp_path / "candidate_decisions.jsonl"

    svc.decide(doc_id=doc_id, action="change", grade="S2", reason="확정", actor_id="admin")
    assert svc.get_candidate(doc_id)["final_grade"] == "S2"   # 이 읽기가 캐시를 채운다
    frozen = os.stat(ledger).st_mtime_ns

    # 두 번째 결정이 첫 번째와 **같은 mtime 틱**에 기록된 상황을 그대로 만든다.
    # 원장에 defer 를 덧붙인 뒤 mtime 을 되돌리면, 시계만 보는 캐시는 무효화되지 않는다.
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({
            "schema_version": 1, "doc_id": doc_id, "action": "defer",
            "status": "deferred", "final_grade": None, "reason": "재검토",
            "actor_id": "admin", "decided_at": "2026-08-31T00:00:00+00:00",
        }, ensure_ascii=False, sort_keys=True) + "\n")
    os.utime(ledger, ns=(frozen, frozen))

    fresh = ProxyGoldCandidateService(tmp_path).get_candidate(doc_id)
    assert fresh["status"] == "deferred", "mtime 이 같다는 이유로 캐시가 옛 상태를 돌려줬다"
    assert fresh["final_grade"] is None
