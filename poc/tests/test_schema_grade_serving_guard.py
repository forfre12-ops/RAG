# -*- coding: utf-8 -*-
"""확정된 TS/S1/S2/S3 이외 등급은 API에서 만들 수 없다.

9/20의 4등급 고정은 모델이 우연히 로드돼 있을 때만의 안전장치가 아니다. 모델이 없거나
모델 메타를 못 읽는 상황, 관리자 force 요청까지 포함해 코드 집합의 추가·삭제·변경을
동일하게 거부해야 한다.
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


@pytest.mark.parametrize("proposed", [FOUR | {"S4"}, FOUR - {"S2"}, {"TS", "S1", "S2", "X"}])
@pytest.mark.parametrize("model_codes", [MODEL, None, frozenset({"A", "B"})])
def test_any_noncanonical_grade_set_is_blocked_even_without_a_served_model(proposed, model_codes):
    with pytest.raises(GradeSchemaBlocked, match="고정"):
        assess_grade_change(FOUR, proposed, model_codes)


def test_restoring_model_code_set_is_allowed():
    # 과거 우회로 DB가 어긋났어도 정본 4등급으로 되돌리는 저장은 허용한다.
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


def test_request_schema_has_no_force_bypass_fields():
    assert "force" not in GradesPutRequest.model_fields and "force_reason" not in GradesPutRequest.model_fields


def test_force_fields_are_rejected_not_silently_ignored():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="force"):
        GradesPutRequest.model_validate({
            "grades": [GradeDefinition(code="TS", name="특급기밀", order=1)],
            "actor": {"user_id": "kl", "role": "admin"},
            "force": True,
        })
