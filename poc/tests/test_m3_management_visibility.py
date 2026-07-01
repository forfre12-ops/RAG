"""[M축 가시화] 비밀관리성(M) 독립 근거 유무 노출 — 등급 무변경 검증.

배경(전수조사 P1): 룰 엔진 M축은 관리표시가 MANAGEMENT 팩터가 아니라 LEAK_IMPACT/NON_PUB
으로 태깅돼 콘텐츠등급에서 역산됐다(독립 근거 없음). 등급 보정(마킹→MANAGEMENT seed) 버전은
eval 에서 'S3+사외비→TS' 과분류가 드러나 폐기(rule_engine s=2·v=2 fiat 피드백). 대신 등급은
그대로 두고 M 이 독립 근거로 뒷받침되는지(관리표시/관리요소 매치)를 management_evidenced 플래그
+검수 경고로 노출한다. 본 테스트는 (1) 마킹 탐지 (2) 플래그/경고 (3) **등급 무변경**을 잠근다.
"""

from __future__ import annotations

from lloydk.modules.m3_labeling.rule_engine import (
    LabelRuleEngine,
    detect_management_marking,
)

_M_WARN = "비밀관리성(M) 독립 근거"


def _g(result):
    return result.grade.value if hasattr(result.grade, "value") else str(result.grade)


def _has_m_warn(result) -> bool:
    return any(_M_WARN in w for w in result.warnings)


def test_detect_management_marking_helper():
    assert detect_management_marking("본 문서는 대외비 자료입니다")
    assert detect_management_marking("사외비 · 취급주의")
    assert detect_management_marking("This document is CONFIDENTIAL")  # 대소문자 무시
    assert not detect_management_marking("홈페이지 게시 공개 안내문")
    assert not detect_management_marking("")


def test_marking_sets_evidenced_no_warning():
    eng = LabelRuleEngine()
    r = eng.label("사외비 신규 사업 추진 계획 내부 검토")
    assert r.management_evidenced is True
    assert not _has_m_warn(r)  # 근거 있으면 M 경고 없음


def test_no_marking_non_s3_gets_warning():
    eng = LabelRuleEngine()
    r = eng.label("신규 사업 추진 계획 내부 검토 자료")
    assert r.grade  # 등급은 산출됨
    if _g(r) != "S3":
        assert r.management_evidenced is False
        assert _has_m_warn(r)  # 근거 없고 S3 아니면 검수 경고


def test_s3_no_m_warning_even_if_not_evidenced():
    eng = LabelRuleEngine()
    r = eng.label("홈페이지에 게시된 공개 채용 공고 안내문")
    assert _g(r) == "S3"
    assert not _has_m_warn(r)  # S3 는 M 경고 대상 아님(공개=관리성 무관)


def test_management_factor_seed_sets_evidenced():
    # MANAGEMENT_LEVEL 요소 시드(암호화 키류) 매치 → 관리표시 없이도 evidenced.
    eng = LabelRuleEngine()
    r = eng.label("암호화 알고리즘 키 마스터 키 관리 대장")
    assert r.management_evidenced is True


def test_marking_does_not_force_ts_grade():
    """핵심 회귀 잠금: 관리표시가 등급을 TS 로 강제 상향하지 않는다(폐기된 버전의 버그).

    'S3/S2 콘텐츠 + 사외비 → TS' 과분류가 재발하지 않아야 한다. 가시화는 등급 무영향.
    """
    eng = LabelRuleEngine()
    # 관리표시를 붙여도 콘텐츠가 TS 급이 아니면 TS 가 되면 안 된다.
    for content in [
        "사외비 신규 사업 추진 계획 내부 검토",
        "대외비 협력사 단가 협상 자료",
        "기밀 인사 평가 결과 명단",
    ]:
        r = eng.label(content)
        assert _g(r) != "TS", f"관리표시로 TS 과분류 재발: {content!r} → {_g(r)}"


def test_evidenced_flag_default_false_on_empty():
    eng = LabelRuleEngine()
    r = eng.label("")
    assert r.management_evidenced is False
    assert _g(r) == "S3"
