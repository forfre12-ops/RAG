from pathlib import Path


def test_distribution_scope_buttons_show_selected_scope_metrics():
    """각 분포 범위 선택은 숫자만 바꾸지 않고 선택 범위 지표를 남긴다."""
    console = Path("poc/src/koipa/api/static/admin.html").read_text(encoding="utf-8")

    assert "const scopeSummary=document.createElement('section')" in console
    assert "scopeSummary.setAttribute('aria-live','polite')" in console
    assert "addMetric(label+' 문서',scopeTotal)" in console
    assert "addMetric('등급 지정',graded)" in console
    assert "addMetric('등급 미지정',missing,'distribution-scope-missing')" in console
    assert "전체 검수 완료율" in console
    assert "button.onclick=()=>renderScope(scope)" in console


def test_model_evaluation_uses_plain_language_for_grade_comparison():
    """관리 화면에는 내부 ML 용어 대신 표의 의미를 직접 설명한다."""
    console = Path("poc/src/koipa/api/static/admin.html").read_text(encoding="utf-8")

    assert ">실제 등급 · 모델 예측 비교<" in console
    assert "평가 기준 등급과 모델 예측 등급을 한 표에서 비교합니다." in console
    assert "실제 등급 · 모델 예측 비교 — 모델" in console
    assert "오분류 표(혼동행렬)" not in console


def test_missing_active_model_uses_an_empty_state_not_a_warning():
    """활성 모델이 없는 404는 정상 초기 상태로 안내한다."""
    console = Path("poc/src/koipa/api/static/admin.html").read_text(encoding="utf-8")

    assert "class=\"model-evaluation-empty\"" in console
    assert "아직 평가 결과가 없습니다" in console
    assert "현재는 활성 모델이 없어 표시할 평가 결과가 없습니다." in console
    assert "if(e&&e.status===404){" in console
