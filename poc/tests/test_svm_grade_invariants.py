"""SVM 룰 등급산출 불변식 — 판정식·M 매핑·label() 경로를 전수로 못박는다.

왜 이 파일이 필요한가(2026-08-29). "룰 등급산출에 버그 없나"라는 질문에 그동안
개별 사례로만 답해 왔다. 사례는 다음에 누가 판정식을 건드리면 그대로 통과한다.
여기서는 **입력 공간을 전부 돌려** 명세와 대조하고, 코퍼스 전체에 불변식을 건다.

세 층으로 나눈다.

    A  순수 판정식      grade_from_svm 27조합 · 가이드 워크드 예시 · 역산표 왕복
    B  ICD M 매핑       보안표시 x 접근범위 전조합의 3상태 계약
    C  label() 경로     엣지 입력 · 코퍼스 불변식 · FNR-safe 방향 · 결정성
    D  현행 동작 고정   곱셈 블록이 실제로 등급을 바꾸는 조합을 명시적으로 적어 둔다

D 를 두는 이유. 곱셈 블록의 S/V/M 은 요소 근거가 아니라 content_grade 에서 역산되므로
(`strong = content_grade in ("TS","S1")`), 도달 가능한 입력은 content_grade x public x
has_mgmt 16가지뿐이다. 그중 최종 등급이 content_grade 와 달라지는 것은 **2가지**이고
나머지 14가지에서 룰 등급은 키워드 argmax 와 같다. 이 사실이 바뀌면 판정면이 바뀐 것이니
테스트가 알려줘야 한다 — 좋다 나쁘다를 판정하는 것이 아니라 **변화를 드러내는** 것이 목적이다.
"""
from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

import pytest

from koipa.modules.m3_labeling.rule_engine import (
    LabelRuleEngine,
    grade_from_svm,
    grade_from_svm_floored,
    management_from_metadata,
    svm_levels_for_grade,
    validate_icd_metadata,
)
from koipa.modules.m3_labeling.seeds import GRADE_ORDER, KEYWORD_SEEDS, to_canonical_factor

LEVELS = (0, 1, 2)
GRADES = ("TS", "S1", "S2", "S3")


@pytest.fixture(scope="module")
def engine() -> LabelRuleEngine:
    # DB 를 타지 않는다 — 코드 시드로 고정해야 결과가 환경에 흔들리지 않는다.
    return LabelRuleEngine(seeds=KEYWORD_SEEDS)


# ── A. 순수 판정식 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("s,v,m", list(itertools.product(LEVELS, repeat=3)))
def test_grade_from_svm_matches_spec(s: int, v: int, m: int) -> None:
    """정본 v2.2: s==2 and v==2 면 곱>=4 TS 아니면 S1, 그 외 곱>=1 S2 아니면 S3."""
    product = s * v * m
    expected = ("TS" if product >= 4 else "S1") if (s == 2 and v == 2) else (
        "S2" if product >= 1 else "S3"
    )
    assert grade_from_svm(s, v, m) == expected


@pytest.mark.parametrize("s,v,m", list(itertools.product(LEVELS, repeat=3)))
def test_grade_never_relaxes_when_a_factor_rises(s: int, v: int, m: int) -> None:
    """어떤 축을 올렸는데 등급이 더 낮아지면(덜 심각해지면) 판정식이 뒤집힌 것이다."""
    base = GRADE_ORDER[grade_from_svm(s, v, m)]
    for ds, dv, dm in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
        nxt = (s + ds, v + dv, m + dm)
        if max(nxt) > 2:
            continue
        assert GRADE_ORDER[grade_from_svm(*nxt)] <= base, f"{(s, v, m)} -> {nxt}"


@pytest.mark.parametrize(
    "levels,expected,label",
    [
        ((1, 1, 1), "S2", "조직도"),
        ((0, 0, 0), "S3", "내선번호"),
        ((1, 2, 2), "S2", "인사평가보고서 — v2.2 보정(가이드 원본은 S1)"),
        ((2, 2, 2), "TS", "중장기 경영계획"),
        ((1, 1, 0), "S3", "사무실 배치도"),
    ],
)
def test_guide_worked_examples(levels, expected: str, label: str) -> None:
    """적용서 §1.2 가 싣고 있는 발주처 가이드 12p 워크드 예시."""
    assert grade_from_svm(*levels) == expected, label


@pytest.mark.parametrize("grade", GRADES)
def test_level_table_round_trips(grade: str) -> None:
    """등급 -> 표준 S/V/M -> 등급 이 제자리로 와야 표시가 모순되지 않는다."""
    assert grade_from_svm(*svm_levels_for_grade(grade)) == grade


def test_level_table_unknown_grade_falls_back_to_s2_levels() -> None:
    assert svm_levels_for_grade("ZZZ") == (1, 1, 1)


@pytest.mark.parametrize("s,v,m", list(itertools.product(LEVELS, repeat=3)))
def test_floored_wrapper_never_collapses_without_proof(s: int, v: int, m: int) -> None:
    """§3.6: 요소값 0 은 '입증'된 경우에만. 미입증이면 1 로 floor 되어 S3 붕괴가 없어야 한다."""
    assert grade_from_svm_floored(s, v, m) != "S3"


def test_floored_wrapper_allows_zero_when_proven() -> None:
    assert grade_from_svm_floored(
        0, 0, 0,
        secrecy_proven_absent=True, value_proven_absent=True, mgmt_proven_absent=True,
    ) == "S3"


# ── B. ICD §3.2·§3.3 M 매핑 ──────────────────────────────────────────────────
_MARKINGS = ["top_secret", "secret", "confidential", "none", "", None, "GARBAGE"]
_SCOPES = ["approved_only", "designated", "department", "all_employees", "", None, "GARBAGE"]


@pytest.mark.parametrize("mark,scope", list(itertools.product(_MARKINGS, _SCOPES)))
def test_management_state_contract(mark, scope) -> None:
    """3상태 계약 — present 는 1|2, proven_absent 는 0, unknown 은 None."""
    state, level, reason = management_from_metadata(mark, scope)
    assert state in ("present", "proven_absent", "unknown")
    assert reason
    if state == "present":
        assert level in (1, 2)
    elif state == "proven_absent":
        assert level == 0
    else:
        assert level is None


def test_security_marking_beats_access_scope() -> None:
    """표시가 있으면 접근범위는 보지 않는다 — 표시가 더 강한 근거다."""
    assert management_from_metadata("top_secret", "all_employees")[1] == 2


def test_all_employees_is_proven_absent_not_unknown() -> None:
    """전 임직원 열람은 '모름'이 아니라 관리성 미충족이 입증된 것이다."""
    assert management_from_metadata("none", "all_employees")[0] == "proven_absent"


def test_out_of_contract_values_are_unknown_not_guessed() -> None:
    assert management_from_metadata("GARBAGE", "GARBAGE")[0] == "unknown"


def test_validate_icd_metadata_warns_but_never_raises() -> None:
    assert validate_icd_metadata(
        {"source_type": "public", "security_marking": "secret", "access_scope": "designated"}
    ) == []
    assert validate_icd_metadata({"security_marking": "WRONG"})
    assert validate_icd_metadata("not a dict") == []
    assert validate_icd_metadata(None) == []


# ── C. label() 경로 불변식 ────────────────────────────────────────────────────
EDGE_INPUTS = [
    "", " ", "\n\n", "a", "가" * 5000, "!@#$%^&*()", "\x00\x01",
    "특급기밀" * 500, "😀" * 100, "<script>alert(1)</script>", "a\tb\rc",
]


@pytest.mark.parametrize("text", EDGE_INPUTS)
def test_label_survives_edge_inputs(engine: LabelRuleEngine, text: str) -> None:
    assert engine.label(text).grade in GRADE_ORDER


def _corpus() -> list[str]:
    """실문서가 있으면 쓰고, 없어도 결정론적 랜덤 코퍼스로 항상 돈다."""
    docs: list[str] = []
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "datasets/labeled_v7_diverse/val.jsonl",
        "datasets/gold_real/_rejudge_claude/holdout109_provenance_corrected.jsonl",
    ):
        path = root / rel
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                text = row.get("content") or row.get("text") or ""
                if text:
                    docs.append(text)
    rng = random.Random(20260829)
    vocab = "가나다라 abc 123 기밀 특급기밀 공개 보도자료 API draft 대외비 암호화 키 관리"
    docs += [
        "".join(rng.choice(vocab) for _ in range(rng.randint(1, 400)))
        for _ in range(200)
    ]
    return docs


def _content_grade(result) -> str:
    """곱셈 블록에 들어가기 전의 키워드 argmax 등급."""
    if result.total_score == 0:
        return "S3"
    top = max(result.grade_scores.values())
    return min(
        [g for g, v in result.grade_scores.items() if v == top],
        key=lambda g: GRADE_ORDER.get(g, 999),
    )


def test_corpus_invariants(engine: LabelRuleEngine) -> None:
    """코퍼스 전체에 대해 한 번도 깨지면 안 되는 것들."""
    violations: dict[str, int] = {}

    def bump(key: str) -> None:
        violations[key] = violations.get(key, 0) + 1

    for text in _corpus():
        result = engine.label(text)
        if result.grade not in GRADE_ORDER:
            bump("등급이 GRADE_ORDER 밖")
        if not 0.0 <= result.confidence <= 1.0:
            bump("conf 가 [0,1] 밖")
        if abs(sum(result.grade_scores.values()) - result.total_score) > 0.01:
            bump("grade_scores 합 != total_score")
        # 하향 금지 — FNR-safe 는 올리기만 한다.
        if GRADE_ORDER.get(result.grade, 99) > GRADE_ORDER.get(_content_grade(result), 99):
            bump("최종 등급이 콘텐츠 등급보다 낮아짐")
        factors = result.factor_scores
        svm = [factors.get(k) for k in ("SECRECY", "VALUE", "MANAGEMENT")]
        if None in svm:
            continue
        if any(x != x or x < 0 for x in svm):  # noqa: PLR0124 - NaN 검사
            bump("요소 점수가 음수/NaN")
        # 곱셈 블록을 탄 문서(요소가 0/1/2 레벨)는 표시와 등급이 반드시 정합해야 한다.
        if result.total_score > 0 and all(float(x).is_integer() and 0 <= x <= 2 for x in svm):
            levels = tuple(int(x) for x in svm)
            if grade_from_svm(*levels) != result.grade:
                bump("표시 S/V/M 이 최종 등급과 모순")
            if result.svm != levels[0] * levels[1] * levels[2]:
                bump("svm 값이 요소 곱과 불일치")

    assert not violations, violations


def test_label_is_deterministic(engine: LabelRuleEngine) -> None:
    for text in _corpus()[:120]:
        assert len({engine.label(text).grade for _ in range(3)}) == 1


# ── D. 곱셈 블록의 현행 동작 고정 ─────────────────────────────────────────────
def _svm_block(content_grade: str, public: bool, has_mgmt: bool) -> tuple[str, str]:
    """label() 의 곱셈 블록 재현 — (svm 등급, FNR-safe 최종 등급)."""
    strong = content_grade in ("TS", "S1")
    s = 0 if (public or content_grade == "S3") else (2 if strong else 1)
    v = 2 if strong else (0 if content_grade == "S3" else 1)
    m = 2 if has_mgmt else (0 if content_grade in ("S3", "S1") else 1)
    svm_grade = grade_from_svm(s, v, m)
    final = min([svm_grade, content_grade], key=lambda g: GRADE_ORDER.get(g, 999))
    return svm_grade, final


def test_multiplicative_block_changes_grade_in_exactly_two_combinations() -> None:
    """도달 가능한 16조합 중 최종 등급이 콘텐츠 등급과 달라지는 것은 둘뿐이다.

    - (S1, public=False, has_mgmt=True)  -> TS   관리성 시드가 S1 을 TS 로 올린다
    - (S1, public=True,  has_mgmt=True)  -> S1   공개 신호가 그 상향을 막는다

    나머지 14조합에서 룰 등급 = 키워드 argmax 다. 이 목록이 바뀌면 판정면이 바뀐 것이니
    수치를 다시 재고 문서(적용서 §3.5 R1·R2)를 함께 고쳐야 한다.
    """
    changed = {
        (cg, pub, hm): _svm_block(cg, pub, hm)[1]
        for cg, pub, hm in itertools.product(GRADES, (False, True), (False, True))
        if _svm_block(cg, pub, hm)[1] != cg
    }
    assert changed == {("S1", False, True): "TS"}, changed


def test_public_signal_only_blocks_the_s1_escalation() -> None:
    """본문의 공개 신호는 등급을 내리지 못한다 — 상향을 막는 것이 유일한 효과다."""
    effective = [
        (cg, hm)
        for cg, hm in itertools.product(GRADES, (False, True))
        if _svm_block(cg, True, hm)[1] != _svm_block(cg, False, hm)[1]
    ]
    assert effective == [("S1", True)], effective


def test_public_signal_never_lowers_the_grade() -> None:
    for cg, hm in itertools.product(GRADES, (False, True)):
        with_public = _svm_block(cg, True, hm)[1]
        without = _svm_block(cg, False, hm)[1]
        assert GRADE_ORDER[with_public] >= GRADE_ORDER[without] or with_public == without
        assert GRADE_ORDER[with_public] <= GRADE_ORDER[cg] or with_public == cg


# ── E. 시드 사전의 구조적 성질 ────────────────────────────────────────────────
def test_no_seed_containment_inverts_a_grade() -> None:
    """exact 매칭이 부분 문자열이라 짧은 시드가 긴 시드 안에서 함께 걸린다.

    짧은 쪽이 **더 무거우면** argmax 가 뒤집혀 더 구체적인 시드가 진다
    (2026-08-26 실측: "Confidential High"(S1,0.8) 가 "Confidential"(S2,0.85) 에 져서 S2).
    시드를 추가할 때 이 관계가 다시 생기면 여기서 잡는다.
    """
    inverted = [
        (a["keyword"], a["grade"], a["weight"], b["keyword"], b["grade"], b["weight"])
        for a in KEYWORD_SEEDS
        for b in KEYWORD_SEEDS
        if a["keyword"] != b["keyword"]
        and a["keyword"] in b["keyword"]
        and a["grade"] != b["grade"]
        and float(a["weight"]) > float(b["weight"])
    ]
    assert not inverted, inverted


def test_every_seed_weight_is_positive() -> None:
    """weight=0 은 점수를 안 주면서 근거로만 집계돼 합의 게이트를 발동시킨다."""
    zero = [s["keyword"] for s in KEYWORD_SEEDS if float(s["weight"]) <= 0]
    assert not zero, zero


def test_management_axis_has_seeds_the_svm_block_cannot_use() -> None:
    """현행 has_mgmt 는 MANAGEMENT 시드 중 TS·S1 태깅만 본다 — 나머지는 M 에 반영되지 않는다.

    이것은 통과가 목표인 단언이 아니라 **알려진 한계의 크기를 고정**하는 것이다.
    숫자가 변하면 시드 태깅이 바뀐 것이므로 곱셈 블록의 M 조건도 함께 봐야 한다.
    """
    mgmt = [s for s in KEYWORD_SEEDS if to_canonical_factor(s.get("factor") or "") == "MANAGEMENT"]
    unused = [s for s in mgmt if s["grade"] not in ("TS", "S1")]
    assert len(mgmt) == 56, len(mgmt)
    assert len(unused) == 29, len(unused)
