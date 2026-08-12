"""관리자 콘솔 화면 ↔ API 계약 회귀 테스트 (FUN-003-⑦ · FUN-005-① · FUN-023-④ · FUN-024-①).

왜 이 파일이 있나:
요건 중 "관리자가 …할 수 있어야 한다"류는 API만으로는 충족 주장이 약하다(화면 경계는 ICD §01·K7
에서 Koipa 구간). 실제로 2026-08 시점에 등급체계 편집·합성 검수·키워드 관리는 API만 있고
admin.html 에 화면이 없었다. 화면을 추가한 뒤 그 화면이 조용히 사라지거나 엔드포인트가
어긋나는 회귀를 막기 위해 두 축을 고정한다:

  A. 정적 축 — admin.html 이 각 요건별 엔드포인트를 실제로 호출하는가(문자열 존재).
  B. 계약 축 — 콘솔이 만들어 보내는 요청 본문이 라우터/스키마에 그대로 맞는가.

B는 PG 불요(서비스 스텁). A는 파일만 읽는다.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from koipa.schemas.common import Actor, GradeDefinition

_ADMIN_HTML = (
    Path(__file__).resolve().parent.parent
    / "src" / "koipa" / "api" / "static" / "admin.html"
)


@pytest.fixture(scope="module")
def console() -> str:
    return _ADMIN_HTML.read_text(encoding="utf-8")


# ── A. 화면 존재 축 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("requirement", "needle"),
    [
        # FUN-005-① 등급체계 추가·변경·삭제 — 조회(GET)만으로는 불충분, 쓰기(PUT)가 화면에 있어야 한다.
        ("FUN-005-①", "'PUT','/schema/grades'"),
        # FUN-023-④ 라벨링·태깅 규칙 관리 — 목록·추가·수정/비활성 3경로.
        ("FUN-023-④", "'GET','/admin/keywords?'"),
        ("FUN-023-④", "'POST','/admin/keywords'"),
        ("FUN-023-④", "/admin/keywords/${k.keyword_id}"),
        # FUN-003-①⑤⑦ 합성 생성 → 검수 → 학습셋 편입.
        ("FUN-003-①", "'POST','/synth/generate'"),
        ("FUN-003-⑦", "/synth/queue?status="),
        ("FUN-003-⑦", "/review`,body"),
    ],
)
def test_console_wires_endpoint(console: str, requirement: str, needle: str):
    assert needle in console, f"{requirement}: 관리자 콘솔에 {needle} 호출이 없습니다(화면 미구현/회귀)."


def test_console_shows_precision_and_recall(console: str):
    """FUN-024-① — 정밀도·재현율은 API에만 있으면 안 되고 화면 지표 카드에 떠야 한다."""
    assert "precision_macro" in console and "recall_macro" in console
    assert "정밀도" in console and "재현율" in console


def test_console_warns_before_grade_deactivation(console: str):
    """PUT /schema/grades 는 목록 치환이라 행 삭제 = 비활성이다. 무경고 저장은 사고 경로."""
    assert "비활성(소프트 삭제)될 등급" in console


# ── B. 계약 축 ────────────────────────────────────────────────────────────────
def test_grade_put_payload_matches_schema():
    """콘솔 saveGrades() 가 만드는 본문 = {grades:[{code,name,order,description,color}], actor}."""
    from koipa.schemas.schema_admin import GradesPutRequest

    req = GradesPutRequest(
        grades=[
            GradeDefinition(code="TS", name="특급기밀", order=1, description=None, color="#7B1FA2"),
            GradeDefinition(code="S1", name="1급 비밀", order=2, description="설명", color="#D32F2F"),
        ],
        actor=Actor(user_id="console", role="admin"),
    )
    assert [g.code for g in req.grades] == ["TS", "S1"]


def test_grade_put_handler_returns_retraining_signal(monkeypatch):
    from koipa.api import schema_admin as sa
    from koipa.schemas.schema_admin import GradesPutRequest, GradesPutResponse

    monkeypatch.setattr(
        sa.SchemaAdminService, "put",
        lambda self, req: GradesPutResponse(
            version="v1", requires_retraining=True, reason="added codes: ['S4']"
        ),
    )
    resp = sa.put_grades(
        GradesPutRequest(
            grades=[GradeDefinition(code="S4", name="4급", order=5)],
            actor=Actor(user_id="console", role="admin"),
        )
    )
    # 콘솔은 이 플래그로 재학습 경고 배너를 띄운다 — 형태가 바뀌면 화면이 조용히 침묵한다.
    assert resp.requires_retraining is True and "S4" in resp.reason


def test_keyword_create_payload_matches_schema():
    """콘솔 createKeyword() 본문: grade·keyword·pattern_type·weight(+factor 선택)·actor."""
    from koipa.schemas.keyword_admin import KeywordCreateRequest

    req = KeywordCreateRequest(
        grade="TS", keyword="극비", pattern_type="regex",
        factor="MANAGEMENT", weight=0.85, actor=Actor(user_id="console", role="admin"),
    )
    assert req.pattern_type == "regex" and req.weight == 0.85


def test_keyword_weight_upper_bound_is_enforced():
    """콘솔이 9.99 로 클램프하는 근거 — Numeric(3,2) 상한."""
    from pydantic import ValidationError

    from koipa.schemas.keyword_admin import KeywordCreateRequest

    with pytest.raises(ValidationError):
        KeywordCreateRequest(
            grade="TS", keyword="x", weight=10.0, actor=Actor(user_id="c", role="admin")
        )


def test_keyword_toggle_payload_is_partial_patch():
    """비활성 버튼은 is_active 만 보낸다 — 나머지 필드는 None 이어야 덮어쓰기 사고가 없다."""
    from koipa.schemas.keyword_admin import KeywordUpdateRequest

    req = KeywordUpdateRequest(is_active=False, actor=Actor(user_id="console", role="admin"))
    assert req.is_active is False
    assert req.keyword is None and req.pattern_type is None and req.weight is None


def test_synth_review_payload_carries_corrected_grade():
    """승인 시 등급을 바꾸면 corrected_grade 로 실려야 학습행이 그 등급으로 만들어진다."""
    from koipa.schemas.synthesis import SynthReviewRequest

    req = SynthReviewRequest(
        decision="approve", corrected_grade="S1",
        comment="console synth review", actor=Actor(user_id="console", role="reviewer"),
    )
    assert req.decision == "approve" and req.corrected_grade == "S1"


def test_synth_generate_payload_matches_schema():
    from koipa.schemas.synthesis import SynthGenerateRequest

    req = SynthGenerateRequest(
        target_grade="TS", domain="hr", count=5, llm_provider="noop",
        actor=Actor(user_id="console", role="admin"),
    )
    assert req.count == 5 and req.llm_provider == "noop"


def test_synth_review_handler_404_when_missing(monkeypatch):
    from fastapi import HTTPException

    from koipa.api import synthesis as syn
    from koipa.schemas.synthesis import SynthReviewRequest

    monkeypatch.setattr(syn.SynthesisService, "review", lambda self, sid, req: None)
    with pytest.raises(HTTPException) as ei:
        syn.synth_review(
            uuid4(),
            SynthReviewRequest(decision="reject", actor=Actor(user_id="c", role="reviewer")),
            auth={"actor_role": "admin", "actor_user_id": "c"},
        )
    assert ei.value.status_code == 404
