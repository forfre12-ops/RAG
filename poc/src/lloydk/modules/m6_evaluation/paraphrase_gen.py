"""D 생성 — 메타모픽 최소쌍(패러프레이즈/사실제거) 생성 오케스트레이션.

metamorphic.py(채점)는 "생성은 별개(앵커 파이프라인·Qwen)"라고 명시한다. 이 모듈이 그
생성을 담당하되, **결정적 사실 보존 게이트**(metamorphic.check_fact_preservation)로만 거른다
— 룰/심판(LLM)으로 거르면 순환(분류기↔평가)이 복귀하므로 쓰지 않는다.

대칭 2종 생성:
  - forward(순방향): 등급 결정 사실을 **유지**하고 문체만 바꾼 패러프레이즈.
    채택 = 필수 토큰이 보존(부정문맥 아님)된 것만 → 분류기가 등급을 안 내려야(미탐 FN 테스트).
  - reverse(역방향): 등급 결정 사실을 **제거**한 변형.
    채택 = 필수 토큰이 실제로 사라진(보존검사 admit=False) 것만 → 등급이 내려가야(과분류 FP 테스트).

LLM은 generate_fn(str)->str 으로 **주입** — 모듈은 순수 오케스트레이션이라 fake로 단위테스트
가능하다. 실제 생성은 runner가 build_provider("ollama"/...)로 Qwen을 물려 호출한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from lloydk.modules.m6_evaluation.metamorphic import (
    DEFAULT_NEGATION_MARKERS,
    DEFAULT_NEGATION_WINDOW,
    check_fact_preservation,
)

# (prompt, system) -> text. system을 받는 이유: forward/reverse가 서로 다른 시스템 지시를 쓴다.
GenerateFn = Callable[[str, Optional[str]], str]

# 생성 실패(빈 응답·thinking만 출력 등) 최소 길이. 미만이면 생성 실패로 보고 양방향 모두 탈락
# — 특히 reverse에서 '빈 텍스트=토큰 없음=제거 성공'으로 오채택되는 것을 막는다.
MIN_GEN_LEN = 10

# 역방향(사실 제거) 테스트에 쓸 수 있는 '강한' 토큰 출처 — 본문 중간 근거 + 본문 구체값.
# (제목 evidence@0·단일키워드 ts_kw·도메인폴백 domain은 제외: 그 토큰을 빼도 등급결정 '사실'이
#  본문에 남아 reverse 전제가 성립 안 함 → 거짓 100% 위반을 만든다. 적대검증 2026-06-29.)
# body_fact(수치/인용 구체값)는 빼면 등급결정 사실이 실제 사라질 수 있어 적격.
REVERSE_ELIGIBLE_SOURCES = ("evidence@N", "body_fact")


def is_reverse_eligible(token_sources: Sequence[str]) -> bool:
    """역방향 적격: 본문 중간 근거(evidence@N) 토큰을 1개 이상 보유.

    약토큰(제목/단일어)만 있으면 부적격 — 제거해도 사실이 본문에 남아 reverse 무효.
    """
    return any(s in REVERSE_ELIGIBLE_SOURCES for s in (token_sources or []))

FORWARD_SYSTEM = (
    "당신은 한국어 문서를 다시 쓰는 도구입니다. 의미와 사실은 그대로 두고 문체·어순·표현만 바꿉니다. "
    "정보를 추가하거나 빼지 말고, 특히 제시된 핵심 사실 표현은 반드시 원문 그대로 포함하세요. "
    "설명 없이 다시 쓴 본문만 출력합니다."
)
REVERSE_SYSTEM = (
    "당신은 한국어 문서를 편집하는 도구입니다. 지정된 '핵심 사실'만 문서에서 완전히 제거하고, "
    "나머지는 자연스러운 일반 문서로 유지합니다. 제거 대상 사실을 다른 표현으로도 남기지 마세요. "
    "설명 없이 편집한 본문만 출력합니다."
)


def build_forward_prompt(text: str, required_tokens: Sequence[str]) -> str:
    toks = "\n".join(f"- {t}" for t in required_tokens)
    return (
        "다음 문서를 문체만 바꿔 다시 쓰세요. 아래 핵심 사실 표현은 **반드시 그대로** 유지하세요:\n"
        f"{toks}\n\n[문서]\n{text}\n\n[다시 쓴 문서]"
    )


def build_reverse_prompt(text: str, required_tokens: Sequence[str]) -> str:
    toks = "\n".join(f"- {t}" for t in required_tokens)
    return (
        "다음 문서에서 아래 핵심 사실만 **완전히 제거**하고 나머지는 자연스럽게 유지해 다시 쓰세요. "
        "제거 대상은 동의어·우회표현으로도 남기지 마세요:\n"
        f"{toks}\n\n[문서]\n{text}\n\n[편집한 문서]"
    )


@dataclass
class GeneratedSample:
    """생성된 최소쌍 한 건. kind=forward/reverse. admitted=결정적 게이트 통과 여부."""

    anchor_id: str
    anchor_grade: str
    kind: str                      # "forward" | "reverse"
    original_text: str
    generated_text: str
    required_tokens: list[str]
    admitted: bool
    reason: str = ""
    meta: dict = field(default_factory=dict)


def make_forward(
    *,
    anchor_id: str,
    anchor_grade: str,
    text: str,
    required_tokens: Sequence[str],
    generate_fn: GenerateFn,
    negation_markers: Sequence[str] = DEFAULT_NEGATION_MARKERS,
    negation_window: int = DEFAULT_NEGATION_WINDOW,
) -> GeneratedSample:
    """문체변경·사실유지 패러프레이즈 생성 + **사실 보존** 게이트(admit=토큰 유지·부정 아님)."""
    gen = (generate_fn(build_forward_prompt(text, list(required_tokens)), FORWARD_SYSTEM) or "").strip()
    if len(gen) < MIN_GEN_LEN:
        return GeneratedSample(
            anchor_id=anchor_id, anchor_grade=anchor_grade, kind="forward",
            original_text=text, generated_text=gen, required_tokens=list(required_tokens),
            admitted=False, reason="empty_generation(생성 실패)",
        )
    pres = check_fact_preservation(
        gen, list(required_tokens),
        negation_markers=negation_markers, negation_window=negation_window,
    )
    return GeneratedSample(
        anchor_id=anchor_id, anchor_grade=anchor_grade, kind="forward",
        original_text=text, generated_text=gen, required_tokens=list(required_tokens),
        admitted=pres.admit, reason=pres.reason,
        meta={"missing": pres.missing_tokens, "negated": pres.negated_tokens},
    )


def make_reverse(
    *,
    anchor_id: str,
    anchor_grade: str,
    text: str,
    required_tokens: Sequence[str],
    generate_fn: GenerateFn,
    negation_markers: Sequence[str] = DEFAULT_NEGATION_MARKERS,
    negation_window: int = DEFAULT_NEGATION_WINDOW,
) -> GeneratedSample:
    """등급 결정 사실 **제거** 변형 생성 + 제거확인 게이트.

    역방향 채택 = 사실 보존검사 admit이 **False**(필수 토큰이 사라졌거나 부정문맥) — 즉 제거가
    실제로 일어난 것만 쓴다. admit=True(토큰 그대로면)는 '제거 실패' → 역방향 전제 무효라 제외.
    """
    gen = (generate_fn(build_reverse_prompt(text, list(required_tokens)), REVERSE_SYSTEM) or "").strip()
    if len(gen) < MIN_GEN_LEN:
        # 빈 생성을 '제거 성공'으로 오인하면 안 됨 — 생성 실패로 탈락(reverse 전제 무효).
        return GeneratedSample(
            anchor_id=anchor_id, anchor_grade=anchor_grade, kind="reverse",
            original_text=text, generated_text=gen, required_tokens=list(required_tokens),
            admitted=False, reason="empty_generation(생성 실패)",
        )
    pres = check_fact_preservation(
        gen, list(required_tokens),
        negation_markers=negation_markers, negation_window=negation_window,
    )
    removed = not pres.admit  # 보존 안 됨 = 제거 성공
    return GeneratedSample(
        anchor_id=anchor_id, anchor_grade=anchor_grade, kind="reverse",
        original_text=text, generated_text=gen, required_tokens=list(required_tokens),
        admitted=removed,
        reason=("removed:" + pres.reason) if removed else "fact_still_present(제거 실패)",
        meta={"missing": pres.missing_tokens, "negated": pres.negated_tokens},
    )


def generate_minimal_pairs(
    anchors: Sequence,
    *,
    generate_fn: GenerateFn,
    grade_of: Callable = lambda a: getattr(a, "anchor_grade", None),
    id_of: Callable = lambda a: getattr(a, "anchor_id", None),
    text_of: Callable = lambda a: getattr(a, "text", ""),
    tokens_of: Callable = lambda a: list(getattr(a, "required_tokens", []) or []),
    sources_of: Callable = lambda a: list(getattr(a, "token_sources", []) or []),
    do_reverse: bool = True,
) -> dict:
    """앵커 시퀀스에 대해 forward(+reverse) 최소쌍을 생성하고 채택/탈락을 집계.

    필수 토큰이 없는 앵커는 생성 자체를 건너뛴다(검사 불가 → 순환 안 들임).
    **reverse는 적격 토큰(evidence@N=본문 중간 근거)을 가진 앵커에서만** 생성한다 —
    제목/단일어 약토큰에서 reverse를 돌리면 거짓 위반(빼도 사실 잔존)이 나오므로 제외한다.
    반환: {forward:[...], reverse:[...], skipped_no_tokens, reverse_skipped_weak_tokens}.
    """
    forward: list[GeneratedSample] = []
    reverse: list[GeneratedSample] = []
    skipped = 0
    rev_skipped_weak = 0
    for a in anchors:
        toks = tokens_of(a)
        gid, grade, text = id_of(a), grade_of(a), text_of(a)
        if not toks or not text or grade is None:
            skipped += 1
            continue
        forward.append(make_forward(
            anchor_id=gid, anchor_grade=grade, text=text,
            required_tokens=toks, generate_fn=generate_fn,
        ))
        if do_reverse:
            if not is_reverse_eligible(sources_of(a)):
                rev_skipped_weak += 1
                continue
            reverse.append(make_reverse(
                anchor_id=gid, anchor_grade=grade, text=text,
                required_tokens=toks, generate_fn=generate_fn,
            ))
    return {
        "forward": forward, "reverse": reverse,
        "skipped_no_tokens": skipped, "reverse_skipped_weak_tokens": rev_skipped_weak,
    }
