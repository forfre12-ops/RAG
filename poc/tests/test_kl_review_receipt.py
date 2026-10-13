"""KL 포털 검수 영수증 — Agent 답변과 분리된 검수·후보 데이터 폐루프.

검수 요청은 Agent의 /classify 응답을 기다리게 하지 않는다. 다만 포털이 confirm/relabel을
남긴 뒤에는 그 결과가 out-of-band 표가 아니라 Correction 원장과 재학습 후보 입구를
추적할 수 있어야 한다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

from koipa.schemas.common import Grade
from koipa.services.confirm_service import (
    ConfirmResult,
    _review_receipt,
    to_confirm_response,
)


def test_persisted_human_review_receipt_is_traceable_candidate_entry():
    inference_id = uuid.uuid4()
    correction = SimpleNamespace(
        correction_id=73,
        reason="포털 검수: 관리조치 근거 확인",
        corrected_at=dt.datetime(2026, 10, 11, 9, 30, tzinfo=dt.timezone.utc),
    )

    receipt = _review_receipt(
        doc_id="doc-portal-17",
        inference_id=inference_id,
        reviewer_id="reviewer-17",
        final_label=Grade.S1,
        reason="ignored when persisted correction has its own reason",
        reviewed_at="2026-10-11T09:29:00+00:00",
        correction=correction,
        classification_status="corrected",
        persisted=True,
    )

    assert receipt.doc_id == "doc-portal-17"
    assert receipt.inference_id == inference_id
    assert receipt.reviewer_id == "reviewer-17"
    assert receipt.final_label == Grade.S1
    assert receipt.reason == "포털 검수: 관리조치 근거 확인"
    assert receipt.reviewed_at.startswith("2026-10-11T09:30")
    assert receipt.correction_id == 73
    assert receipt.candidate_data_status == "eligible_for_candidate_rebuild"


def test_held_or_unpersisted_review_is_not_presented_as_candidate_data():
    held = _review_receipt(
        doc_id="doc-1", inference_id=None, reviewer_id="reviewer-01",
        final_label=Grade.TS, reason=None, reviewed_at="2026-10-11T00:00:00Z",
        classification_status="needs_second_review", persisted=True,
    )
    missing = _review_receipt(
        doc_id="doc-2", inference_id=None, reviewer_id="reviewer-02",
        final_label=Grade.S2, reason=None, reviewed_at="2026-10-11T00:00:00Z",
        persisted=False,
    )

    assert held.candidate_data_status == "held_for_finalization"
    assert missing.candidate_data_status == "not_persisted"
    assert missing.correction_id is None


def test_confirm_response_exposes_receipt_as_additive_field():
    receipt = _review_receipt(
        doc_id="doc-3", inference_id=None, reviewer_id="reviewer-03",
        final_label=Grade.S3, reason="same label confirmed",
        reviewed_at="2026-10-11T00:00:00Z", classification_status="confirmed",
        persisted=True,
    )
    response = to_confirm_response(
        ConfirmResult(
            confirmation_id=uuid.uuid4(),
            confirmed_at="2026-10-11T00:00:00Z",
            persisted=True,
            warnings=[],
            review_receipt=receipt,
        )
    )

    assert response.confirmation_id
    assert response.review_receipt == receipt
    assert response.review_receipt.candidate_data_status == "eligible_for_candidate_rebuild"
