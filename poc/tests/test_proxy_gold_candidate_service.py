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

    with pytest.raises(ValueError, match="source reference"):
        svc.create_uploaded_candidate(
            filename="operations.txt", content=payload, actor_id="admin",
            document_origin="organization_real", authorization_basis="소유부서 승인",
        )

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
    assert summary["actual_document_intake"] == 1
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
