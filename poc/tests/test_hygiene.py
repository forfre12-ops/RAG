"""koipa.hygiene 위생 유틸 단위 테스트 — 정규화·텍스트해시·누출탐지·홀드아웃 분리.

정본 정규화 표준(공백 전부 제거 + 대소문자 보존)과 build_p1 분리 알고리즘의 동작을 고정한다.
"""
from koipa.hygiene import (
    find_leaked,
    normalize_text,
    stratified_holdout_split,
    text_hash,
)


def test_normalize_default_removes_all_whitespace_keeps_case():
    assert normalize_text("A  B\tC\nD") == "ABCD"
    assert normalize_text("Abc DEF") == "AbcDEF"  # 대소문자 보존
    assert normalize_text(None) == ""


def test_normalize_keep_spaces_and_lower_options():
    assert normalize_text("A  B\tC", keep_spaces=True) == "A B C"
    assert normalize_text("  A B  ", keep_spaces=True) == "A B"
    assert normalize_text("ABC", lower=True) == "abc"


def test_text_hash_ignores_whitespace_differences():
    # 공백만 다른 동일 본문 → 같은 해시 (raw 해시였다면 달랐을 것)
    assert text_hash("기밀 문서 입니다") == text_hash("기밀문서입니다")
    assert text_hash("a b c") == text_hash("abc")


def test_text_hash_case_sensitive_by_default():
    assert text_hash("ABC") != text_hash("abc")
    assert text_hash("ABC", lower=True) == text_hash("abc", lower=True)


def test_find_leaked_catches_whitespace_variant_with_different_doc_id():
    train = [{"doc_id": "t1", "text": "영업비밀 자료"}]
    eval_ = [
        {"doc_id": "e1", "text": "영업비밀자료"},      # 공백만 다름 → 누출
        {"doc_id": "e2", "text": "완전히 다른 문서"},   # 무관
    ]
    leaked = find_leaked(eval_, train)
    assert [r["doc_id"] for r in leaked] == ["e1"]


def test_stratified_holdout_split_is_deterministic_and_no_straddle():
    rows = [
        {"text": f"S1 문서 {i}", "label_source": "llm", "label": "S1"} for i in range(10)
    ] + [
        {"text": f"S3 문서 {i}", "label_source": "llm", "label": "S3"} for i in range(10)
    ]
    sk = lambda r: (r["label_source"], r["label"])
    h1 = stratified_holdout_split(rows, frac=0.3, strata_key=sk)
    h2 = stratified_holdout_split(rows, frac=0.3, strata_key=sk)
    assert h1 == h2                      # 결정론적
    assert len(h1) == 6                  # 등급당 round(10*0.3)=3, 2등급 → 6


def test_stratified_holdout_identical_text_groups_to_one_key():
    # 동일 본문(공백만 다름)은 하나의 키로 묶여 양쪽 분할에 걸치지 않음
    rows = [
        {"text": "같은 문서", "label_source": "llm", "label": "S1"},
        {"text": "같은문서", "label_source": "llm", "label": "S1"},
        {"text": "다른 문서", "label_source": "llm", "label": "S1"},
    ]
    keys = stratified_holdout_split(rows, frac=0.5, strata_key=lambda r: r["label"])
    # 정규화 텍스트키는 2개("같은문서","다른문서") → round(2*0.5)=1건 홀드아웃
    assert len(keys) == 1
    assert keys <= {"같은문서", "다른문서"}
