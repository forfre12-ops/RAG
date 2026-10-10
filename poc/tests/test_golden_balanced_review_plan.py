"""실제 검수자 수를 기준으로 하는 균등 검수 배정 계약."""
from __future__ import annotations

import pytest

from koipa.api import golden
from koipa.api._jwt_auth import JWTClaims
from koipa.config import settings
from koipa.schemas.golden import GoldenReviewPlanRequest
from koipa.services import golden_reviewer_access as access


BATCH = "KL_REVIEW_20261011"
REVIEWERS = ["reviewer-a", "reviewer-b", "reviewer-c"]


def _index(count: int = 8) -> dict[str, str]:
    return {f"DOC-{number:04d}": BATCH for number in range(1, count + 1)}


def _auth() -> dict:
    return {
        "claims": JWTClaims(sub="admin-operator", roles=("admin",)),
        "actor_role": "admin",
        "actor_roles": ["admin"],
    }


def test_balanced_plan_assigns_every_document_once_with_at_most_one_difference(tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    rows, group_id, already_applied, plan = ledger.apply_balanced_review_plan(
        review_batch=BATCH,
        reviewer_ids=REVIEWERS,
        doc_batches=_index(),
        actor_id="admin-operator",
    )

    counts = [len(doc_ids) for doc_ids in plan.values()]
    assert len(rows) == 8
    assert already_applied is False and group_id
    assert max(counts) - min(counts) <= 1
    assert {doc_id for doc_ids in plan.values() for doc_id in doc_ids} == set(_index())
    assert {row["assignment_kind"] for row in rows} == {"balanced_review_plan"}
    assert {row["assignment_group_id"] for row in rows} == {group_id}


def test_balanced_plan_retries_idempotently_and_refuses_too_many_reviewers(tmp_path):
    ledger = access.AssignmentLedger(tmp_path)
    first = ledger.apply_balanced_review_plan(
        review_batch=BATCH,
        reviewer_ids=REVIEWERS,
        doc_batches=_index(),
        actor_id="admin-operator",
    )
    second = ledger.apply_balanced_review_plan(
        review_batch=BATCH,
        reviewer_ids=REVIEWERS,
        doc_batches=_index(),
        actor_id="admin-operator",
    )

    assert len(first[0]) == 8
    assert second[0] == [] and second[2] is True
    with pytest.raises(ValueError, match="candidates but"):
        ledger.apply_balanced_review_plan(
            review_batch=BATCH,
            reviewer_ids=["one", "two", "three"],
            doc_batches=_index(2),
            actor_id="admin-operator",
        )


def test_balanced_plan_endpoint_checks_active_accounts_and_returns_actual_range(monkeypatch, tmp_path):
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

    out = golden.golden_review_plan(
        GoldenReviewPlanRequest(review_batch=BATCH, reviewer_ids=REVIEWERS),
        _auth(),
    )

    assert out.reviewer_count == 3 and out.total_documents == 8
    assert (out.min_documents_per_reviewer, out.max_documents_per_reviewer) == (2, 3)
    assert sum(out.reviewer_document_counts.values()) == 8
