"""합성 배치 품질 게이트 — 만들자마자 재고, 나쁘면 검수큐에 넣지 않는다.

왜 필요한가(2026-09-05). 누출 측정기(koipa.dataset_leakage)는 이미 있는데 **생성 경로에
배선돼 있지 않았다.** 학습셋 빌더(build_direct_authored_catalog_*·build_kl_review_pool)만
게이트를 걸고, 합성 생성은 만들어서 그대로 검수큐에 넣었다. 그래서 옛 산출물의 33.8%가
본문에 자기 등급명을 노출한 채 쌓였고(S1 은 94.3%), 그것을 나중에 학습셋 단계에서야
걸러냈다. 검수자가 답이 적힌 문서를 읽으면 검수가 검증이 아니라 확인 절차가 된다.

이 모듈은 그 자리를 채운다 — 생성 직후, 검수큐 적재 **전**.

판정 기준(등급명 노출은 0 이어야 한다)
    grade_token_exposed  본문에 'TS'·'S1' 등 등급 문자열이 남은 문서 수.
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


def _exposes_grade_token(text: str) -> bool:
    """본문에 등급 문자열이 남았는가 — 문서 한 건으로 판정 가능한 유일한 지표."""
    from koipa.dataset_leakage import _GRADE_TOKEN  # noqa: PLC0415

    return bool(_GRADE_TOKEN.search(text or ""))


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
