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


def _fold_for_match(text: str) -> str:
    """검사용 정규화 — 전각을 반각으로 접는다. **본문은 고치지 않는다.**

    [2026-09-05] 종전에는 원문 그대로 찾아서 전각 표기를 하나도 못 잡았다:

        본 문서의 등급은 TS 이다.     잡힘
        본 문서의 등급은 ＴＳ 이다.    **놓침**  (U+FF34 U+FF33)
        본 문서는 １급 비밀이다.       **놓침**  (U+FF11)

    전처리(m2_preprocess/normalizer.py)는 NFKC 로 이미 접으므로 **분류 경로는 안전하다.**
    이 게이트는 정규화 **전** 원문에 돌기 때문에(생성 직후·검수큐 적재 전) 여기서 따로 접는다.

    ⚠ 접은 결과는 검사에만 쓴다. 검수자가 읽는 것은 원문이어야 하므로 본문을 바꾸지 않는다.
    """
    import unicodedata  # noqa: PLC0415

    return unicodedata.normalize("NFKC", text or "")


def _document_quality_errors(text: str) -> list[str]:
    """문서 한 건의 품질 하한 — 정본은 proxy_corpus 의 SYNTHETIC_QUALITY_POLICY 다.

    목록을 여기 다시 적지 않는다. 두 벌을 두면 갈리고, 갈리면 같은 합성 문서가 경로에
    따라 다른 기준을 받는다 — 고치려는 것이 바로 그 상태다. 사유 문자열은
    ``quality:<지표>:<값><비교><임계>`` 모양이라 그대로 기록에 남긴다.
    """
    from koipa.proxy_corpus import SYNTHETIC, _quality_errors  # noqa: PLC0415

    try:
        return _quality_errors(text, origin=SYNTHETIC)
    except Exception as exc:  # noqa: BLE001 — 계량 실패가 생성 경로를 끊으면 안 된다
        logger.warning("문서 품질 계량 실패 — 이 건은 통과시킨다: %s", exc)
        return []


def _exposes_grade_token(text: str) -> bool:
    """본문에 등급 표기가 남았는가 — 문서 한 건으로 판정 가능한 유일한 지표."""
    return bool(_grade_term_pattern().search(_fold_for_match(text)))


def screen_batch(
    docs: Sequence[tuple[str, str]],
    *,
    max_length_leak: float = DEFAULT_MAX_LENGTH_LEAK,
    max_tell_coverage: float = DEFAULT_MAX_TELL_COVERAGE,
) -> dict[str, Any]:
    """(등급, 본문) 배치를 재서 통과분·보류분을 가른다.

    Returns:
        {"metrics": {...}, "admit": [인덱스], "flagged": [{"index":i, "reason":str}],
         "quality_flagged": [{"index":i, "reason":"low_quality:<지표>"}],
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

    # ①-2 문서 품질 하한 — 한 건으로 판정 가능하므로 표본 수와 무관하게 본다.
    #
    # [2026-09-06] 이 하한(한글비율·고유4gram·문단수·긴문단·수치사실·중복문단 8지표)은
    # proxy_corpus 에만 걸려 있었다. 전수로 확인한 결과 _quality_errors 호출부는
    # proxy_corpus.py 한 곳뿐이었고, 그래서 **같은 합성 문서가 어느 버튼으로 만들었느냐에
    # 따라 다른 기준을 받았다** — 콘솔 산출물은 이 검사를 한 번도 받지 않았다.
    #
    # 붙이기 전에 재 봤다(datasets/ab_synth/current_s1.jsonl, 현행 생성기 60건):
    #     통과 53 · 탈락 7   사유 blocks<5 5건 · numeric_facts<3 3건 · long_blocks<3 1건
    # 88% 가 통과하고 탈락분은 문단 5개 미만·수치 3개 미만인 얇은 문서다 — 기준이 과한
    # 것이 아니라 걸러야 할 것을 거른다.
    # **떨어뜨리지 않고 표시만 한다.** 왜 그런가.
    #   이 하한은 proxy 코퍼스(고등급 1,200자 이상의 구조 문서) 기준으로 잡힌 값이고,
    #   콘솔 생성은 기본 600~2,000자다. 얇다고 검수자에게서 **감추면** 사람이 그것을 볼
    #   기회가 없어진다 — 검수는 "쓸 수 있나"를 사람이 판단하는 자리다. 그래서 여기서는
    #   기록만 남기고, 떨어뜨릴지는 학습 편입 단계에서 정한다.
    quality_flagged: list[dict[str, Any]] = []
    for i, (_g, text) in enumerate(rows):
        if i in exposed_idx or not (text or "").strip():
            continue
        errors = _document_quality_errors(text)
        if errors:
            # 첫 사유만 남긴다 — 어느 지표에 걸렸는지가 고칠 실마리다.
            quality_flagged.append({"index": i, "reason": f"low_quality:{errors[0]}"})

    kept = [(i, g, t) for i, (g, t) in enumerate(rows) if i not in exposed_idx]
    metrics = audit([(g, t) for _i, g, t in kept]) if kept else {"documents": 0}
    metrics["low_quality_documents"] = len(quality_flagged)

    # ② 코퍼스 단위 — 표본이 모자라면 판정하지 않는다(작은 배치에서 거짓 양성).
    if len(kept) < MIN_DOCS_FOR_CORPUS_METRICS:
        return {
            "metrics": metrics,
            "admit": [i for i, _g, _t in kept],
            "flagged": flagged,
            "quality_flagged": quality_flagged,
            "batch_verdict": "too_small_for_corpus_metrics",
        }

    # ②-1 [2026-09-06] **등급이 하나뿐인 배치에서는 두 코퍼스 지표가 성립하지 않는다.**
    #
    #   길이  1-NN 은 이웃이 무조건 같은 등급이라 항상 1.000 이다. audit() 이 함께 내는
    #         무작위 기대값(length_only_random)도 1.000 이다 — 정보가 0인 값인데 게이트는
    #         무작위 기준선을 보지 않고 1nn > 0.55 만 봐서 **항상 켜졌다.**
    #   tell  "등장의 95% 이상이 한 등급" 조건이 등급이 하나면 무조건 100% 다. 3건 이상
    #         반복되는 상투구가 있으면 등급 정보가 없는데도 tell 로 잡힌다.
    #
    # 실측(datasets/ab_s1, S1 60건씩): qwen 60/60 · gemma 60/60 이 corpus_leak 으로
    # 전량 보류됐다. 등급명 노출 0 · tell 문장 0 인 배치였다 — 걸릴 이유가 없었다.
    # 그런데 **약한 칸을 메우는 증강은 본디 한 등급이다**(S1 이 약해서 S1 만 만든다).
    # 즉 합성의 가장 정당한 용도가 게이트에 100% 막혀 있었다.
    #
    # 같은 결함을 holdout_independence 가 갖고 있었고 9acb882e 에서 길이 축을 뺐다.
    # 여기서는 tell 축까지 뺀다 — 위 근거대로 그쪽도 한 등급에서는 성립하지 않는다.
    # 등급명 노출(①)은 문서 한 건으로 판정되므로 그대로 본다.
    grades_present = len(metrics.get("length_by_grade") or {})
    if grades_present < 2:
        logger.info(
            "synth 배치: 등급이 %d종뿐이라 코퍼스 지표(길이·tell)를 판정에 쓰지 않았다 — "
            "한 등급 배치에서는 두 지표 모두 정의상 최대값이 된다. 등급명 노출은 그대로 봤다.",
            grades_present,
        )
        return {
            "metrics": metrics,
            "admit": [i for i, _g, _t in kept],
            "flagged": flagged,
            "quality_flagged": quality_flagged,
            "batch_verdict": "single_grade_corpus_metrics_skipped",
        }

    leak = float(metrics.get("length_only_1nn", 0.0) or 0.0)
    cover = float(metrics.get("tell_coverage", 0.0) or 0.0)
    if leak <= max_length_leak and cover <= max_tell_coverage:
        return {
            "metrics": metrics,
            "admit": [i for i, _g, _t in kept],
            "flagged": flagged,
            "quality_flagged": quality_flagged,
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
            "quality_flagged": quality_flagged,
        "batch_verdict": "corpus_leak",
    }
