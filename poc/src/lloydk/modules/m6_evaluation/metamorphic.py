"""D — 메타모픽 최소쌍 회귀 테스트 코어 + 사실 보존 게이트 (정답 평가셋 아님).

설계 결정(평가셋 앵커 중심 다층 평가, 2026-06-29):
- D는 "정답 평가셋"이 아니라 **회귀 테스트**다. 불변식: 외부 사실 앵커(NKT→TS·판례→S1/S2)를
  내부문서체로 **문체만 바꿔도 등급이 내려가면 안 된다**. anchor=TS·paraphrase=S3 = 위험 신호.
- **핵심 규율: 실패만 정보다. 통과는 정보가 아니다.** (전부 TS로 찍는 분류기도 순방향을 공허
  통과한다.) 그래서 대칭 3종을 같이 본다:
    · 순방향 — 사실 유지·문체 변경 → 등급 안 내려가야 (미탐 FN)
    · 역방향 — 등급 결정 사실 제거 → 등급 내려가야 (과분류 FP; 안 내려가면 표면 단서 패닉)
    · 과분류율 — 공개→S3 유지 (과분류 베이스라인)
  → MetamorphicReport.quality_score는 의도적으로 NotImplementedError(통과=품질 아님).

- 패러프레이즈 라벨 상속은 **결정적 사실 보존 검사**로만 거른다(룰/심판 아님 — 그러면 순환 복귀).
  check_fact_preservation: 소스별 필수 사실 토큰 보존 + 부정/방향 가드(토큰 살아도 의미 반전 차단).
  필요/충분이 아니라 **필요 조건**이다(부정 휴리스틱은 완벽 보증 못 함 — 모델카드 명시).

순수 함수 — DB/LLM/파일 불요. 패러프레이즈 *생성*은 별개(앵커 파이프라인·Qwen). 이 모듈은
생성 결과(쌍 + 분류기 예측)를 받아 **채점/검사**만 한다. eval_cards.wilson_interval 재사용.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from lloydk.modules.m6_evaluation.eval_cards import GRADE_ORDER, wilson_interval

# 등급 결정 사실이 부정·완화·제외 문맥에 묶였는지 — 토큰 주변 윈도에서 탐지.
DEFAULT_NEGATION_MARKERS = (
    "아니", "아닌", "않", "없", "미해당", "해당 없", "해당없", "비해당", "제외",
    "not ", "n't", "no ", "non-", "없음",
)
DEFAULT_NEGATION_WINDOW = 20  # 토큰 앞뒤 글자수


@dataclass
class PreservationResult:
    """패러프레이즈 사실 보존 검사 결과(D 라벨상속 결정적 게이트)."""

    admit: bool
    missing_tokens: list[str] = field(default_factory=list)   # 빠진 필수 사실 토큰
    negated_tokens: list[str] = field(default_factory=list)   # 부정/방향 반전된 토큰
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "admit": self.admit,
            "missing_tokens": self.missing_tokens,
            "negated_tokens": self.negated_tokens,
            "reason": self.reason,
        }


def _negation_near(text: str, start: int, end: int, markers: Sequence[str], window: int) -> bool:
    seg = text[max(0, start - window): end + window].lower()
    return any(m.lower() in seg for m in markers)


def check_fact_preservation(
    text: str,
    required_tokens: Sequence[str],
    *,
    negation_markers: Sequence[str] = DEFAULT_NEGATION_MARKERS,
    negation_window: int = DEFAULT_NEGATION_WINDOW,
) -> PreservationResult:
    """패러프레이즈가 원 앵커의 **등급 결정 사실**을 보존했는지 결정적으로 검사.

    admit = (필수 토큰 전부 존재) AND (어떤 토큰도 부정/제외 문맥에 안 묶임).
    룰/심판(LLM) 미사용 — 결정적 문자열 검사라 순환을 들이지 않는다. 부정 가드는
    휴리스틱(필요조건)이라 완벽 보증은 아니다. required_tokens 비면 admit=False(검사 불가).
    """
    if not required_tokens:
        return PreservationResult(admit=False, reason="no_required_tokens(검사 불가)")

    missing: list[str] = []
    negated: list[str] = []
    for tok in required_tokens:
        idx = text.find(tok)
        if idx < 0:
            missing.append(tok)
            continue
        if _negation_near(text, idx, idx + len(tok), negation_markers, negation_window):
            negated.append(tok)

    admit = not missing and not negated
    if admit:
        reason = "ok"
    elif missing:
        reason = f"missing_tokens:{missing}"
    else:
        reason = f"negated_tokens:{negated}"
    return PreservationResult(admit=admit, missing_tokens=missing, negated_tokens=negated, reason=reason)


@dataclass
class ViolationStat:
    """한 메타모픽 검사의 위반 통계. 핵심은 violations(실패) — 통과는 품질 증거 아님."""

    name: str
    n: int
    violations: int
    violation_rate: float
    ci: tuple[float, float]   # 위반율 Wilson 95% CI
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "n": self.n,
            "violations": self.violations,
            "violation_rate": round(self.violation_rate, 4),
            "ci": [round(self.ci[0], 4), round(self.ci[1], 4)],
            "note": self.note,
        }


@dataclass
class MetamorphicReport:
    forward: ViolationStat
    reverse: ViolationStat
    over_class: ViolationStat
    forward_failures: list[dict] = field(default_factory=list)  # 위험 신호 상세

    @property
    def quality_score(self):
        """통과=품질 아님 — 코드 레벨 차단. 메타모픽은 **실패만 정보**다."""
        raise NotImplementedError(
            "메타모픽 회귀는 실패(위반)만 정보다. 통과(violations=0)는 품질 증거가 아니다 "
            "(전부 TS로 찍어도 순방향 공허 통과). violations/CI를 보라."
        )

    def to_dict(self) -> dict:
        return {
            "forward": self.forward.to_dict(),
            "reverse": self.reverse.to_dict(),
            "over_class": self.over_class.to_dict(),
            "forward_failures": self.forward_failures,
            "quality_score": None,  # 단일 품질 점수 없음 — 설계상 금지
            "note": "실패만 정보 · 통과는 품질 증거 아님 · 순방향(FN)+역방향(FP)+과분류 대칭",
        }


def _ord(g: object) -> int | None:
    return GRADE_ORDER.get(str(g)) if g is not None else None


def build_metamorphic_report(
    forward_pairs: Sequence[dict],
    *,
    reverse_pairs: Sequence[dict] | None = None,
    public_records: Sequence[dict] | None = None,
    anchor_grade_of: Callable[[dict], object] = lambda r: r.get("anchor_grade"),
    fwd_pred_of: Callable[[dict], object] = lambda r: r.get("paraphrase_pred"),
    rev_pred_of: Callable[[dict], object] = lambda r: r.get("fact_removed_pred"),
    pub_pred_of: Callable[[dict], object] = lambda r: r.get("pred"),
    id_of: Callable[[dict], object] = lambda r: r.get("anchor_id"),
    lowest_grade: str = "S3",
) -> MetamorphicReport:
    """최소쌍 메타모픽 대칭 채점. GRADE_ORDER: 낮을수록 고등급.

    순방향 위반(미탐): 패러프레이즈 예측이 앵커 등급보다 **더 낮은 등급**(order↑). =위험 신호.
    역방향 위반(과분류): 등급 사실 제거 후에도 **안 내려감**(order ≤ 앵커). =표면 단서 패닉.
    과분류 위반: 공개 문서 예측이 최저등급보다 **더 비밀**(order < S3). =공개→비밀 오분류.
    """
    lowest_ord = GRADE_ORDER.get(lowest_grade, 99)

    # 순방향
    fwd_n = fwd_v = 0
    failures: list[dict] = []
    for r in forward_pairs:
        ao, po = _ord(anchor_grade_of(r)), _ord(fwd_pred_of(r))
        if ao is None or po is None:
            continue
        fwd_n += 1
        if po > ao:  # 더 낮은 등급으로 예측 = 미탐
            fwd_v += 1
            failures.append({
                "anchor_id": id_of(r),
                "anchor_grade": str(anchor_grade_of(r)),
                "paraphrase_pred": str(fwd_pred_of(r)),
            })
    forward = ViolationStat(
        name="forward(미탐 FN)", n=fwd_n, violations=fwd_v,
        violation_rate=(fwd_v / fwd_n if fwd_n else 0.0), ci=wilson_interval(fwd_v, fwd_n),
        note="사실 유지·문체 변경 → 등급 안 내려가야. 위반=고등급 미탐(위험).",
    )

    # 역방향
    rev_n = rev_v = 0
    for r in (reverse_pairs or []):
        ao, po = _ord(anchor_grade_of(r)), _ord(rev_pred_of(r))
        if ao is None or po is None:
            continue
        rev_n += 1
        if po <= ao:  # 안 내려감(같거나 더 비밀) = 사실 무시
            rev_v += 1
    reverse = ViolationStat(
        name="reverse(과분류 FP)", n=rev_n, violations=rev_v,
        violation_rate=(rev_v / rev_n if rev_n else 0.0), ci=wilson_interval(rev_v, rev_n),
        note="등급 사실 제거 → 등급 내려가야. 위반=표면 단서 패닉(과분류).",
    )

    # 과분류율(공개→S3)
    pub_n = pub_v = 0
    for r in (public_records or []):
        po = _ord(pub_pred_of(r))
        if po is None:
            continue
        pub_n += 1
        if po < lowest_ord:  # 최저등급보다 더 비밀
            pub_v += 1
    over_class = ViolationStat(
        name="over_class(공개→비밀)", n=pub_n, violations=pub_v,
        violation_rate=(pub_v / pub_n if pub_n else 0.0), ci=wilson_interval(pub_v, pub_n),
        note=f"공개 문서는 {lowest_grade} 유지해야. 위반=공개를 비밀로 과분류.",
    )

    return MetamorphicReport(
        forward=forward, reverse=reverse, over_class=over_class, forward_failures=failures,
    )
