"""합성 배치 품질 게이트 — 만들자마자 재고, 나쁘면 검수큐에 넣지 않는다.

왜 필요한가(2026-09-05). 누출 측정기(koipa.dataset_leakage)는 이미 있는데 **생성 경로에
배선돼 있지 않았다.** 학습셋 빌더(build_direct_authored_catalog_*·build_kl_review_pool)만
게이트를 걸고, 합성 생성은 만들어서 그대로 검수큐에 넣었다. 그래서 옛 산출물의 33.8%가
본문에 자기 등급명을 노출한 채 쌓였고(S1 은 94.3%), 그것을 나중에 학습셋 단계에서야
걸러냈다. 검수자가 답이 적힌 문서를 읽으면 검수가 검증이 아니라 확인 절차가 된다.

이 모듈은 그 자리를 채운다 — 생성 직후, 검수큐 적재 **전**.

판정 기준(등급명 노출은 0 이어야 한다)
    grade_token_exposed  본문에 등급 표기가 남은 문서 수 — 'TS'·'S1' 같은 코드뿐 아니라
                         '1급 비밀'·'대외비'·'Level 1 Secret' 까지 본다
                         (generator.FORBIDDEN_GRADE_TERMS 가 정본).
                         검수 후보에서는 0 이 기준이다(allow_grade_token=False).
    tell_coverage        한 등급에만 나오는 문장을 가진 문서 비율. 임계 0.10.
    length_only_1nn      글자 수만 보고 이웃 등급을 따라갔을 때 적중률. 임계 0.55.
                         무작위 기대값은 1/등급수 = 0.25.

⚠ 배치가 작으면 tell/length 지표는 신뢰할 수 없다(한 등급에 몇 건뿐이면 그 등급 문장이
  전부 'tell' 로 잡힌다). MIN_DOCS_FOR_CORPUS_METRICS 미만이면 **등급명 노출만** 본다 —
  그 지표는 문서 한 건으로도 판정이 성립한다.

⚠ 이 게이트는 **버리지 않는다.** 걸린 문서는 flagged 로 표시해 돌려주고, 호출부가
  검수큐 적재에서 뺀다. 생성 비용은 이미 들었으므로 무엇이 왜 걸렸는지는 남긴다.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Sequence

from koipa.dataset_leakage import (
    DEFAULT_MAX_LENGTH_LEAK,
    DEFAULT_MAX_TELL_COVERAGE,
    audit,
    grade_tells,
)

logger = logging.getLogger(__name__)

# 이 아래면 코퍼스 지표(tell·length)는 표본이 모자라 신뢰할 수 없다. 등급명 노출만 본다.
MIN_DOCS_FOR_CORPUS_METRICS = 24


@lru_cache(maxsize=1)
def _grade_term_pattern() -> "re.Pattern[str]":
    """생성기가 금지한 등급 어휘 전체를 잡는 정규식.

    [2026-09-05] 종전에는 dataset_leakage._GRADE_TOKEN 하나만 썼다. 그것은
    ``\b(TS|S1|S2|S3)\b`` 뿐이라 생성기가 실제로 쓰는 한국어 표기를 하나도 못 잡았다:

        본 문서는 [가상기업A]의 1급 비밀로 분류된 자료입니다.      → 놓침
        본 문서는 [가상기업A]의 특급기밀 수준의 정보를 포함한다.    → 놓침
        This document is material classified as Level 1 Secret.  → 놓침
        본 문서의 등급은 S1 이다.                                → 잡힘

    실측(rag_corpus_v2 720건): 등급표현이 있는 527건 중 192건만 잡혔다 —
    **377건(52.4%)이 답을 적은 채로 검수 후보에 들어갔다.** 검수자가 답을 보고 읽으면
    검수가 검증이 아니라 확인 절차가 된다.

    목록은 generator.FORBIDDEN_GRADE_TERMS 에서 가져온다 — 프롬프트가 금지하는 것과
    게이트가 검사하는 것이 갈라지지 않게 한다.
    """
    from koipa.modules.m1_synthesis.generator import (  # noqa: PLC0415
        FORBIDDEN_GRADE_TERMS,
    )

    # 영문 약어(TS·S1…)는 낱말 경계를 요구한다 — "S1" 이 "PS100" 안에서 걸리면 안 된다.
    # 한국어에는 낱말 경계가 없으므로(조사가 붙는다) 그대로 찾는다.
    parts = []
    for term in FORBIDDEN_GRADE_TERMS:
        esc = re.escape(term)
        parts.append(r"\b%s\b" % esc if term.isascii() else esc)
    return re.compile("|".join(parts), re.IGNORECASE)


def _exposes_grade_token(text: str) -> bool:
    """본문에 등급 표기가 남았는가 — 문서 한 건으로 판정 가능한 유일한 지표."""
    return bool(_grade_term_pattern().search(text or ""))


def screen_batch(
    docs: Sequence[tuple[str, str]],
    *,
    max_length_leak: float = DEFAULT_MAX_LENGTH_LEAK,
    max_tell_coverage: float = DEFAULT_MAX_TELL_COVERAGE,
) -> dict[str, Any]:
    """(등급, 본문) 배치를 재서 통과분·보류분을 가른다.

    Returns:
        {"metrics": {...}, "admit": [인덱스], "flagged": [{"index":i, "reason":str}],
         "batch_verdict": "ok" | "corpus_leak" | "too_small_for_corpus_metrics"}

        admit  검수큐에 넣어도 되는 문서의 원본 인덱스
        flagged 걸린 문서와 사유. 호출부가 로그·기록에 쓴다.
    """
    rows = [(g, (t or "")) for g, t in docs]
    flagged: list[dict[str, Any]] = []

    # ① 문서 단위 — 등급명 노출. 표본 수와 무관하게 항상 본다.
    exposed_idx = {i for i, (_g, t) in enumerate(rows) if _exposes_grade_token(t)}
    for i in sorted(exposed_idx):
        flagged.append({"index": i, "reason": "grade_token_exposed"})

    kept = [(i, g, t) for i, (g, t) in enumerate(rows) if i not in exposed_idx]
    metrics = audit([(g, t) for _i, g, t in kept]) if kept else {"documents": 0}

    # ② 코퍼스 단위 — 표본이 모자라면 판정하지 않는다(작은 배치에서 거짓 양성).
    if len(kept) < MIN_DOCS_FOR_CORPUS_METRICS:
        return {
            "metrics": metrics,
            "admit": [i for i, _g, _t in kept],
            "flagged": flagged,
            "batch_verdict": "too_small_for_corpus_metrics",
        }

    leak = float(metrics.get("length_only_1nn", 0.0) or 0.0)
    cover = float(metrics.get("tell_coverage", 0.0) or 0.0)
    if leak <= max_length_leak and cover <= max_tell_coverage:
        return {
            "metrics": metrics,
            "admit": [i for i, _g, _t in kept],
            "flagged": flagged,
            "batch_verdict": "ok",
        }

    # 코퍼스 지표가 임계를 넘으면 **배치 전체**를 보류한다. 어느 문서가 원인인지
    # 단독으로 가릴 수 없는 지표라(길이 분포·등급 전용 문장은 배치의 성질이다)
    # 문서를 골라내는 대신 배치를 통째로 세운다.
    tells = grade_tells([(g, t) for _i, g, t in kept])
    for i, _g, _t in kept:
        flagged.append({"index": i, "reason": "corpus_leak"})
    logger.warning(
        "synth 배치 보류 — length_only_1nn=%.3f(임계 %.2f) tell_coverage=%.3f(임계 %.2f) "
        "tell문장 %d개. 프롬프트가 등급별로 길이·문장을 갈라 쓰고 있을 수 있다.",
        leak, max_length_leak, cover, max_tell_coverage, len(tells),
    )
    return {
        "metrics": metrics,
        "admit": [],
        "flagged": flagged,
        "batch_verdict": "corpus_leak",
    }
