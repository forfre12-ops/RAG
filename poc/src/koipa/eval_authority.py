# -*- coding: utf-8 -*-
"""평가 권위 게이트 — 정답 권위가 없으면 **PASS 라는 문자열을 만들 수 없게** 한다.

## 왜 만들었나 (2026-09-14)

같은 날 두 시간 안에 같은 실수를 두 번 했다.

    1) 배포본 서빙 미탐 2.78%(holdout109) 를 재고 "PMR-002 재현율 90% 를 3배 여유로
       넘는다" 고 보고했다. 그 36건의 정답 등급은 GOLD 0 · SILVER 26 · BRONZE 10 이었다.
       EVAL_CRITERIA 제2조("성능 수치는 GOLD 에서만 낸다") 를 그대로 어겼다.
    2) 이어서 "10건만 더 서명하면 된다" 고 했다. 그 36건 중 13건(36.1%)이
       학습셋에 들어 있었다.

두 번 다 **리포트에는 정보가 있었다.** `label_source` 도, 겹침 검사 도구도 있었다.
빠뜨린 것은 사람(나)이다. 그러니 출력 포맷을 고치는 것으로는 또 같은 일이 난다.

⭐ **이 모듈의 계약: 조건이 안 맞으면 PASS 를 반환할 방법이 아예 없다.**
   `ClaimVerdict.claim_status` 는 생성자에서 계산되고 밖에서 못 바꾼다(frozen).
   `as_headline()` 은 PASS 가 아니면 목표 대비 합격 문구를 만들지 않는다.

## 세 상태

    PASS             GOLD 정답 · 목표 확정 · 대표성 확정 · 학습 겹침 0 — 전부 충족
    DIAGNOSTIC_ONLY  정답이 SILVER/BRONZE — 상대 비교·회귀 감시에만 쓴다
    BLOCKED          목표·정답권위·누출·대표성 중 하나가 **없다** — 참고로도 쓰면 안 된다

## 지표 이름을 강제하는 이유

"미탐률" 한 낱말이 서로 다른 네 가지를 가리키고 있었다. 특히 오늘 잰 값은
`status != needs_review` 를 제외하므로 **재현율이 아니라 '고등급을 낮게 자동확정한 비율'**
이다. 검수로 보낸 것은 사람이 보므로 미탐으로 세지 않는다. 이름에 그 사실을 박는다.

## 대표성의 세 번째 값

`unprovable_now` 가 필요하다. 대표성을 증명하려면 고객사 문서 분포를 알아야 하는데
`document_origin == "customer_real"` 이 전 데이터셋 **0건**이다(실측 2026-09-14).
2값(proven/unproven)만 두면 모든 결과가 영구 BLOCKED 가 되어 게이트가 무의미해진다.
그래서 '무엇이 있어야 확정되는가' 를 함께 적게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class TruthTier(str, Enum):
    """정답을 누가 정했는가. 낮을수록 주장 범위가 좁다."""

    GOLD = "GOLD"            # 사람이 판단하고 서명했다 — 신원이 남는다
    SILVER = "SILVER"        # 기계가 판정했으나 여럿이 합의
    BRONZE = "BRONZE"        # 한 기계 단독 · 생성기 의도 라벨
    CIRCULAR = "CIRCULAR"    # 우리 규칙이 만든 라벨 — 우리 규칙 재현율일 뿐
    NONE = "NONE"            # 출처 기록이 **없다** — 누가 매겼는지 모른다
    UNKNOWN = "UNKNOWN"      # 출처 값이 분류표에 없다


#: BRONZE 와 NONE 은 다르다. 전자는 "한 기계가 매겼다"(추적 가능),
#: 후자는 "누가 매겼는지 모른다"(추적 불가) — 후자는 회귀 대조에도 쓰면 안 된다.
_DIAGNOSTIC_TIERS = frozenset({TruthTier.SILVER, TruthTier.BRONZE})


class Representativeness(str, Enum):
    PROVEN = "proven"
    UNPROVEN = "unproven"
    #: 지금 구조로는 증명할 경로 자체가 없다. 무엇이 있어야 하는지 함께 적는다.
    UNPROVABLE_NOW = "unprovable_now"


class MetricName(str, Enum):
    """'미탐률' 한 낱말이 가리키던 네 가지를 쪼갠다."""

    MODEL_RECALL = "model_recall"
    """모델 raw 등급만 평가. post-model 서빙 가드를 안 태운다."""

    SERVING_RECALL = "serving_recall"
    """모델 + 서빙 게이트 뒤 최종 등급. 검수 라우팅을 성공으로 세지 **않는다**."""

    HIGH_GRADE_AUTO_CONFIRM_FN_RATE = "high_grade_auto_confirm_fn_rate"
    """고등급인데 낮은 등급으로 **자동확정**한 비율. needs_review 로 간 것은 제외한다.
    운영 안전성 지표이지 재현율이 아니다 — 2026-09-14 이전에 '무음 미탐률'로 부르던 값."""

    REVIEW_CAPTURE_RATE = "review_capture_rate"
    """고등급 위험을 검수로 보낸 비율."""

    REVIEW_LOAD = "review_load"
    """검수로 간 문서 비율 — 업무량 축."""


#: 계약상 재현율 주장에 쓸 수 있는 지표. 나머지는 진단·운영 지표다.
_RECALL_METRICS = frozenset({MetricName.MODEL_RECALL, MetricName.SERVING_RECALL})


class CIMethod(str, Enum):
    """단측/양측을 이름에 박는다 — 36건 1미탐에서 12.51% 대 14.53% 로 갈린다."""

    CLOPPER_PEARSON_ONE_SIDED_95 = "clopper_pearson_one_sided_95"
    CLOPPER_PEARSON_TWO_SIDED_95 = "clopper_pearson_two_sided_95"


class ClaimStatus(str, Enum):
    PASS = "PASS"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class TargetSpec:
    """계약 목표 수치. **미확정이면 확정된 척할 수 없다.**

    2026-09-14 현재 세 값이 충돌한다 — RFP 재현율 90% · KL 품질계획서 80% ·
    시나리오 KPI 미탐율 5%. 발주처가 정하기 전에는 `resolved=False` 다.
    """

    name: str                      # 예: "PMR-002"
    max_miss_rate: float | None     # 예: 0.10 (재현율 90%)
    resolved: bool
    source: str = ""               # 어느 문서의 어느 줄인가
    conflicting: tuple[str, ...] = ()

    @classmethod
    def unresolved(cls, *, conflicting: Iterable[str], name: str = "PMR-002") -> "TargetSpec":
        return cls(name=name, max_miss_rate=None, resolved=False,
                   source="미확정", conflicting=tuple(conflicting))


@dataclass(frozen=True)
class EvalEvidence:
    """수치 하나가 서려면 이만큼이 같이 와야 한다 — EVAL_CRITERIA 제5조의 코드판."""

    suite_id: str
    metric: MetricName
    truth_tier: TruthTier
    n: int                              # 분모(고등급 문서 수 등)
    misses: int
    ci_method: CIMethod
    target: TargetSpec
    representativeness: Representativeness
    training_overlap_checked: bool
    training_overlap_count: int
    ship_model_id: str = ""
    measured_at: str = ""
    #: 대표성이 unprovable_now 일 때 '무엇이 있어야 확정되는가'
    representativeness_blocker: str = ""
    #: 여러 평가면을 합산하지 않았음을 명시 — 적대면·일반면·공개S3면은 따로 본다
    aggregated_faces: tuple[str, ...] = ()
    #: 이 평가면이 제외 목록에 있으면 그 사유. 있으면 무조건 BLOCKED 다.
    #: (예: patent_proxy — 정답이 우리 등급식과 반대라 채점이 뒤집힌다)
    suite_excluded_reason: str = ""

    def __post_init__(self) -> None:
        if self.n < 0 or self.misses < 0:
            raise ValueError("n·misses 는 음수일 수 없다")
        if self.misses > self.n:
            raise ValueError(f"misses({self.misses}) > n({self.n})")
        if self.representativeness is Representativeness.UNPROVABLE_NOW \
                and not self.representativeness_blocker.strip():
            raise ValueError(
                "representativeness=unprovable_now 면 무엇이 있어야 확정되는지 적어야 한다"
            )


@dataclass(frozen=True)
class ClaimVerdict:
    """판정. `claim_status` 는 밖에서 못 바꾼다."""

    status: ClaimStatus
    reasons: tuple[str, ...]
    evidence: EvalEvidence
    observed_rate: float | None
    ci_upper: float | None

    @property
    def is_pass(self) -> bool:
        return self.status is ClaimStatus.PASS

    def as_headline(self) -> str:
        """사람이 읽는 한 줄. **PASS 가 아니면 합격 문구를 만들지 않는다.**"""
        e = self.evidence
        base = (f"[{e.suite_id} · N={e.n} · 정답등급 {e.truth_tier.value} · "
                f"{e.metric.value}]")
        if self.status is ClaimStatus.PASS:
            return (f"{base} {e.target.name} 충족 — 관측 {self._pct(self.observed_rate)} · "
                    f"{e.ci_method.value} 상한 {self._pct(self.ci_upper)} ≤ "
                    f"{self._pct(e.target.max_miss_rate)}")
        if self.status is ClaimStatus.DIAGNOSTIC_ONLY:
            return (f"{base} 진단값 — 관측 {self._pct(self.observed_rate)}. "
                    f"성능 주장 불가: {'; '.join(self.reasons)}")
        return f"{base} 주장 불가(BLOCKED): {'; '.join(self.reasons)}"

    @staticmethod
    def _pct(x: float | None) -> str:
        return "-" if x is None else f"{x * 100:.2f}%"

    def to_dict(self) -> dict:
        e = self.evidence
        return {
            "suite_id": e.suite_id,
            "metric_name": e.metric.value,
            "truth_tier": e.truth_tier.value,
            "claim_status": self.status.value,
            "reasons": list(self.reasons),
            "n": e.n,
            "misses": e.misses,
            "observed_rate": self.observed_rate,
            "ci_method": e.ci_method.value,
            "ci_upper": self.ci_upper,
            "target": {
                "name": e.target.name,
                "max_miss_rate": e.target.max_miss_rate,
                "resolved": e.target.resolved,
                "source": e.target.source,
                "conflicting": list(e.target.conflicting),
            },
            "representativeness": e.representativeness.value,
            "representativeness_blocker": e.representativeness_blocker,
            "training_overlap_checked": e.training_overlap_checked,
            "training_overlap_count": e.training_overlap_count,
            "ship_model_id": e.ship_model_id,
            "measured_at": e.measured_at,
            "aggregated_faces": list(e.aggregated_faces),
            "suite_excluded_reason": e.suite_excluded_reason,
        }


#: 평가면 단위 제외 목록. 파일을 옮기지 않고 **역할을 뺀다** — 경로가 847곳에 박혀 있어
#: 이동은 되돌릴 수 없다(git 추적 1.39%). 목록은 커밋되는 자리에 둔다.
EXCLUSIONS_PATH = "evidence/eval_suite_exclusions.jsonl"


def load_suite_exclusions(poc_root=None) -> dict[str, dict]:
    """제외된 평가면 목록. 파일 부재는 '아직 아무것도 제외 안 함' 이라 빈 dict 를 돌린다."""
    import json as _json  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    root = _Path(poc_root) if poc_root else _Path(__file__).resolve().parent.parent.parent
    p = root / EXCLUSIONS_PATH
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            r = _json.loads(line)
        except ValueError:
            continue
        key = str(r.get("suite_path") or "")
        if key:
            out[key] = r
    return out


def _ci_upper(misses: int, n: int, method: CIMethod) -> float | None:
    """Clopper-Pearson 상한. scipy 가 없으면 None — 없는 값을 지어내지 않는다."""
    if n <= 0:
        return None
    try:
        from scipy.stats import beta  # noqa: PLC0415
    except ImportError:
        return None
    q = 0.95 if method is CIMethod.CLOPPER_PEARSON_ONE_SIDED_95 else 0.975
    if misses >= n:
        return 1.0
    return float(beta.ppf(q, misses + 1, n - misses))


def assess(evidence: EvalEvidence) -> ClaimVerdict:
    """증거 하나를 받아 판정한다. 이 함수 밖에서 PASS 를 만들 수 없다.

    BLOCKED 우선 — 하나라도 없으면 진단값으로도 쓰지 않는다.
    """
    e = evidence
    blocked: list[str] = []
    diagnostic: list[str] = []

    # (0) 평가면 제외 — 정답 자체가 쓸 수 없는 면이면 다른 축을 볼 필요가 없다
    if e.suite_excluded_reason.strip():
        blocked.append(f"평가면 제외 목록에 있음 — {e.suite_excluded_reason.strip()}")

    # (1) 누출 — 검사 안 했으면 '겹침 0' 과 구별되어야 한다
    if not e.training_overlap_checked:
        blocked.append("학습 겹침 미검사 — 안 잰 것은 '겹침 없음'이 아니다")
    elif e.training_overlap_count > 0:
        blocked.append(f"학습 겹침 {e.training_overlap_count}건 — 결과 폐기")

    # (2) 목표 수치
    if not e.target.resolved or e.target.max_miss_rate is None:
        conflict = ", ".join(e.target.conflicting) or "미확정"
        blocked.append(f"TARGET_UNRESOLVED — 계약 목표 미확정({conflict})")

    # (3) 정답 권위
    if e.truth_tier is TruthTier.GOLD:
        pass
    elif e.truth_tier in _DIAGNOSTIC_TIERS:
        diagnostic.append(f"정답등급 {e.truth_tier.value} — GOLD 아님")
    else:
        blocked.append(
            f"정답등급 {e.truth_tier.value} — "
            + ("우리 규칙 역산이라 성능이 아니다" if e.truth_tier is TruthTier.CIRCULAR
               else "출처 기록이 없어 회귀 대조에도 쓸 수 없다")
        )

    # (4) 대표성 — 재현율 주장에만 요구한다. 운영 안전성 지표는 면 단위로 읽는다.
    if e.metric in _RECALL_METRICS:
        if e.representativeness is Representativeness.UNPROVEN:
            blocked.append("대표성 미확정 — 전체 성능 주장 금지")
        elif e.representativeness is Representativeness.UNPROVABLE_NOW:
            blocked.append(
                f"대표성 증명 경로 없음 — {e.representativeness_blocker}"
            )

    # (5) 평가면 합산 금지
    if len(e.aggregated_faces) > 1:
        blocked.append(
            f"평가면 {len(e.aggregated_faces)}개를 합산했다 — 적대면·일반면·공개S3면은 따로 낸다"
        )

    rate = (e.misses / e.n) if e.n else None
    ub = _ci_upper(e.misses, e.n, e.ci_method)

    if blocked:
        return ClaimVerdict(ClaimStatus.BLOCKED, tuple(blocked), e, rate, ub)
    if diagnostic:
        return ClaimVerdict(ClaimStatus.DIAGNOSTIC_ONLY, tuple(diagnostic), e, rate, ub)

    # 여기까지 왔으면 GOLD · 목표확정 · 겹침 0 · 대표성 OK. 마지막은 수치다.
    if ub is None:
        return ClaimVerdict(
            ClaimStatus.BLOCKED, ("신뢰구간을 계산하지 못했다(scipy 부재) — 상한 없이 주장 금지",),
            e, rate, ub,
        )
    assert e.target.max_miss_rate is not None
    if ub > e.target.max_miss_rate:
        return ClaimVerdict(
            ClaimStatus.DIAGNOSTIC_ONLY,
            (f"{e.ci_method.value} 상한 {ub * 100:.2f}% > 목표 "
             f"{e.target.max_miss_rate * 100:.2f}% — 표본 부족",),
            e, rate, ub,
        )
    return ClaimVerdict(ClaimStatus.PASS, (), e, rate, ub)


def required_n(max_miss_rate: float, misses: int, method: CIMethod, *, cap: int = 5000) -> int | None:
    """미탐 `misses` 건을 관측한 상태에서 목표를 주장하려면 표본이 몇 건이어야 하나."""
    for n in range(max(misses, 1), cap + 1):
        ub = _ci_upper(misses, n, method)
        if ub is not None and ub <= max_miss_rate:
            return n
    return None


__all__ = [
    "CIMethod", "ClaimStatus", "ClaimVerdict", "EvalEvidence", "MetricName",
    "Representativeness", "TargetSpec", "TruthTier", "assess", "required_n",
    "load_suite_exclusions", "EXCLUSIONS_PATH",
]
