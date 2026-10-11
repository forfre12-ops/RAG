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
