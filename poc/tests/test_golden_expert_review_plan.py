"""20명 x 50건 전문가 검수 배정의 원자성·접근제어 계약."""
from __future__ import annotations

import pytest

from koipa.api import golden
from koipa.api._jwt_auth import JWTClaims
from koipa.config import settings
from koipa.schemas.golden import GoldenExpertReviewPlanRequest, GoldenExpertReviewRebalanceRequest
from koipa.services import golden_reviewer_access as access


REVIEWERS = [f"reviewer-{number:02d}" for number in range(1, 21)]
REBALANCED_REVIEWERS = [f"reviewer-{number:02d}" for number in range(1, 26)]
BATCH = "KL_EXPERT_REVIEW_20261010"


def _index() -> dict[str, str]:
    return {f"DOC-{number:04d}": BATCH for number in range(1, 1001)}


def _auth() -> dict:
    return {
        "claims": JWTClaims(sub="admin-operator", roles=("admin",)),
        "actor_role": "admin",
        "actor_roles": ["admin"],
    }


def test_exact_expert_plan_assigns_every_document_once_and_each_reviewer_gets_50(tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    rows, group_id, already_applied, plan = ledger.apply_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=_index(), actor_id="admin-operator",
    )

    assert len(rows) == 1000
    assert already_applied is False and group_id
    assert set(plan) == set(REVIEWERS)
    assert all(len(doc_ids) == 50 for doc_ids in plan.values())
    assert len({doc_id for doc_ids in plan.values() for doc_id in doc_ids}) == 1000
    assert {row["assignment_group_id"] for row in rows} == {group_id}
    assert {row["review_batch_context"] for row in rows} == {BATCH}
    assert {row["assignment_kind"] for row in rows} == {"exact_expert_review_20x50"}

    state = ledger.status(_index())
    assert state["assigned_candidate_count"] == 1000
    assert state["unassigned_candidate_count"] == 0
    assert {row["visible_doc_count"] for row in state["reviewers"]} == {50}


def test_exact_plan_status_reports_per_reviewer_decision_progress_in_its_batch(tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    _, _, _, plan = ledger.apply_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=_index(), actor_id="admin-operator",
    )
    first_docs = plan["reviewer-01"]
    decisions = [
        {"doc_id": first_docs[0], "actor_id": "reviewer-01", "action": "approve", "decided_at": "2026-10-11T01:00:00+00:00"},
        {"doc_id": first_docs[1], "actor_id": "reviewer-01", "action": "defer", "decided_at": "2026-10-11T01:01:00+00:00"},
        {"doc_id": first_docs[2], "actor_id": "reviewer-01", "action": "reopen", "decided_at": "2026-10-11T01:02:00+00:00"},
        # 다른 검수자의 기록과 결정이 아닌 출처 기록은 reviewer-01 진행률에 섞이면 안 된다.
        {"doc_id": plan["reviewer-02"][0], "actor_id": "reviewer-02", "action": "change", "decided_at": "2026-10-11T01:03:00+00:00"},
        {"doc_id": first_docs[3], "actor_id": "reviewer-01", "event_kind": "provenance", "action": "record_provenance"},
    ]

    status = ledger.status(_index(), review_batch=BATCH, decision_events=decisions)
    first = next(row for row in status["reviewers"] if row["reviewer_id"] == "reviewer-01")
    assert len(status["reviewers"]) == 20
    assert first["visible_doc_count"] == 50
    assert first["reviewed_doc_count"] == 3
    assert first["fixed_doc_count"] == 1
    assert first["deferred_doc_count"] == 1
    assert first["in_progress_doc_count"] == 1
    assert first["not_started_doc_count"] == 47
    assert first["remaining_doc_count"] == 48
    assert first["last_action"] == "reopen"
    assert status["progress_summary"]["assigned_doc_count"] == 1000
    assert status["progress_summary"]["reviewed_doc_count"] == 4


def test_assignment_status_endpoint_reads_decision_progress_from_the_candidate_ledger(monkeypatch, tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    _, _, _, plan = ledger.apply_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=_index(), actor_id="admin-operator",
    )

    class FakeService:
        root = tmp_path

        @staticmethod
        def review_batch_index():
            return _index()

        @staticmethod
        def ledger_rows():
            return [{
                "doc_id": plan["reviewer-01"][0], "actor_id": "reviewer-01",
                "action": "change", "decided_at": "2026-10-11T01:00:00+00:00",
            }]

    monkeypatch.setattr(golden, "ProxyGoldCandidateService", FakeService)
    out = golden.golden_assignment_status(review_batch=BATCH, auth=_auth())

    first = next(row for row in out["reviewers"] if row["reviewer_id"] == "reviewer-01")
    assert first["reviewed_doc_count"] == first["fixed_doc_count"] == 1
    assert out["progress_summary"]["reviewed_doc_count"] == 1


def test_exact_expert_plan_is_idempotent_only_for_the_same_exclusive_plan(tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    first = ledger.apply_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=_index(), actor_id="admin-operator",
    )
    second = ledger.apply_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=_index(), actor_id="admin-operator",
    )
    assert len(first[0]) == 1000
    assert second[0] == [] and second[2] is True
    assert len(ledger.read_events()) == 1000


def test_rebalance_replaces_an_unopened_20x50_plan_with_an_auditable_25x40_plan(tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    ledger.apply_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=_index(), actor_id="admin-operator",
    )

    rows, group_id, already_applied, plan = ledger.rebalance_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REBALANCED_REVIEWERS, doc_batches=_index(),
        decision_events=[], actor_id="admin-operator", reason="보호원 25명 x 40건 재배정",
    )
    assert already_applied is False and group_id
    assert len(rows) == 2000
    assert sum(row["event"] == access.EVENT_UNASSIGN for row in rows) == 1000
    assert sum(row["event"] == access.EVENT_ASSIGN for row in rows) == 1000
    assert {row["assignment_kind"] for row in rows} == {"exact_expert_review_25x40_rebalance"}
    assert set(plan) == set(REBALANCED_REVIEWERS)
    assert all(len(doc_ids) == 40 for doc_ids in plan.values())

    state = ledger.status(_index(), review_batch=BATCH)
    assert state["assigned_candidate_count"] == 1000
    assert {row["visible_doc_count"] for row in state["reviewers"]} == {40}
    assert len(state["reviewers"]) == 25

    repeat = ledger.rebalance_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REBALANCED_REVIEWERS, doc_batches=_index(),
        decision_events=[], actor_id="admin-operator",
    )
    assert repeat[0] == [] and repeat[2] is True


def test_rebalance_refuses_a_reviewed_batch_without_changing_assignments(tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    _, _, _, plan = ledger.apply_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=_index(), actor_id="admin-operator",
    )
    before = ledger.read_events()
    with pytest.raises(ValueError, match="reviewer decisions"):
        ledger.rebalance_exact_expert_review_plan(
            review_batch=BATCH, reviewer_ids=REBALANCED_REVIEWERS, doc_batches=_index(),
            decision_events=[{
                "doc_id": plan["reviewer-01"][0], "actor_id": "reviewer-01", "action": "change",
            }],
            actor_id="admin-operator",
        )
    assert ledger.read_events() == before


def test_exact_expert_plan_refuses_collision_without_writing_a_partial_plan(tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    ledger.apply(event=access.EVENT_ASSIGN, reviewer_id="other-reviewer", actor_id="admin-operator",
                 targets=[("doc_id", "DOC-0001")])
    before = ledger.read_events()
    with pytest.raises(ValueError, match="batch already assigned"):
        ledger.apply_exact_expert_review_plan(
            review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=_index(), actor_id="admin-operator",
        )
    assert ledger.read_events() == before


def test_exact_expert_plan_rejects_a_batch_that_is_not_exactly_1000(tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    short = {f"DOC-{number:04d}": BATCH for number in range(1, 1000)}
    with pytest.raises(ValueError, match="exactly 1000"):
        ledger.apply_exact_expert_review_plan(
            review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=short, actor_id="admin-operator",
        )
    assert ledger.read_events() == []


def test_expert_plan_endpoint_requires_both_server_enforcement_flags(monkeypatch, tmp_path):
    class FakeService:
        root = tmp_path

        @staticmethod
        def review_batch_index():
            return _index()

    monkeypatch.setattr(golden, "ProxyGoldCandidateService", FakeService)
    request = GoldenExpertReviewPlanRequest(review_batch=BATCH, reviewer_ids=REVIEWERS)
    monkeypatch.setattr(settings, "golden_reviewer_assignment_enforced", False)
    monkeypatch.setattr(settings, "golden_review_blind_enforced", True)
    with pytest.raises(golden.HTTPException) as assignment_off:
        golden.golden_expert_review_plan(request, _auth())
    assert assignment_off.value.status_code == 409

    monkeypatch.setattr(settings, "golden_reviewer_assignment_enforced", True)
    monkeypatch.setattr(settings, "golden_review_blind_enforced", False)
    with pytest.raises(golden.HTTPException) as blind_off:
        golden.golden_expert_review_plan(request, _auth())
    assert blind_off.value.status_code == 409


def test_expert_plan_endpoint_records_an_auditable_atomic_group(monkeypatch, tmp_path):
    class FakeService:
        root = tmp_path

        @staticmethod
        def review_batch_index():
            return _index()

    monkeypatch.setattr(golden, "ProxyGoldCandidateService", FakeService)
    monkeypatch.setattr(
        golden.reviewer_credentials,
        "list_accounts",
        lambda: [{"username": reviewer, "roles": ("reviewer",), "active": True} for reviewer in REVIEWERS],
    )
    monkeypatch.setattr(settings, "golden_reviewer_assignment_enforced", True)
    monkeypatch.setattr(settings, "golden_review_blind_enforced", True)
    out = golden.golden_expert_review_plan(
        GoldenExpertReviewPlanRequest(review_batch=BATCH, reviewer_ids=REVIEWERS, reason="전문가 검수 1차"),
        _auth(),
    )
    assert out.reviewer_count == 20 and out.documents_per_reviewer == 50
    assert out.total_documents == 1000 and out.events_written == 1000
    assert set(out.reviewer_document_counts.values()) == {50}
    assert {row["assignment_group_id"] for row in access.AssignmentLedger(tmp_path).read_events()} == {
        out.assignment_group_id
    }


def test_rebalance_endpoint_replaces_the_plan_only_with_25_active_reviewers(monkeypatch, tmp_path):
    class FakeService:
        root = tmp_path

        @staticmethod
        def review_batch_index():
            return _index()

        @staticmethod
        def ledger_rows():
            return []

    monkeypatch.setattr(golden, "ProxyGoldCandidateService", FakeService)
    monkeypatch.setattr(
        golden.reviewer_credentials,
        "list_accounts",
        lambda: [{"username": reviewer, "roles": ("reviewer",), "active": True}
                 for reviewer in REBALANCED_REVIEWERS],
    )
    monkeypatch.setattr(settings, "golden_reviewer_assignment_enforced", True)
    monkeypatch.setattr(settings, "golden_review_blind_enforced", True)
    ledger = access.AssignmentLedger(tmp_path)
    ledger.apply_exact_expert_review_plan(
        review_batch=BATCH, reviewer_ids=REVIEWERS, doc_batches=_index(), actor_id="admin-operator",
    )

    out = golden.golden_expert_review_plan_rebalance(
        GoldenExpertReviewRebalanceRequest(
            review_batch=BATCH, reviewer_ids=REBALANCED_REVIEWERS, reason="25명 전환",
        ),
        _auth(),
    )
    assert out.reviewer_count == 25 and out.documents_per_reviewer == 40
    assert out.revoked_assignment_events == out.assigned_events == 1000
    assert out.events_written == 2000
    assert set(out.reviewer_document_counts.values()) == {40}


def test_expert_plan_refuses_unprovisioned_reviewer_before_writing(monkeypatch, tmp_path):
    class FakeService:
        root = tmp_path

        @staticmethod
        def review_batch_index():
            return _index()

    monkeypatch.setattr(golden, "ProxyGoldCandidateService", FakeService)
    monkeypatch.setattr(
        golden.reviewer_credentials,
        "list_accounts",
        lambda: [{"username": reviewer, "roles": ("reviewer",), "active": True} for reviewer in REVIEWERS[:-1]],
    )
    monkeypatch.setattr(settings, "golden_reviewer_assignment_enforced", True)
    monkeypatch.setattr(settings, "golden_review_blind_enforced", True)
    with pytest.raises(golden.HTTPException) as denied:
        golden.golden_expert_review_plan(
            GoldenExpertReviewPlanRequest(review_batch=BATCH, reviewer_ids=REVIEWERS), _auth(),
        )
    assert denied.value.status_code == 422
    assert "명부에 없는 ID" in str(denied.value.detail)
    assert access.AssignmentLedger(tmp_path).read_events() == []


def test_reviewer_roster_exposes_only_active_reviewer_accounts(monkeypatch):
    monkeypatch.setattr(
        golden.reviewer_credentials,
        "list_accounts",
        lambda: [
            {"username": "expert-a", "roles": ("reviewer",), "active": True},
            {"username": "suspended", "roles": ("reviewer",), "active": False},
            {"username": "admin-only", "roles": ("admin",), "active": True},
        ],
    )

    assert [entry.model_dump() for entry in golden.golden_reviewer_roster()] == [
        {"reviewer_id": "expert-a", "roles": ["reviewer"]},
    ]
