"""합성 생성 게이트가 등급 표기를 실제로 잡는가 — 한국어까지.

왜 이 시험이 있는가(2026-09-05). 게이트는 검수 후보에서 등급 노출을 0 으로 요구한다
(allow_grade_token=False). 검수자가 답을 보고 읽으면 검수가 검증이 아니라 확인 절차가
되기 때문이다. 그런데 검사식이 ``\b(TS|S1|S2|S3)\b`` 하나뿐이라 **생성기가 실제로 쓰는
한국어 표기를 하나도 못 잡았다.**

    프롬프트가 금지하는 것   12종 (TS·S1·S2·S3·특급기밀·1급 비밀·2급·3급·대외비·기밀·비밀·극비)
    게이트가 검사하던 것      4종

실측(datasets/rag_corpus_v2 720건):

    등급 표기가 있는 문서    569 (79.0%)
    고치기 전 게이트가 잡음   192 (26.7%)
    놓치던 것                377 (52.4%)   — 오검출 0, 전부 실제 선언문이었다
                                            "본 문서는 [가상기업A]의 1급 비밀로 분류된 자료입니다"

이제 게이트는 generator.FORBIDDEN_GRADE_TERMS 를 읽는다 — 프롬프트가 금지하는 목록과
게이트가 검사하는 목록이 갈라지지 않게 한 것이 이 수정의 핵심이다.
"""
from __future__ import annotations

from koipa.modules.m1_synthesis.generator import FORBIDDEN_GRADE_TERMS
from koipa.services.synth_quality import _exposes_grade_token, screen_batch


def test_korean_grade_expressions_are_caught():
    """생성기가 실제로 쓰던 표기 — 고치기 전에는 전부 놓쳤다."""
    for text in (
        "본 문서는 [가상기업A]의 1급 비밀로 분류된 자료입니다.",
        "본 문서는 [가상기업A]의 특급기밀 수준의 정보를 포함하고 있습니다.",
        "본 자료는 대외비이며 외부 공유를 금지합니다.",
        "2024년도 신규 사업 확장 전략 (1급 비밀)",
        "이 문서는 극비 등급으로 관리한다.",
    ):
        assert _exposes_grade_token(text), text


def test_english_grade_expressions_are_caught():
    for text in (
        "This document is material for [Company A] classified as Level 1 Secret.",
        "The attached plan is Top Secret and must not be shared.",
    ):
        assert _exposes_grade_token(text), text


def test_grade_codes_still_caught():
    """옛 검사식이 잡던 것은 그대로 잡는다(회귀 방지)."""
    assert _exposes_grade_token("본 문서의 등급은 S1 이다.")
    assert _exposes_grade_token("grade: TS")


def test_ascii_codes_require_word_boundary():
    """영문 약어는 낱말 경계를 요구한다 — 모델명에 걸리면 안 된다."""
    for text in (
        "장비 모델 PS100 과 TS200 을 비교한다.",
        "부품 번호 XS1234 의 수율을 확인했다.",
    ):
        assert not _exposes_grade_token(text), text


def test_ordinary_business_text_passes():
    """등급을 말하지 않는 본문은 통과한다 — 게이트가 전부 막으면 쓸모가 없다."""
    for text in (
        "원가 구조와 수율 개선 방안을 정리한 내부 검토 자료이다.",
        "협력사 단가 협상 경과와 후속 조치를 기록한다.",
        "2024년 3분기 설비 투자 계획과 집행 실적을 대조한다.",
    ):
        assert not _exposes_grade_token(text), text


def test_gate_vocabulary_comes_from_the_generator():
    """프롬프트가 금지하는 목록과 게이트가 검사하는 목록이 같아야 한다.

    갈라지면 이번 결함이 그대로 재현된다 — 프롬프트는 12종을 금지하는데 게이트는 4종만 봤다.
    """
    assert "1급 비밀" in FORBIDDEN_GRADE_TERMS
    assert "특급기밀" in FORBIDDEN_GRADE_TERMS
    assert "대외비" in FORBIDDEN_GRADE_TERMS
    for term in FORBIDDEN_GRADE_TERMS:
        assert _exposes_grade_token("앞말 %s 뒷말" % term), term


def test_batch_flags_exposed_documents_without_dropping_them():
    """걸린 문서는 flagged 로 돌려주고 버리지 않는다 — 생성 비용은 이미 들었다."""
    docs = [
        ("S1", "본 문서는 [가상기업A]의 1급 비밀로 분류된 자료입니다. " * 4),
        ("S2", "협력사 단가 협상 경과와 후속 조치를 정리한 문서이다. " * 4),
    ]
    res = screen_batch(docs)
    assert res["admit"] == [1], res
    assert [f["reason"] for f in res["flagged"]] == ["grade_token_exposed"]
    assert res["flagged"][0]["index"] == 0


# ── 골든 콘솔 품질 지표 — 출처별로 어휘가 다르다 (2026-09-05) ────────────────
#
# 같은 정규식이 두 벌 있었다(dataset_leakage · proxy_gold_candidate_service). 두 벌이면
# 다음에 한쪽만 고쳐진다 — 오늘 고친 결함 셋이 전부 그 모양이었다.
#
# 그런데 콘솔 쪽은 **넓히면 안 되는 자리**다. 이 서비스는 실문서도 다루고
# (public_real · organization_real), 실문서에 찍힌 "대외비"는 검수자가 봐야 하는 문서의
# 일부이며 비밀관리성(M) 판단의 근거다(rule_engine._MANAGEMENT_MARKING_TERMS 가 점수로 쓴다).
# 생성기가 지어낸 것과 원본에 찍혀 있던 것은 성격이 다르다.

from koipa.services.proxy_gold_candidate_service import _exposes_grade


def test_synthetic_candidate_uses_the_broad_vocabulary():
    """합성 후보에서는 프롬프트가 금지한 표기 전부가 노출이다."""
    for text in (
        "본 문서는 [가상기업A]의 1급 비밀로 분류된 자료입니다.",
        "본 자료는 대외비이며 외부 공유를 금지합니다.",
        "This document is classified as Level 1 Secret.",
    ):
        assert _exposes_grade(text, is_real=False), text


def test_real_document_marking_is_not_treated_as_exposure():
    """실문서의 보안표시는 노출이 아니다 — 그것이 곧 비밀관리성 근거다."""
    for text in (
        "본 문서는 대외비로 지정되어 관계자 외 열람을 제한한다.",
        "표지에 극비 표기가 있으며 지정된 인원만 열람한다.",
    ):
        assert not _exposes_grade(text, is_real=True), text
        # 같은 문장이 합성 후보에서 나오면 생성기가 금지를 어긴 것이다.
        assert _exposes_grade(text, is_real=False), text


def test_grade_code_is_exposure_in_both():
    """등급 코드는 어느 쪽에서도 노출이다 — 실문서에 있으면 라벨이 새어 든 것이다."""
    for text in ("이 문서의 등급은 S1 이다.", "grade: TS"):
        assert _exposes_grade(text, is_real=True), text
        assert _exposes_grade(text, is_real=False), text


def test_ordinary_text_passes_in_both():
    text = "원가 구조와 수율 개선 방안을 정리한 검토 자료이다."
    assert not _exposes_grade(text, is_real=True)
    assert not _exposes_grade(text, is_real=False)


# ── 전각 문자 (2026-09-05) ──────────────────────────────────────────────────
#
# 한국 공문서·구형 한글 문서에는 전각 알파벳·숫자가 섞인다. 전처리
# (m2_preprocess/normalizer.py)는 NFKC 로 접으므로 **분류 경로는 안전한데**, 이 게이트는
# 정규화 **전** 원문에 돈다(생성 직후·검수큐 적재 전). 그래서 전각을 하나도 못 잡았다:
#
#     본 문서의 등급은 TS 이다.     잡힘
#     본 문서의 등급은 ＴＳ 이다.    **놓침**  (U+FF34 U+FF33)
#     본 문서는 １급 비밀이다.       **놓침**  (U+FF11)
#
# ⚠ 접은 결과는 검사에만 쓴다 — 검수자가 읽는 것은 원문이어야 하므로 본문은 바꾸지 않는다.

def test_fullwidth_grade_code_is_caught():
    """전각 알파벳으로 쓴 등급 코드도 잡는다."""
    assert _exposes_grade_token("본 문서의 등급은 \uff34\uff33 이다.")      # ＴＳ
    assert _exposes_grade_token("문서 등급 \uff33\uff11 로 분류함.")        # Ｓ１


def test_fullwidth_korean_grade_is_caught_for_synthetic():
    """전각 숫자로 쓴 한국어 등급 표기도 합성 후보에서는 노출이다."""
    assert _exposes_grade_token("본 문서는 \uff11급 비밀 자료이다.")        # １급


def test_folding_does_not_widen_the_real_document_vocabulary():
    """실문서 경로는 접기만 하고 **어휘는 그대로** — 보안표시는 여전히 노출이 아니다.

    실문서에 찍힌 '1급 비밀'은 비밀관리성(M) 판단의 근거지 답 노출이 아니다. 전각을 접는
    것이 그 구분까지 흐리면 안 된다.
    """
    from koipa.services.proxy_gold_candidate_service import _exposes_grade

    assert not _exposes_grade("본 문서는 \uff11급 비밀 자료이다.", is_real=True)
    assert not _exposes_grade("본 문서는 1급 비밀 자료이다.", is_real=True)
    # 등급 코드는 전각이든 반각이든 실문서에서도 노출이다(라벨이 샌 것).
    assert _exposes_grade("등급 \uff34\uff33 로 분류.", is_real=True)
    assert _exposes_grade("등급 TS 로 분류.", is_real=True)


def test_folding_does_not_change_the_text():
    """본문을 고치지 않는다 — 검수자가 읽는 것은 원문이다."""
    from koipa.services.synth_quality import _fold_for_match

    src = "본 문서는 \uff11급 비밀이다."
    assert _fold_for_match(src) != src, "검사용으로는 접힌다"
    assert "\uff11" in src, "원본 문자열은 그대로 남는다"
