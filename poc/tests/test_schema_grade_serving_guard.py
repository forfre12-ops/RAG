# -*- coding: utf-8 -*-
"""등급체계 변경이 서빙 분류기를 멈추게 하면 막는다 — FUN-005-01 · 감리 별첨 177(마).

서빙은 모델의 등급 집합이 DB 활성 등급 집합과 다르면 모델 로드를 거부하고 규칙엔진으로
내려간다(m5_inference/pipeline.py _id2label_from_config). 종전에는 그런 변경이
requires_retraining=True 만 달고 저장돼, 다음 재기동부터 분류기가 조용히 멈췄다(2026-09-10).
콘솔은 그 상태를 "재학습 전까지 기존 라벨로 추론합니다"라고 안내하고 있었다.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from koipa.schemas.common import Actor, GradeDefinition
from koipa.schemas.schema_admin import GradesPutRequest
from koipa.services import schema_admin_service as sas
from koipa.services.schema_admin_service import GradeSchemaBlocked, assess_grade_change

FOUR = {"TS", "S1", "S2", "S3"}
MODEL = frozenset(FOUR)


def test_same_code_set_passes():
    # 이름·설명·색·순서만 바뀌는 저장 — 집합이 같으면 모델 매핑과 무관하다
    assert assess_grade_change(FOUR, set(FOUR), MODEL) == ("", [])


def test_no_change_passes_even_when_db_already_mismatched():
    assert assess_grade_change(FOUR, set(FOUR), frozenset({"A", "B"})) == ("", [])


def test_adding_grade_with_served_model_is_blocked():
    with pytest.raises(GradeSchemaBlocked, match="규칙엔진"):
        assess_grade_change(FOUR, FOUR | {"S4"}, MODEL)


def test_deactivating_grade_with_served_model_is_blocked():
    with pytest.raises(GradeSchemaBlocked, match="force"):
        assess_grade_change(FOUR, FOUR - {"S2"}, MODEL)


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_force_without_reason_is_blocked(reason):
    with pytest.raises(GradeSchemaBlocked, match="사유"):
        assess_grade_change(FOUR, FOUR - {"S2"}, MODEL, force=True, force_reason=reason)


def test_force_with_reason_passes_and_reports_impact():
    impact, extra = assess_grade_change(
        FOUR, FOUR - {"S2"}, MODEL, model_name="v-x", force=True, force_reason="S2 폐지 결정 — 재학습 예정",
    )
    assert "규칙엔진" in impact and "v-x" in impact
    assert "forced: S2 폐지 결정 — 재학습 예정" in extra


def test_without_served_model_nothing_to_protect():
    assert assess_grade_change(FOUR, FOUR - {"S3"}, None)[0] == ""


def test_untrainable_code_is_reported_even_without_model():
    _, extra = assess_grade_change(FOUR, FOUR | {"S4"}, None)
    assert any("S4" in e and "4등급" in e for e in extra)


def test_restoring_model_code_set_is_allowed():
    # DB 가 이미 어긋나 있을 때(S2 비활성) 모델과 같은 집합으로 되돌리는 변경은 통과
    assert assess_grade_change(FOUR - {"S2"}, set(FOUR), MODEL) == ("", [])


def test_served_model_codes_reads_config_without_loading_model(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(
        json.dumps({"id2label": {"0": "TS", "1": "S1", "2": "S2", "3": "S3"}}), encoding="utf-8"
    )
    monkeypatch.setattr("koipa.services.classify_service._resolve_serving_model_dir", lambda: str(tmp_path))
    assert sas.served_model_codes() == (str(tmp_path), MODEL)


def test_served_model_codes_without_model(monkeypatch):
    monkeypatch.setattr("koipa.services.classify_service._resolve_serving_model_dir", lambda: None)
    assert sas.served_model_codes() == (None, None)


def test_served_model_codes_unreadable_config(tmp_path, monkeypatch):
    monkeypatch.setattr("koipa.services.classify_service._resolve_serving_model_dir", lambda: str(tmp_path))
    assert sas.served_model_codes() == (str(tmp_path), None)


def test_api_translates_block_to_409(monkeypatch):
    from koipa.api import schema_admin as api

    def _blocked(self, req):
        raise GradeSchemaBlocked("분류기가 멈춥니다")

    monkeypatch.setattr(sas.SchemaAdminService, "put", _blocked)
    req = GradesPutRequest(
        grades=[GradeDefinition(code="TS", name="특급기밀", order=1)],
        actor=Actor(user_id="console", role="admin"),
    )
    with pytest.raises(HTTPException) as ei:
        api.put_grades(req)
    assert ei.value.status_code == 409 and "분류기가 멈춥니다" in ei.value.detail


def test_request_defaults_keep_old_callers_working():
    req = GradesPutRequest(
        grades=[GradeDefinition(code="TS", name="특급기밀", order=1)],
        actor=Actor(user_id="kl", role="admin"),
    )
    assert req.force is False and req.force_reason is None
