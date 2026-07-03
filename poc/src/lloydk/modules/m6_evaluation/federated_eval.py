"""연합평가(federated eval) — 고객사 폐쇄망 내부 교정을 실분포 평가로 집계 (반출 0).

왜 이 모듈이 최상위 레버인가
----------------------------
constructed_floor·특허 시간축·앵커는 전부 합성/프록시 분포다 — capstone 실측(합성-only
학습→실문서 F1 0.26)이 보인 '합성↔실 텍스처 갭'을 원리상 못 건드린다. **실분포 성능을
재는 유일한 정답원은 고객사 운영 검수자(사람)의 교정**이다(지재원 사람검수 0 제약과 양립 —
금지된 것은 지재원 검수이지 고객사 운영 검수가 아니다, [[golden-vs-operational-review-loops]]).

이 모듈은 폐쇄망 안에서 교정 (모델예측, 사람정답) 쌍을 받아 혼동행렬·방향별 오류율·보정
카드를 집계한다. **문서·텍스트·doc_id·검수자 신원은 입력 계약에 아예 없다**(EvalPair는
등급 코드·신뢰도·맥락 라벨만) — 구조적으로 유출 불가. 반출이 승인되면 스칼라 집계만
(redact_for_export), 반출이 막히면 현장 리포트로만 소비한다.

정직성 규율(반드시 지킴)
------------------------
1. **단일 population FNR 헤드라인 금지.** 교정은 '검수된 문서'만이라 선택편향이 있다
   (자동확정-미검수는 이 집합에 없다). review_context(escalated vs random_audit)로 층화하고,
   각 층의 편향 성격을 always 명시한다. random_audit 층만이 무편향 추정에 가깝다.
2. under-class(미탐, 사람정답이 더 비밀) = 안전-critical. over-class(과분류)도 함께 보고하되
   섞지 않는다. eval_cards와 동일한 Wilson CI + fail-secure verdict(소표본=INCONCLUSIVE).
3. 보정(calibration): 모델 신뢰도 구간별 실제 정확도 — 과신(OOD) 탐지. bin count 없으면 skip.

순수 모듈 — DB/파일/LLM 0. CLI(federated_eval_report.py)가 DB→EvalPair 매핑을 담당.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from lloydk.modules.m6_evaluation.eval_cards import (
    DEFAULT_FNR_CEILING,
    DEFAULT_MAX_CI_WIDTH,
    DEFAULT_MIN_N,
    GRADE_ORDER,
    VERDICT_FAIL,
    VERDICT_INCONCLUSIVE,
    VERDICT_NA,
    VERDICT_PASS,
    wilson_interval,
)

GRADES = ("TS", "S1", "S2", "S3")

# review_context 값 — 선택편향 성격을 라벨한다.
# ⚠️ 무편향(random_audit)과 신뢰도-프록시 파생층을 엄격히 구분한다: 고신뢰 검수분은
# '무작위 감사'가 아니라 '검수된 편향 표본의 고신뢰 부분집합'(오히려 쉬운 케이스 쏠림)이다.
CTX_RANDOM_AUDIT = "random_audit"            # 명시적 감사 플래그로만 채워짐(스키마 플래그 필요) → 무편향
CTX_ESCALATED_PROXY = "escalated_conf_proxy"  # 신뢰도<τ 프록시(실 라우팅 이력 아님) → 어려운 케이스 쏠림
CTX_HIGH_CONF_PROXY = "high_confidence_conf_proxy"  # 신뢰도>=τ 프록시 → 편향표본의 고신뢰 부분(무편향 아님)
CTX_UNKNOWN = "unknown"                      # 맥락 불명 → 편향 성격 미상(단독 인용 금지)

# 하위호환 별칭(구 이름 참조 방지용) — escalated는 프록시 파생임을 이름으로 드러낸다.
CTX_ESCALATED = CTX_ESCALATED_PROXY

# 신뢰도 프록시로 파생된 층 — context_source='confidence_proxy'로 아티팩트에 자백시킨다.
_CONFIDENCE_PROXY_CONTEXTS = frozenset({CTX_ESCALATED_PROXY, CTX_HIGH_CONF_PROXY})

_CONTEXT_CAVEAT = {
    CTX_RANDOM_AUDIT: "무작위 감사 표본(명시적 감사 플래그) — 실분포 무편향 추정에 가장 가깝다(표본 크기 확인).",
    CTX_ESCALATED_PROXY: "신뢰도<τ 프록시 층(실제 라우팅 이력 아님) — 어려운/불확실 케이스 쏠림. "
                         "FNR이 실분포보다 높게 나오는 경향(상한 성격).",
    CTX_HIGH_CONF_PROXY: "신뢰도>=τ 프록시 층 — 무작위 감사 아님. 검수된 편향 표본의 고신뢰 부분집합"
                         "(쉬운 케이스 쏠림 가능)이라 무편향 성능 인용 금지.",
    CTX_UNKNOWN: "검수 맥락 불명 — 편향 성격 미상. 단독 성능 인용 금지.",
}

# 반출 페이로드에 허용되는 최상위 키(화이트리스트) — 그 외는 유출로 간주해 차단.
_EXPORT_ALLOWED_TOP = frozenset({
    "schema", "generated_note", "min_n", "fnr_ceiling",
    "by_context", "export_contract",
})
_EXPORT_ALLOWED_CTX = frozenset({
    "n", "caveat", "context_source", "confusion_counts", "cards", "calibration_bins",
})
# depth≥3 노드별 허용 키(백스톱을 depth-complete로 — 화이트리스트 밖 키는 어느 깊이서든 차단).
_CARD_KEYS = frozenset({
    "grade", "n", "underclass_fnr", "fnr_ci", "overclass_rate", "recall",
    "verdict", "cannot_measure",
})
_BIN_KEYS = frozenset({"bin", "n", "mean_confidence", "empirical_accuracy", "gap"})


@dataclass(frozen=True)
class EvalPair:
    """교정 1건의 평가 쌍 — **등급 코드·신뢰도·맥락만**(문서/신원 필드 없음=구조적 무유출).

    model_pred : 모델이 예측한 등급(교정의 original_level).
    human_truth: 사람 검수자가 확정한 등급(교정의 corrected_level). confirm이면 == model_pred.
    confidence : 모델 신뢰도(0~1) — 보정 카드용. 없으면 None.
    review_context: CTX_* — 선택편향 성격.
    """

    model_pred: str
    human_truth: str
    confidence: float | None = None
    review_context: str = CTX_UNKNOWN


def _verdict(n: int, ci: tuple[float, float], *, min_n: int, ceiling: float,
             max_ci_width: float, is_lowest: bool) -> tuple[str, str]:
    """eval_cards와 동일한 fail-secure under-class 판정(재사용 대신 동일 규칙 명시)."""
    if is_lowest:
        return VERDICT_NA, "최저등급 — under-class 미탐 정의 안 됨"
    if n < min_n:
        return VERDICT_INCONCLUSIVE, f"표본 부족(n={n}<{min_n})"
    lo, hi = ci
    if (hi - lo) > max_ci_width:
        return VERDICT_INCONCLUSIVE, f"신뢰구간 과대(width={hi - lo:.2f}>{max_ci_width})"
    if hi <= ceiling:
        return VERDICT_PASS, ""
    if lo > ceiling:
        return VERDICT_FAIL, f"미탐 하한 {lo:.2f} > 천장 {ceiling}"
    return VERDICT_INCONCLUSIVE, f"CI가 천장({ceiling})을 가로지름"


def _cards_for_context(
    pairs: Sequence[EvalPair], *, min_n: int, ceiling: float, max_ci_width: float,
) -> tuple[list[dict], dict]:
    """한 context의 (정답등급별) under-class FNR + over-class 카드 + 혼동행렬 counts."""
    max_order = max(GRADE_ORDER.values())
    # (true) -> [n, under, over, correct]
    by_true: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    cm: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in pairs:
        t, pr = p.human_truth, p.model_pred
        if t not in GRADE_ORDER:
            continue
        cm[t][pr] += 1
        b = by_true[t]
        b[0] += 1
        if pr == t:
            b[3] += 1
        elif pr in GRADE_ORDER and GRADE_ORDER[pr] > GRADE_ORDER[t]:
            b[1] += 1  # 더 낮은 등급 예측 = under-class(미탐, 안전-critical)
        elif pr in GRADE_ORDER and GRADE_ORDER[pr] < GRADE_ORDER[t]:
            b[2] += 1  # 더 높은 등급 예측 = over-class(과분류, 안전하나 검수부담)

    cards: list[dict] = []
    for g in GRADES:
        n, under, over, correct = by_true.get(g, [0, 0, 0, 0])
        fnr = under / n if n else 0.0
        fnr_ci = wilson_interval(under, n)
        is_lowest = GRADE_ORDER.get(g, 0) >= max_order
        verdict, why = _verdict(n, fnr_ci, min_n=min_n, ceiling=ceiling,
                                max_ci_width=max_ci_width, is_lowest=is_lowest)
        cards.append({
            "grade": g, "n": n,
            "underclass_fnr": round(fnr, 4),
            "fnr_ci": [round(fnr_ci[0], 4), round(fnr_ci[1], 4)],
            "overclass_rate": round(over / n, 4) if n else 0.0,
            "recall": round(correct / n, 4) if n else 0.0,
            "verdict": verdict, "cannot_measure": why,
        })
    counts = {t: dict(row) for t, row in sorted(cm.items())}
    return cards, counts


def _calibration_bins(pairs: Sequence[EvalPair], *, n_bins: int = 5) -> list[dict]:
    """신뢰도 구간별 실제 정확도(과신 탐지). confidence 없는 쌍은 제외. 빈 bin은 스킵."""
    bins: list[list[int]] = [[0, 0] for _ in range(n_bins)]  # [count, correct]
    conf_sum = [0.0] * n_bins
    for p in pairs:
        if p.confidence is None:
            continue
        c = min(max(float(p.confidence), 0.0), 1.0)
        idx = min(int(c * n_bins), n_bins - 1)
        bins[idx][0] += 1
        conf_sum[idx] += c
        if p.model_pred == p.human_truth:
            bins[idx][1] += 1
    out: list[dict] = []
    for i, (cnt, correct) in enumerate(bins):
        if cnt == 0:
            continue
        acc = correct / cnt
        mean_conf = conf_sum[i] / cnt
        out.append({
            "bin": f"[{i / n_bins:.1f},{(i + 1) / n_bins:.1f})",
            "n": cnt,
            "mean_confidence": round(mean_conf, 4),
            "empirical_accuracy": round(acc, 4),
            "gap": round(mean_conf - acc, 4),  # +면 과신(신뢰도>실정확도) — OOD 위험
        })
    return out


def build_federated_report(
    pairs: Sequence[EvalPair],
    *,
    min_n: int = DEFAULT_MIN_N,
    fnr_ceiling: float = DEFAULT_FNR_CEILING,
    max_ci_width: float = DEFAULT_MAX_CI_WIDTH,
) -> dict:
    """교정 쌍 → context 층화 실분포 평가 리포트(단일 헤드라인 없음).

    반환 dict: {schema, min_n, fnr_ceiling, by_context:{ctx:{n,caveat,confusion_counts,
    cards,calibration_bins}}, generated_note, export_contract}. 문서/신원 필드 없음.
    """
    by_ctx: dict[str, list[EvalPair]] = defaultdict(list)
    for p in pairs:
        ctx = p.review_context if p.review_context in _CONTEXT_CAVEAT else CTX_UNKNOWN
        by_ctx[ctx].append(p)

    contexts: dict[str, dict] = {}
    for ctx, ps in sorted(by_ctx.items()):
        cards, counts = _cards_for_context(
            ps, min_n=min_n, ceiling=fnr_ceiling, max_ci_width=max_ci_width
        )
        contexts[ctx] = {
            "n": len(ps),
            "caveat": _CONTEXT_CAVEAT[ctx],
            "context_source": ("confidence_proxy" if ctx in _CONFIDENCE_PROXY_CONTEXTS
                               else "explicit_or_declared"),
            "confusion_counts": counts,
            "cards": cards,
            "calibration_bins": _calibration_bins(ps),
        }

    return {
        "schema": "federated_eval.v1",
        "min_n": min_n,
        "fnr_ceiling": fnr_ceiling,
        "by_context": contexts,
        "generated_note": (
            "고객사 폐쇄망 내부 교정 집계 — 실분포 정답(사람 검수). 단일 population FNR 없음"
            "(선택편향: 검수된 문서만). **오직 random_audit 층(context_source=explicit_or_declared,"
            " 명시적 감사 플래그)만 무편향 추정에 가깝다. confidence_proxy 층(escalated/high_confidence)은"
            " 편향 표본의 신뢰도 분할일 뿐 무편향 아님 — 무편향 성능으로 인용 금지.**"
        ),
        "export_contract": (
            "이 리포트는 문서/텍스트/doc_id/검수자 신원을 포함하지 않는다(스칼라 집계만). "
            "반출 시 redact_for_export()로 최소화하고 반출 승인 절차를 따른다. "
            "반출 불가 시 현장 소비만."
        ),
    }


def redact_for_export(report: dict) -> dict:
    """반출용 최소 스칼라 페이로드 — 화이트리스트 키만, 그 외 전부 제거.

    입력 리포트는 이미 집계-only지만, 반출 경계에서 한 번 더 화이트리스트로 좁혀
    스키마 확장이 실수로 식별정보를 흘리는 것을 원천 차단한다(fail-closed 필터).
    """
    out: dict = {k: report[k] for k in _EXPORT_ALLOWED_TOP if k in report}
    ctxs = report.get("by_context", {})
    out["by_context"] = {
        ctx: {k: v for k, v in body.items() if k in _EXPORT_ALLOWED_CTX}
        for ctx, body in ctxs.items()
    }
    out["export_contract"] = "SCALAR-ONLY. 반출 승인 필요(반출0 정책). 문서/신원 미포함."
    assert_export_safe(out)
    return out


_ALLOWED_LEAF_TYPES = (int, float, bool, str, type(None))


def assert_export_safe(payload: dict) -> None:
    """반출 페이로드가 스칼라 집계뿐임을 강제 — 위반 시 AssertionError(fail-closed).

    **depth-complete 백스톱**: 모든 깊이에서 노드별 허용 키 집합을 강제한다(최상위·context-body만
    아니라 cards 항목·confusion_counts·calibration_bins 내부까지). 스키마가 확장돼 임의 깊이에
    식별정보 키가 끼어들면 도달 가능성과 무관하게 AssertionError. 리프는 스칼라만 허용.
    """
    top_extra = set(payload) - _EXPORT_ALLOWED_TOP - {"by_context", "export_contract"}
    assert not top_extra, f"export leak: 허용 밖 최상위 키 {top_extra}"

    def _leaf(v: object, where: str) -> None:
        assert isinstance(v, _ALLOWED_LEAF_TYPES), f"export leak: {where} 비스칼라 값 {type(v)}"

    for ctx, body in payload.get("by_context", {}).items():
        assert isinstance(ctx, str), f"export leak: 비문자열 context 키 {ctx!r}"
        ctx_extra = set(body) - _EXPORT_ALLOWED_CTX
        assert not ctx_extra, f"export leak: context '{ctx}' 허용 밖 키 {ctx_extra}"
        for card in body.get("cards", []):
            card_extra = set(card) - _CARD_KEYS
            assert not card_extra, f"export leak: cards 항목 허용 밖 키 {card_extra}"
            for k, v in card.items():
                if isinstance(v, (list, tuple)):   # fnr_ci
                    for x in v:
                        _leaf(x, f"cards.{k}[]")
                else:
                    _leaf(v, f"cards.{k}")
        cm = body.get("confusion_counts", {})
        for tg, row in cm.items():
            assert tg in GRADES, f"export leak: confusion_counts 정답등급 키 {tg!r}"
            assert isinstance(row, dict), f"export leak: confusion_counts[{tg}] 비dict"
            for pg, cnt in row.items():
                assert pg in GRADES, f"export leak: confusion_counts pred 키 {pg!r}"
                _leaf(cnt, f"confusion_counts[{tg}][{pg}]")
        for b in body.get("calibration_bins", []):
            bin_extra = set(b) - _BIN_KEYS
            assert not bin_extra, f"export leak: calibration_bins 항목 허용 밖 키 {bin_extra}"
            for v in b.values():
                _leaf(v, "calibration_bins")
        # n/caveat/context_source 스칼라
        for k in ("n", "caveat", "context_source"):
            if k in body:
                _leaf(body[k], f"by_context.{k}")
