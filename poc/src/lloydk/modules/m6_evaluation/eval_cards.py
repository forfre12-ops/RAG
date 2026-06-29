"""출처별 분리 평가 리포트 카드 — Wilson CI + PASS/FAIL/INCONCLUSIVE (단일 숫자 금지).

설계 결정(평가셋 앵커 중심 다층 평가, 2026-06-29):
- 합성·앵커 평가는 출처(tier/source)마다 신뢰도가 다르다. 섞어서 단일 헤드라인(F1 0.92)을
  내면 거짓 신뢰가 된다. 이 모듈은 (출처 × 등급) 칸마다 N·Wilson 신뢰구간을 내고,
  **표본이 부족하거나 CI가 넓으면 PASS가 아니라 INCONCLUSIVE(판단보류)** 를 반환한다.
- fail-secure: 미탐(under-class FNR)은 '상한 CI ≤ 천장'일 때만 PASS. 못 가르면 INCONCLUSIVE,
  하한조차 천장 초과면 FAIL. (golden_tiers.eval_readiness가 locked 최소표본을 보는 것과
  같은 철학을 모든 출처×등급으로 확장.)
- **단일 숫자로 합치지 않는다**: EvalCardReport.combined_score는 의도적으로 NotImplementedError.
  가중평균 F1 같은 헤드라인을 코드 레벨에서 금지한다.

순수 함수 — DB/파일 불요, 단위테스트 가능. (numpy 불요 — 경량.)
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Sequence

# 심각도 순서 (낮을수록 고등급). confusion_matrix.GRADE_ORDER와 동일.
GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}
DEFAULT_GRADES = ("TS", "S1", "S2", "S3")

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_NA = "N/A"  # 최저등급 — under-class 미탐 정의 안 됨 (PASS로 안 셈)

DEFAULT_MIN_N = 30          # 칸당 최소 표본 (미달이면 INCONCLUSIVE)
DEFAULT_FNR_CEILING = 0.05  # under-class FNR 천장 (상한 CI가 이 이하면 PASS)
DEFAULT_MAX_CI_WIDTH = 0.30  # CI 폭이 이보다 넓으면 검정력 부족 → INCONCLUSIVE


def wilson_interval(successes: int, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """이항 비율의 Wilson score 신뢰구간(기본 95%). n==0이면 (0.0, 1.0)=최대 불확실.

    점추정(k/n) 대신 Wilson을 쓰는 이유: 소표본·극단비율(0%·100%)에서 정규근사보다
    안정적이고, 표본이 적을수록 구간이 자동으로 넓어져 '검정력 부족'을 드러낸다.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    lo = (center - margin) / denom
    hi = (center + margin) / denom
    return (max(0.0, lo), min(1.0, hi))


@dataclass
class GradeCard:
    """(출처 × 등급) 한 칸의 평가 카드. 단일 숫자가 아니라 이 카드들을 나란히 본다."""

    slice: str
    grade: str
    n: int
    underclass_fnr: float            # 점추정 (정답=grade인데 더 낮은 등급으로 예측)
    fnr_ci: tuple[float, float]      # Wilson 95% CI
    recall: float
    recall_ci: tuple[float, float]
    verdict: str                     # PASS / FAIL / INCONCLUSIVE / N/A — under-class FNR 기준
    cannot_measure: str = ""         # 왜 판단보류/제외인지(표본부족·CI과대·최저등급)

    def to_dict(self) -> dict:
        return {
            "slice": self.slice,
            "grade": self.grade,
            "n": self.n,
            "underclass_fnr": round(self.underclass_fnr, 4),
            "fnr_ci": [round(self.fnr_ci[0], 4), round(self.fnr_ci[1], 4)],
            "recall": round(self.recall, 4),
            "recall_ci": [round(self.recall_ci[0], 4), round(self.recall_ci[1], 4)],
            "verdict": self.verdict,
            "cannot_measure": self.cannot_measure,
        }


@dataclass
class EvalCardReport:
    """출처×등급 카드 묶음. **단일 헤드라인 숫자를 의도적으로 제공하지 않는다.**"""

    cards: list[GradeCard]
    counts: dict[str, int] = field(default_factory=dict)  # verdict -> 카드 수 (N/A 제외)
    min_n: int = DEFAULT_MIN_N
    fnr_ceiling: float = DEFAULT_FNR_CEILING
    max_ci_width: float = DEFAULT_MAX_CI_WIDTH

    @property
    def combined_score(self):
        """단일 헤드라인 금지 — 코드 레벨 차단. (평가셋 앵커 중심 다층 설계의 'C가 코드로 금지')"""
        raise NotImplementedError(
            "단일 헤드라인 금지: 출처×등급 카드를 나란히 보라(.cards / .by_slice()). "
            "가중평균 F1 등으로 합치면 거짓 신뢰가 된다(합성·순환 천장)."
        )

    def by_slice(self) -> dict[str, list[GradeCard]]:
        out: dict[str, list[GradeCard]] = defaultdict(list)
        for c in self.cards:
            out[c.slice].append(c)
        return dict(out)

    def to_dict(self) -> dict:
        return {
            "cards": [c.to_dict() for c in self.cards],
            "counts": dict(self.counts),
            "min_n": self.min_n,
            "fnr_ceiling": self.fnr_ceiling,
            "max_ci_width": self.max_ci_width,
            "headline": None,  # 단일 숫자 없음 — 설계상 금지
            "note": "출처×등급 분리 보고 — 단일 숫자로 합치지 않음(표본부족=INCONCLUSIVE)",
        }


def _underclass_verdict(
    n: int,
    ci: tuple[float, float],
    *,
    min_n: int,
    ceiling: float,
    max_ci_width: float,
    is_lowest_grade: bool,
) -> tuple[str, str]:
    """under-class FNR 기준 fail-secure 판정. (PASS=상한CI≤천장일 때만)"""
    if is_lowest_grade:
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


def build_eval_cards(
    records: Sequence[dict],
    *,
    slice_of: Callable[[dict], str] = lambda r: r.get("source") or r.get("tier") or "?",
    true_of: Callable[[dict], object] = lambda r: r.get("gold") or r.get("true") or r.get("label"),
    pred_of: Callable[[dict], object] = lambda r: r.get("pred") or r.get("predicted"),
    grades: Sequence[str] = DEFAULT_GRADES,
    min_n: int = DEFAULT_MIN_N,
    fnr_ceiling: float = DEFAULT_FNR_CEILING,
    max_ci_width: float = DEFAULT_MAX_CI_WIDTH,
) -> EvalCardReport:
    """레코드를 (출처 × 등급) 카드로 분해. 각 칸은 under-class FNR Wilson CI + 판정.

    레코드는 dict. slice_of/true_of/pred_of로 출처·정답·예측을 뽑는다(기본 키 제공).
    under-class = 정답이 grade인데 *더 낮은 등급(심각도↑)*으로 예측 = 미탐(FNR-safe 핵심).
    최저등급(S3)은 under-class 정의 불가 → N/A(PASS로 세지 않음 — 전부-S3 분류기 거짓통과 방지).
    """
    grade_list = list(grades)
    max_order = max((GRADE_ORDER.get(g, 0) for g in grade_list), default=0)

    # (slice, true_grade) -> [n, under, correct]
    bucket: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for r in records:
        t = true_of(r)
        if t not in GRADE_ORDER:
            continue
        s = slice_of(r)
        p = pred_of(r)
        b = bucket[(str(s), str(t))]
        b[0] += 1
        if p == t:
            b[2] += 1
        elif p in GRADE_ORDER and GRADE_ORDER[p] > GRADE_ORDER[t]:
            b[1] += 1  # 더 낮은 등급으로 예측 = under-class 미탐

    cards: list[GradeCard] = []
    counts: dict[str, int] = {VERDICT_PASS: 0, VERDICT_FAIL: 0, VERDICT_INCONCLUSIVE: 0}
    for (s, g), (n, under, correct) in sorted(bucket.items()):
        fnr = under / n if n else 0.0
        recall = correct / n if n else 0.0
        fnr_ci = wilson_interval(under, n)
        recall_ci = wilson_interval(correct, n)
        is_lowest = GRADE_ORDER.get(g, 0) >= max_order
        verdict, why = _underclass_verdict(
            n, fnr_ci, min_n=min_n, ceiling=fnr_ceiling,
            max_ci_width=max_ci_width, is_lowest_grade=is_lowest,
        )
        cards.append(GradeCard(
            slice=s, grade=g, n=n,
            underclass_fnr=fnr, fnr_ci=fnr_ci,
            recall=recall, recall_ci=recall_ci,
            verdict=verdict, cannot_measure=why,
        ))
        if verdict in counts:
            counts[verdict] += 1

    return EvalCardReport(
        cards=cards, counts=counts,
        min_n=min_n, fnr_ceiling=fnr_ceiling, max_ci_width=max_ci_width,
    )
