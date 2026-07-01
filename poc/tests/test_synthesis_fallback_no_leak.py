"""[모델품질] noop fallback 본문의 등급 신호 누출 차단.

과거 _fallback_body는 GRADE_SITUATION_PROMPTS의 situation/disclosure_scope를 본문에 직접
넣어 강한 등급 마커가 합성 코퍼스에 누출됐다. fallback이 학습에 새면 룰·모델이 실데이터에
없는 합성 단서에 과적합한다. 본문은 grade-중립 placeholder여야 한다(모든 등급에서 동일).
"""

from __future__ import annotations

from lloydk.modules.m1_synthesis.generator import (
    GRADE_SITUATION_PROMPTS,
    _fallback_body,
)

# 등급을 강하게 식별하는 마커 텍스트(과거 누출 소스). 본문에 없어야 한다.
_GRADE_MARKERS = [
    "외부 공유 절대 불가", "회사 존립", "유출 시", "C-level", "영업비밀 수준",
    "경쟁우위", "협상력", "방산 기밀", "암호 키",
]


def test_fallback_body_is_grade_neutral_across_grades():
    # 모든 등급에서 본문이 동일 → 등급 신호 0.
    bodies = {g: _fallback_body(g, "반도체") for g in ("TS", "S1", "S2", "S3")}
    assert len(set(bodies.values())) == 1, "fallback 본문이 등급별로 달라 신호 누출"


def test_fallback_body_has_no_grade_markers():
    for g in ("TS", "S1", "S2", "S3"):
        body = _fallback_body(g, "재무")
        for marker in _GRADE_MARKERS:
            assert marker not in body, f"{g} fallback에 등급 마커 누출: {marker!r}"


def test_fallback_body_excludes_situation_disclosure_text():
    # GRADE_SITUATION_PROMPTS의 situation/disclosure_scope/harm_potential 원문이 본문에 없어야.
    for g in ("TS", "S1"):
        body = _fallback_body(g, "계약")
        sit = GRADE_SITUATION_PROMPTS[g]
        assert sit["situation"] not in body
        assert sit["disclosure_scope"] not in body
        assert sit["harm_potential"] not in body


def test_fallback_body_still_includes_domain():
    # 도메인 placeholder는 유지(파이프라인 연결 테스트 식별용) — 등급과 무관.
    assert "반도체" in _fallback_body("S2", "반도체")
