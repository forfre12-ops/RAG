"""[P1 정직성] confirm/relabel 응답이 persisted/warnings 를 표면화하는지 검증.

배경: 서비스(ConfirmResult/RelabelResult)는 persisted·warnings·second_review_required 를
판정하는데 응답 스키마·매퍼가 이를 버려, 분류 미존재/DB미가용에도 200+id 만 반환하는 '무음
성공'이었다. HTTP status 는 DB-down 안전계약상 200 유지하되, 응답 필드로 실제 영속 여부·경고를
노출해 호출자가 감사-only 상황을 구분하게 한다. 본 테스트는 매퍼가 필드를 전파하는지 잠근다.
"""

from __future__ import annotations

import uuid

from lloydk.services.confirm_service import (
    ConfirmResult,
    RelabelResult,
    to_confirm_response,
    to_relabel_response,
)


def test_confirm_response_surfaces_not_persisted():
    # 분류 미존재 → persisted=False + 경고. 이전엔 응답에서 사라져 '무음 성공'이었음.
    res = ConfirmResult(
        confirmation_id=uuid.uuid4(),
        confirmed_at="2026-07-01T00:00:00Z",
        persisted=False,
        warnings=["classification not found in DB — confirmation recorded in audit only"],
    )
    resp = to_confirm_response(res)
    assert resp.persisted is False
    assert any("not found" in w for w in resp.warnings)
    assert resp.second_review_required is False


def test_confirm_response_surfaces_second_review():
    res = ConfirmResult(
        confirmation_id=uuid.uuid4(),
        confirmed_at="2026-07-01T00:00:00Z",
        persisted=True,
        warnings=["held as needs_second_review"],
        second_review_required=True,
    )
    resp = to_confirm_response(res)
    assert resp.persisted is True
    assert resp.second_review_required is True


def test_confirm_response_happy_path_defaults():
    res = ConfirmResult(
        confirmation_id=uuid.uuid4(),
        confirmed_at="2026-07-01T00:00:00Z",
        persisted=True,
        warnings=[],
    )
    resp = to_confirm_response(res)
    assert resp.persisted is True
    assert resp.warnings == []
    assert resp.second_review_required is False


def test_relabel_response_surfaces_not_persisted():
    res = RelabelResult(
        relabel_id=uuid.uuid4(),
        queue_size=0,
        retrain_threshold=50,
        persisted=False,
        warnings=["classification not found in DB — relabel queued in audit only"],
    )
    resp = to_relabel_response(res)
    assert resp.persisted is False
    assert any("not found" in w for w in resp.warnings)
    # 기존 필드도 유지
    assert resp.queue_size == 0
    assert resp.retrain_threshold == 50


def test_relabel_response_happy_path():
    res = RelabelResult(
        relabel_id=uuid.uuid4(),
        queue_size=3,
        retrain_threshold=50,
        persisted=True,
        warnings=[],
    )
    resp = to_relabel_response(res)
    assert resp.persisted is True
    assert resp.queue_size == 3
