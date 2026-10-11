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
        # FUN-005-① 고정 4등급의 표시 메타데이터 — 조회(GET)만으로는 불충분, 쓰기(PUT)가 화면에 있어야 한다.
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


@pytest.mark.parametrize(
    ("mark", "requirement"),
    [("§N", "FUN-003-⑦"), ("§E", "FUN-005-①"), ("§T", "FUN-023-④")],
)
def test_console_carries_submitted_section_marks(console: str, mark: str, requirement: str):
    """제출본(RFP 구현현황)이 근거란에서 카드를 `admin.html §N`·`§E`·`§T` 로 지목한다.

    2026-08-24 실측: 그 표기가 화면에 0건이라 감리에서 "§N 이 어디냐"에 짚을 수 없었다.
    """
    assert f'>{mark}<' in console, f"{requirement}: 제출본이 지목하는 {mark} 좌표가 화면에 없습니다."


def test_console_shows_precision_and_recall(console: str):
    """FUN-024-① — 정밀도·재현율은 API에만 있으면 안 되고 화면 지표 카드에 떠야 한다."""
    assert "precision_macro" in console and "recall_macro" in console
    assert "정밀도" in console and "재현율" in console


def test_console_model_learning_and_release_uses_evidence_scoped_metrics(console: str):
    """FUN-004·024 모델 화면은 학습, 평가, Active Learning 상태를 보여 준다."""
    for marker in (
        'class="card release-card model-card"',
        '모델 학습 및 릴리스 관리',
        '데이터 분할',
        '모델 학습에 사용',
        '학습 중 점검에 사용',
        '최종 성능 확인에 사용',
        '학습 반영 대기',
        'id="active-learning-status"',
        'function loadActiveLearning()',
        '모델 학습 · 성능 평가 · 재학습 이력',
        'F1 점수',
        '고등급 미탐률',
        '평가 데이터',
        '평가 표본 수',
        'metric_timestamp_source',
        'fnr_high',
        'id="metrics-context"',
    ):
        assert marker in console
    for marker in (
        'id="deploy-adv"',
        'function activateModel()',
        'function rollbackModel()',
        'function reloadModel()',
    ):
        assert marker in console


def test_console_model_cards_do_not_use_a_top_accent_band(console: str):
    """모델 학습·평가 카드는 굵고 둥근 상단 띠로 구분하지 않는다."""
    assert ".model-card{border-top:1px solid var(--border-mid);}" in console
    assert ".model-card{border-top:3px solid var(--ui-navy);}" not in console


def test_console_shows_operating_state_but_keeps_connection_controls_out_of_settings(console: str):
    for marker in (
        'class="connection-summary"',
        'id="settings-health-state"',
        'class="settings-stack"',
        'class="settings-section" aria-labelledby="settings-connection-title"',
        'class="settings-section settings-maintenance" data-tech',
        '현재 연결',
        '시연 데이터 정리',
        'data-pane="settings" data-product-view="settings"',
        'productSetLegacyVisibility(PRODUCT_VIEW)',
        'id="training-coverage"',
        '데이터 담당자용',
    ):
        assert marker in console
    assert "학습셋 분포·검토 후보" not in console
    assert "d.caveat" not in console


def test_console_keeps_training_coverage_out_of_synthesis_review(console: str):
    """분포 공백은 데이터 담당자용 분석이며 합성 검토 큐의 상태가 아니다."""
    synth_start = console.index('data-product-view="synthesis"')
    model_start = console.index('id="model-training"')
    active_learning_start = console.index('<!-- ===== Active Learning =====')

    synthesis_card = console[synth_start:model_start]
    model_training_card = console[model_start:active_learning_start]

    assert 'id="sy-coverage"' not in synthesis_card
    assert 'id="training-coverage"' in model_training_card
    assert '데이터 담당자용' in model_training_card
    assert "productShowView('synthesis');" in console


def test_console_exposes_regulation_rag_switches_when_the_feature_is_installed(console: str):
    """운영 설정에서 규정 RAG 표시와 로컬 LLM 정밀선택을 각각 제어할 수 있어야 한다."""
    for marker in (
        "사내 규정 참고 RAG",
        "규정 참고 RAG 사용",
        'id="reg-runtime-toggle"',
        'id="reg-llm-toggle"',
        "'/regulations/runtime-toggle'",
        "'/regulations/llm-select-toggle'",
    ):
        assert marker in console


def test_console_does_not_offer_a_force_or_code_set_change(console: str):
    """9/20 4등급 고정: 화면에 추가·삭제·force 우회가 남으면 API 보호를 다시 무력화한다."""
    assert "등급 행 추가" not in console
    assert "removeGradeRow" not in console
    assert "force_reason" not in console
    assert "TS · S1 · S2 · S3 네 코드는 정책상 고정" in console


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


def test_grade_put_handler_returns_fixed_metadata_signal(monkeypatch):
    from koipa.api import schema_admin as sa
    from koipa.schemas.schema_admin import GradesPutRequest, GradesPutResponse

    monkeypatch.setattr(
        sa.SchemaAdminService, "put",
        lambda self, req: GradesPutResponse(
            version="v1", requires_retraining=False, reason="fixed grade metadata updated"
        ),
    )
    resp = sa.put_grades(
        GradesPutRequest(
            grades=[GradeDefinition(code="TS", name="특급기밀", order=1)],
            actor=Actor(user_id="console", role="admin"),
        )
    )
    assert resp.requires_retraining is False and "fixed" in resp.reason


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


def test_console_v2_product_shell_keeps_the_operational_views_and_real_api_boundaries(console: str):
    """관리자 콘솔 V2는 새 서버 계약을 만들지 않고 기존 후보 API를 작업대·지표에 연결한다.

    제품 셸을 CSS 시안으로만 남기면 실제 배포본에서 구역이나 실패 상태가 빠질 수 있어,
    운영자가 쓰는 일곱 메뉴·문서유형 지표·원본 문서 작업대의 앵커를 정적으로 잠근다.
    """
    for marker in (
        'data-product-nav="overview"',
        'data-product-nav="documents"',
        'data-product-nav="experts"',
        'data-product-nav="policy"',
        'data-product-nav="models"',
        'data-product-nav="audit"',
        'data-product-nav="settings"',
        'id="product-category-body"',
        'id="document-workbench"',
        'id="product-experts"',
        'progress_summary',
        'expert-progress-stats',
        'data-product-requires-golden="1"',
        'id="overview-help"',
        'data-resize="left"',
        'data-resize="right"',
        "applyProductReviewBatch(new URLSearchParams({limit:'2000'}))",
        "/golden/candidates/${encodeURIComponent(docId)}",
        'function productShowView(',
        'function loadProductOverview(',
        'function loadWorkbench(',
        'function saveWorkbenchDecision(',
        'function renderWorkbenchBody(',
        'function loadExpertOperations(',
        'function productGoldenReviewAvailable()',
        "document.dispatchEvent(new Event('koipa:profile-ready'))",
    ):
        assert marker in console
    for wording in (
        "문서 검토 및 확정",
        "검수 계정별 진행 현황",
        "판정",
        "미처리",
        "최근 활동",
        "분류 정책 및 근거",
        "모델 학습 및 평가",
        "감사 기록",
        "운영 설정",
    ):
        assert wording in console
    for removed_wording in ("이중검토 보류", "평가 정답지 준비도", "안전 경보"):
        assert removed_wording not in console
    assert "전문가 검수 운영" not in console
    assert "배포 준비 상태" not in console
    for removed_control in (
        'id="expert-reviewer-ids"',
        'id="expert-reviewer-roster"',
        'id="expert-plan-submit"',
        'function submitBalancedReviewPlan(',
        'function loadExpertReviewerRoster(',
        "'/golden/assignments/expert-review-plan'",
    ):
        assert removed_control not in console
    assert "안전 정지" not in console
    assert 'href="../api/v1/golden/candidates/login.html"' not in console


def test_console_document_screen_filters_by_review_status(console: str):
    """통일된 개요와 별개로 문서 검토 화면에서 상태별 재조회가 가능해야 한다."""
    assert 'id="wb-status" onchange="applyWorkbenchStatusFilter()"' in console
    for option in (
        '<option value="fixed">검수완료</option>',
        '<option value="unfixed">미검수</option>',
        '<option value="deferred">보류</option>',
        '<option value="discarded">폐기</option>',
        '<option value="out_of_scope">검수대상제외</option>',
    ):
        assert option in console
    assert "function workbenchStatusMatches(row,filter)" in console
    assert "function applyWorkbenchStatusFilter()" in console


def test_console_uses_the_batch_selector_without_a_duplicate_batch_stamp(console: str):
    """선택한 검수 배치는 필터로 남기되, 개요 카드에 같은 이름을 다시 넣지 않는다."""
    assert 'id="product-review-batch"' in console
    assert "function applyProductReviewBatch(" in console
    assert "distribution-stamp" not in console


def test_console_does_not_use_the_undefined_jargon_workbench(console: str):
    """"작업대"는 이 화면 어디에도 정의가 없는 조어였다(2026-10-11 사용자 지적).

    같은 화면이 이미 "문서 검토 화면"(product-documents 제목과 같은 말)이라는 쉬운 이름을
    쓰고 있어, 버튼·안내문만 다른 낱말을 쓰던 것을 그 이름으로 통일한다.
    """
    assert "작업대" not in console
    assert "문서 검토 화면 열기" in console


def test_console_mobile_header_wraps_the_navigation(console: str):
    """320px 폭에서도 상단 링크가 본문을 가로질러 잘리지 않게 두 줄 헤더로 전환한다."""
    assert "@media(max-width:640px)" in console
    assert ".top{height:auto;min-height:58px" in console
    assert ".top .cnav{order:3;width:100%;overflow-x:auto" in console


def test_console_uses_synth_capability_and_keeps_debug_log_collapsed(console: str):
    """없는 /synth router를 profile 추정으로 두드리지 않고, 기술 로그도 기본 화면을 가리지 않는다."""
    assert 'data-capability="synthesis-console"' in console
    assert "synthesis_console_enabled" in console
    assert "function synthConsoleAvailable()" in console
    assert '<div class="logbar-body collapsed" id="logbody"' in console
    assert 'aria-expanded="false"' in console


def test_console_keeps_mock_document_generation_as_a_dedicated_menu(console: str):
    """모의 문서 생성·검토는 모델 릴리스 화면에 묻히지 않는 필수 운영 기능이다."""
    assert 'data-product-nav="synthesis"' in console
    assert "모의 문서 생성·검토" in console
    assert 'data-product-view="synthesis"' in console
    assert "'synthesis'" in console
    assert "if(view==='synthesis' && synthConsoleAvailable())" in console
