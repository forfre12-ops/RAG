"""paraphrase_gen (D 생성) — forward 보존게이트 · reverse 제거게이트 · 순환차단 테스트.

LLM은 fake generate_fn(prompt,system)으로 주입 — 모델 없이 오케스트레이션만 검증.
"""
from __future__ import annotations

from dataclasses import dataclass

from lloydk.modules.m6_evaluation.paraphrase_gen import (
    generate_minimal_pairs,
    is_reverse_eligible,
    make_forward,
    make_reverse,
)


@dataclass
class _Anchor:
    anchor_id: str
    anchor_grade: str
    text: str
    required_tokens: list
    token_sources: list = None

    def __post_init__(self):
        # 기본: 강한 토큰(evidence@N)으로 둔다(역방향 적격). 약토큰 테스트는 명시 지정.
        if self.token_sources is None:
            self.token_sources = ["evidence@N"] * len(self.required_tokens)


def _gen_keep(prompt, system=None):
    # 핵심 사실을 유지한 패러프레이즈를 흉내(토큰 포함).
    return "본 자료는 EUV 공정 레시피를 담은 내부 설계 명세서이다."


def _gen_drop(prompt, system=None):
    # 핵심 사실을 제거한 변형(토큰 없음).
    return "본 자료는 일반적인 공정 안내 문서이다."


def _gen_negate(prompt, system=None):
    return "본 자료는 EUV 공정 레시피에 해당하지 않는 일반 문서이다."


def _gen_empty(prompt, system=None):
    return "   "  # 빈/공백 생성(Qwen thinking-only·빈 응답 흉내)


# ---- 빈 생성 가드(생성 실패는 양방향 모두 탈락) ----

def test_forward_rejects_empty_generation():
    s = make_forward(anchor_id="a1", anchor_grade="TS", text="원문",
                     required_tokens=["EUV 공정 레시피"], generate_fn=_gen_empty)
    assert s.admitted is False and "생성 실패" in s.reason


def test_reverse_rejects_empty_generation_not_treated_as_removed():
    # 빈 생성이 '토큰 없음=제거 성공'으로 오채택되면 안 됨.
    s = make_reverse(anchor_id="a1", anchor_grade="TS", text="원문",
                     required_tokens=["EUV 공정 레시피"], generate_fn=_gen_empty)
    assert s.admitted is False and "생성 실패" in s.reason


# ---- forward: 사실 유지여야 채택 ----

def test_forward_admits_when_fact_kept():
    s = make_forward(anchor_id="a1", anchor_grade="TS", text="원문",
                     required_tokens=["EUV 공정 레시피"], generate_fn=_gen_keep)
    assert s.kind == "forward" and s.admitted is True


def test_forward_rejects_when_fact_dropped():
    s = make_forward(anchor_id="a1", anchor_grade="TS", text="원문",
                     required_tokens=["EUV 공정 레시피"], generate_fn=_gen_drop)
    assert s.admitted is False and "missing" in s.reason


def test_forward_rejects_when_fact_negated():
    s = make_forward(anchor_id="a1", anchor_grade="TS", text="원문",
                     required_tokens=["EUV 공정 레시피"], generate_fn=_gen_negate)
    assert s.admitted is False  # 토큰 살아도 부정문맥 → 사실 반전


# ---- reverse: 사실 제거여야 채택(대칭 반대) ----

def test_reverse_admits_when_fact_removed():
    s = make_reverse(anchor_id="a1", anchor_grade="TS", text="원문",
                     required_tokens=["EUV 공정 레시피"], generate_fn=_gen_drop)
    assert s.kind == "reverse" and s.admitted is True  # 제거 성공


def test_reverse_rejects_when_fact_still_present():
    s = make_reverse(anchor_id="a1", anchor_grade="TS", text="원문",
                     required_tokens=["EUV 공정 레시피"], generate_fn=_gen_keep)
    assert s.admitted is False and "제거 실패" in s.reason


# ---- 배치 + 토큰없음 스킵(순환 차단) ----

def test_generate_skips_anchor_without_tokens():
    anchors = [
        _Anchor("a1", "TS", "원문", ["EUV 공정 레시피"]),
        _Anchor("a2", "S1", "원문", []),  # 토큰 없음 → 스킵
    ]
    out = generate_minimal_pairs(anchors, generate_fn=_gen_keep)
    assert out["skipped_no_tokens"] == 1
    assert len(out["forward"]) == 1 and len(out["reverse"]) == 1
    assert out["forward"][0].anchor_id == "a1"


def test_generate_forward_only_when_reverse_disabled():
    anchors = [_Anchor("a1", "TS", "원문", ["EUV 공정 레시피"])]
    out = generate_minimal_pairs(anchors, generate_fn=_gen_keep, do_reverse=False)
    assert len(out["forward"]) == 1 and out["reverse"] == []


# ---- 역방향 적격성: 약토큰(제목/도메인어)은 reverse에서 제외(거짓 100% 방지) ----

def test_is_reverse_eligible_only_for_midbody_evidence():
    assert is_reverse_eligible(["evidence@N"]) is True
    assert is_reverse_eligible(["evidence@0"]) is False   # 제목
    assert is_reverse_eligible(["ts_kw"]) is False         # 단일 키워드
    assert is_reverse_eligible(["domain"]) is False        # 도메인 폴백
    assert is_reverse_eligible([]) is False


def test_weak_token_anchor_excluded_from_reverse_but_kept_in_forward():
    # 제목 토큰만 가진 앵커: forward는 돌되 reverse는 제외(약토큰=거짓위반 방지).
    anchors = [_Anchor("a1", "TS", "원문", ["문서 제목"], token_sources=["evidence@0"])]
    out = generate_minimal_pairs(anchors, generate_fn=_gen_keep)
    assert len(out["forward"]) == 1
    assert out["reverse"] == []
    assert out["reverse_skipped_weak_tokens"] == 1


def test_strong_token_anchor_runs_reverse():
    anchors = [_Anchor("a1", "TS", "원문", ["EUV 공정 레시피"], token_sources=["evidence@N"])]
    out = generate_minimal_pairs(anchors, generate_fn=_gen_drop)
    assert len(out["reverse"]) == 1
    assert out["reverse_skipped_weak_tokens"] == 0
