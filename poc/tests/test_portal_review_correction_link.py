"""포털 confirm/relabel이 실제 Correction 원장에 묶이는지의 서비스 경계 시험.

DB·큐 없이 서비스의 저장 경계를 대체해, 포털 검수 응답이 독립된 40건 표 식별자가
아니라 문서/추론/검수자/사유/시각을 가진 Correction 원장 행을 가리키는지를 잠근다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

from koipa.schemas.common import Actor, Grade
from koipa.schemas.confirm import ConfirmRequest, RelabelRequest
from koipa.services import confirm_service as service_module


_DOC_ID = uuid.uuid4()
_INFERENCE_ID = uuid.uuid4()


class _Repo:
    correction: SimpleNamespace | None = None

    def __init__(self, _db):
        self.db = _db
        self.cls = SimpleNamespace(
            classification_id=_INFERENCE_ID,
            doc_id=_DOC_ID,
            predicted_level_id=2,
            status="needs_review",
        )

    def get(self, inference_id, *, for_update=False):
        assert inference_id == _INFERENCE_ID
        assert for_update is True
        return self.cls

    def level_id_by_code(self, code):
        return {"TS": 1, "S1": 2, "S2": 3, "S3": 4}[str(getattr(code, "value", code))]

    def correction_exists(self, *_args):
        return False

    def add_correction(self, **kwargs):
        type(self).correction = SimpleNamespace(
            correction_id=841,
            reason=kwargs["reason"],
            corrected_at=dt.datetime(2026, 10, 11, 10, 0, tzinfo=dt.timezone.utc),
        )
        return type(self).correction

    def distinct_reviewers_for_level(self, *_args):
        return set()

    def unconsumed_corrections(self):
        return [self.correction] if self.correction else []


@contextmanager
def _scope():
    yield object()


def _install_repo(monkeypatch):
    monkeypatch.setattr(service_module, "session_scope", _scope)
    monkeypatch.setattr(service_module, "ClassifyRepo", _Repo)
    monkeypatch.setattr(service_module, "_record_live_fnr", lambda *_args: None)


def test_portal_confirm_receipt_points_to_persisted_correction(monkeypatch):
    _install_repo(monkeypatch)
    result = service_module.ConfirmService().confirm(
        ConfirmRequest(
            doc_id=str(_DOC_ID),
            inference_id=_INFERENCE_ID,
            confirmed_label=Grade.S1,
            note="포털 검수 확정 사유",
            actor=Actor(user_id="expert.park", role="reviewer"),
        )
    )

    assert result.persisted is True
    receipt = result.review_receipt
    assert receipt is not None
    assert receipt.doc_id == str(_DOC_ID)
    assert receipt.inference_id == _INFERENCE_ID
    assert receipt.reviewer_id == "expert.park"
    assert receipt.final_label == Grade.S1
    assert receipt.reason == "포털 검수 확정 사유"
    assert receipt.correction_id == 841
    assert receipt.reviewed_at.startswith("2026-10-11T10:00")
    assert receipt.candidate_data_status == "eligible_for_candidate_rebuild"


def test_portal_relabel_receipt_keeps_original_inference_link(monkeypatch):
    _install_repo(monkeypatch)
    result = service_module.RelabelService().relabel(
        RelabelRequest(
            doc_id=str(_DOC_ID),
            inference_id=_INFERENCE_ID,
            original_label=Grade.S1,
            corrected_label=Grade.TS,
            reason="포털 검수 정정 사유",
            actor=Actor(user_id="expert.choi", role="reviewer"),
        )
    )

    assert result.persisted is True
    receipt = result.review_receipt
    assert receipt is not None
    assert receipt.doc_id == str(_DOC_ID)
    assert receipt.inference_id == _INFERENCE_ID
    assert receipt.reviewer_id == "expert.choi"
    assert receipt.final_label == Grade.TS
    assert receipt.reason == "포털 검수 정정 사유"
    assert receipt.correction_id == 841
    assert receipt.candidate_data_status == "eligible_for_candidate_rebuild"
