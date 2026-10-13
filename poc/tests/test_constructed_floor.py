"""constructed_floor 결정적 검증기 — 2026-07-03 적대검증에서 확인된 공격 시나리오의 회귀 가드.

핵심 계약:
  - floor 전용(exact 아님): 통과해도 grade_floor만 주장
  - 공격 갭 수정: 전 출현 스캔(첫 출현만 ❌) + 문단 공개선언(20자 윈도 ❌)
  - 상위등급 negative scan: S2 의도에 TS 어휘 → 탈락 (룰시드 veto 단방향 재사용)
  - 권위 계층화: witness basis 필수, TS는 외부 열거 인용 필수
  - lexicon disjoint: witness 표면형이 학습 코퍼스에 있으면 offender
"""
from __future__ import annotations

from koipa.modules.m6_evaluation.constructed_floor import (
    TIER_CONSTRUCTED_FLOOR,
    check_witness,
    evaluate_constructed_floor,
    find_occurrences,
    upper_grade_negative_scan,
    witness_lexicon_disjoint,
)

# 테스트 전용 시드(실 KEYWORD_SEEDS 대신 주입 — 결정적·의존 최소화)
SEEDS = [
    {"grade": "TS", "keyword": "극비", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "국가핵심기술 지정", "weight": 1.0, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "핵심 영업비밀", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "대외비", "weight": 0.8, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S3", "keyword": "보도자료", "weight": 0.5, "factor": "NON_PUBLICITY"},
]

BODY_OK = (
    "내부 검토 자료\n\n"
    "당사 신제품의 원가 구조는 부품당 1,250원이며 협력사별 마진 테이블을 포함한다.\n"
    "이 정보는 사내 결재권자 승인 없이 외부 제공을 금지한다.\n"
)


def _rec(text=BODY_OK, grade="S2", witnesses=None):
    return {
        "text": text,
        "intended_grade": grade,
        "witnesses": witnesses if witnesses is not None
        else [{"token": "부품당 1,250원", "basis": "가이드 v2.2 §경제적유용성 — 원가정보"}],
    }


def test_happy_path_admits_floor_only():
    v = evaluate_constructed_floor(_rec(), seeds=SEEDS)
    assert v.admitted and v.grade_floor == "S2"
    assert v.label_kind == "floor"                      # exact 주장 없음
    assert "constructed_floor" in v.truth_warning
    assert TIER_CONSTRUCTED_FLOOR == "constructed_floor_eval"  # locked와 절대 혼동 금지


def test_all_occurrences_scanned_not_just_first():
    # 첫 출현은 부정 문맥("해당 없"), 둘째 출현은 긍정 — 사실은 존재한다.
    text = (
        "구모델에는 부품당 1,250원 원가가 해당 없음.\n\n"
        "신모델의 2026년 상반기 양산 라인 기준으로 산정한 실제 원가는 부품당 1,250원으로 확정되었다."
    )
    chk = check_witness(text, "부품당 1,250원")
    assert chk.occurrences == 2
    assert chk.present is True          # 구 check_fact_preservation(첫 출현만)이면 negated로 오판


def test_paragraph_public_declaration_breaks_nonpublicity():
    # 공격 시나리오 그대로: 20자 윈도 밖의 장거리 공개선언 — 문단 스캔이 잡아야 한다.
    text = (
        "아래 항목은 이미 특허 공개공보에 게재된 내용이므로 참고용으로만 첨부한다. "
        "관련 상세 수치를 포함하면 다음과 같다: 부품당 1,250원 원가 구조.\n\n"
        "기타 일반 사항은 별도 문서 참조."
    )
    chk = check_witness(text, "부품당 1,250원")
    assert chk.publicity_broken is True
    v = evaluate_constructed_floor(_rec(text=text), seeds=SEEDS)
    assert not v.admitted
    assert any("publicity_broken" in r for r in v.reasons)


def test_upper_grade_contamination_rejected():
    # S2 의도 문서에 TS 어휘(극비) 혼입 → 상향오염 결정적 차단.
    text = BODY_OK + "\n한편 본 건은 극비 프로젝트와 연계된다.\n"
    hits = upper_grade_negative_scan(text, "S2", seeds=SEEDS)
    assert "극비" in hits
    v = evaluate_constructed_floor(_rec(text=text), seeds=SEEDS)
    assert not v.admitted
    assert any("upper_grade_contamination" in r for r in v.reasons)


def test_ts_requires_external_basis():
    ts_text = "본 문서는 반도체 식각 공정 파라미터 원본 레시피를 담는다.\n\n상세 수치 생략."
    w_internal = [{"token": "식각 공정 파라미터", "basis": "자사 가이드 v2.2"}]
    v = evaluate_constructed_floor(
        {"text": ts_text, "intended_grade": "TS", "witnesses": w_internal}, seeds=SEEDS
    )
    assert not v.admitted and any("ts_basis_not_external" in r for r in v.reasons)
    w_external = [{"token": "식각 공정 파라미터",
                   "basis": "산업기술보호법 §9 · 산업부 고시 반도체 1호"}]
    v2 = evaluate_constructed_floor(
        {"text": ts_text, "intended_grade": "TS", "witnesses": w_external}, seeds=SEEDS
    )
    assert v2.admitted and v2.grade_floor == "TS"


def test_no_witness_or_no_basis_rejected():
    v = evaluate_constructed_floor(_rec(witnesses=[]), seeds=SEEDS)
    assert not v.admitted and any("no_witnesses" in r for r in v.reasons)
    v2 = evaluate_constructed_floor(
        _rec(witnesses=[{"token": "부품당 1,250원", "basis": ""}]), seeds=SEEDS
    )
    assert not v2.admitted and any("witness_no_basis" in r for r in v2.reasons)


def test_witness_lexicon_disjoint_guard():
    offenders = witness_lexicon_disjoint(
        ["부품당 1,250원", "미출현 토큰"],
        ["학습 문서 A: 부품당 1,250원 언급", "학습 문서 B"],
    )
    assert offenders == [{"token": "부품당 1,250원", "train_hits": 1}]
    assert witness_lexicon_disjoint(["미출현 토큰"], ["학습 문서"]) == []


def test_find_occurrences_all():
    assert find_occurrences("aXbXc", "X") == [1, 3]
    assert find_occurrences("aaa", "aa") == [0, 1]  # 겹침 포함 전 출현
    assert find_occurrences("abc", "") == []
