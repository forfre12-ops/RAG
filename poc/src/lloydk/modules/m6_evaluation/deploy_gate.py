"""재학습 모델 배포 합격선 게이트 — A2-② / C-ver (doc/36 본개발 #1).

재학습된 후보 모델을 운영에 **활성화(activate)** 하기 전, 현재 활성(baseline) 모델 대비
보안 회귀가 없는지 검증한다. 영업비밀 시스템의 안전원칙(미탐=치명)에 따라 fail-SECURE:

  - **fnr_high(고등급 미탐율)** 가 baseline + tolerance 이내여야 한다 (미탐 악화 차단 — 최우선).
  - **f1_macro** 가 baseline - tolerance 이상이어야 한다 (전반 성능 붕괴 차단).
  - **degenerate('전부 한 클래스')** 후보는 무조건 거부 (confusion matrix 열 지배 ≥99%).
  - 핵심 지표(fnr_high)가 후보에 **없으면 거부**(검증 불가 → 배포 보류, fail-CLOSED).

baseline이 없으면(최초 배포) degenerate가 아닌 한 통과한다 — 첫 모델은 비교대상이 없으므로
회귀 판정이 불가능하고, 룰-폴백보다는 학습 모델이 낫다는 가정. 단, require_baseline=True면
baseline 부재 자체를 미통과로 본다(엄격 운영).

순수 함수 — DB·모델·파일 없이 dict/숫자만으로 판정한다(블라인드 자동배포 위험 없이 테스트 가능).
호출부(workers/tasks.py train_classifier_task)는 이 결정으로 activate 여부를 가른다:
register는 항상(이력 보존), activate는 gate.passed AND settings.retrain_auto_activate 일 때만.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

# 게이트 기본 허용오차 — config.py(Settings)에서 주입 가능. 안전 기본:
#   fnr_high는 악화 0 허용(tolerance=0.0)이 가장 안전하나, 평가셋 표본분산(±1~2건)으로
#   동급 모델이 반려되는 것을 막기 위해 소폭(0.02) 허용. f1은 5%p 하락까지만 용인.
DEFAULT_FNR_HIGH_TOLERANCE = 0.02
DEFAULT_F1_DROP_TOLERANCE = 0.05
DEFAULT_DEGENERATE_COL_SHARE = 0.99


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str
    candidate: Optional[float] = None
    baseline: Optional[float] = None


@dataclass
class DeployDecision:
    passed: bool
    reason: str
    checks: list[GateCheck] = field(default_factory=list)
    candidate_version: Optional[str] = None
    baseline_version: Optional[str] = None
    had_baseline: bool = False

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "candidate_version": self.candidate_version,
            "baseline_version": self.baseline_version,
            "had_baseline": self.had_baseline,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "detail": c.detail,
                    "candidate": c.candidate,
                    "baseline": c.baseline,
                }
                for c in self.checks
            ],
        }


def _as_metrics(obj: Any) -> dict:
    """TrainReport / MetricsResult / dict 어느 것이든 metric dict로 정규화."""
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return dict(obj)
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            d = to_dict()
            if isinstance(d, Mapping):
                return dict(d)
        except Exception:  # noqa: BLE001
            pass
    # dataclass/일반 객체 — __dict__ 폴백
    d = getattr(obj, "__dict__", None)
    return dict(d) if isinstance(d, Mapping) else {}


def _get_float(d: Mapping, *keys: str) -> Optional[float]:
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                continue
    return None


def _is_degenerate(cm: Any, *, col_share: float = DEFAULT_DEGENERATE_COL_SHARE) -> Optional[bool]:
    """confusion matrix(행=정답, 열=예측)에서 한 예측 열이 전체의 col_share 이상이면 degenerate.

    cm가 없거나 형태가 이상하면 None(판정 불가) — 호출부는 None을 '검사 생략'으로 본다.
    trainer._compute_degenerate_penalty와 동일 기준이되 의존 없이 순수 계산.
    """
    if not isinstance(cm, Sequence) or not cm:
        return None
    try:
        rows = [list(r) for r in cm]
        n_cols = len(rows[0])
        if n_cols == 0 or any(len(r) != n_cols for r in rows):
            return None
        total = sum(sum(int(v) for v in r) for r in rows)
        if total <= 0:
            return None
        col_sums = [sum(int(rows[i][j]) for i in range(len(rows))) for j in range(n_cols)]
        return max(col_sums) >= col_share * total
    except (TypeError, ValueError):
        return None


def evaluate_deploy_gate(
    candidate: Any,
    baseline: Any = None,
    *,
    fnr_high_tolerance: float = DEFAULT_FNR_HIGH_TOLERANCE,
    f1_drop_tolerance: float = DEFAULT_F1_DROP_TOLERANCE,
    require_baseline: bool = False,
    first_deploy_fnr_high_max: Optional[float] = None,
    candidate_version: Optional[str] = None,
    baseline_version: Optional[str] = None,
) -> DeployDecision:
    """후보 모델을 활성화해도 되는지 fail-SECURE 판정.

    Args:
        candidate: 후보 모델 metric (TrainReport/MetricsResult/dict). fnr_high·f1_macro 필요.
        baseline:  현재 활성 모델 metric. None이면 최초 배포로 간주.
        fnr_high_tolerance: 후보 fnr_high ≤ baseline + 이 값이어야 통과. (악화 허용 상한)
        f1_drop_tolerance:  후보 f1_macro ≥ baseline - 이 값이어야 통과. (성능 하락 허용)
        require_baseline:   True면 baseline 부재 자체를 미통과로(엄격 운영).
        first_deploy_fnr_high_max: [NEW-M2] 최초 배포(baseline 없음) 절대 FNR floor.
            None(기본)이면 미적용(동작 보존). 설정 시 baseline 없는 첫 모델도
            fnr_high ≤ 이 값이어야 통과(미탐 하한 — fnr=10% 같은 미탐모델 무조건 통과 차단).
        *_version: 로깅/감사용 라벨.

    Returns:
        DeployDecision(passed, reason, checks[]). passed=True일 때만 activate 권장.
    """
    cand = _as_metrics(candidate)
    base = _as_metrics(baseline)
    had_baseline = bool(base)
    checks: list[GateCheck] = []

    # ── 1. degenerate 가드 (baseline 유무와 무관, 무조건) ────────────────────
    degen = _is_degenerate(cand.get("confusion_matrix"))
    if degen is True:
        checks.append(GateCheck(
            "degenerate", False,
            "후보 예측이 한 클래스에 ≥99% 집중(degenerate) — 거부",
        ))
    elif degen is None:
        checks.append(GateCheck(
            "degenerate", True,
            "confusion_matrix 없음 — degenerate 검사 생략",
        ))
    else:
        checks.append(GateCheck("degenerate", True, "degenerate 아님"))

    # ── 2. fnr_high 존재 검증 (fail-CLOSED) ─────────────────────────────────
    cand_fnr = _get_float(cand, "fnr_high", "test_fnr_high")
    if cand_fnr is None:
        checks.append(GateCheck(
            "fnr_high_present", False,
            "후보에 fnr_high 지표 없음 — 미탐 회귀 검증 불가, 배포 보류(fail-closed)",
        ))
    else:
        checks.append(GateCheck("fnr_high_present", True, f"fnr_high={cand_fnr:.4f}", cand_fnr))

    # ── 3. baseline 비교 (있을 때만) ────────────────────────────────────────
    if had_baseline:
        base_fnr = _get_float(base, "fnr_high", "test_fnr_high")
        if cand_fnr is not None and base_fnr is not None:
            ok = cand_fnr <= base_fnr + fnr_high_tolerance
            checks.append(GateCheck(
                "fnr_high_regression", ok,
                f"고등급 미탐율 후보={cand_fnr:.4f} vs 기준={base_fnr:.4f} "
                f"(허용 +{fnr_high_tolerance:.3f}) → {'OK' if ok else '악화-거부'}",
                cand_fnr, base_fnr,
            ))
        cand_f1 = _get_float(cand, "f1_macro", "test_f1_macro")
        base_f1 = _get_float(base, "f1_macro", "test_f1_macro")
        if cand_f1 is not None and base_f1 is not None:
            ok = cand_f1 >= base_f1 - f1_drop_tolerance
            checks.append(GateCheck(
                "f1_regression", ok,
                f"F1-macro 후보={cand_f1:.4f} vs 기준={base_f1:.4f} "
                f"(허용 -{f1_drop_tolerance:.3f}) → {'OK' if ok else '하락-거부'}",
                cand_f1, base_f1,
            ))
    elif require_baseline:
        checks.append(GateCheck(
            "baseline_present", False,
            "활성 baseline 없음 + require_baseline=True — 미통과(엄격 운영)",
        ))
    else:
        checks.append(GateCheck(
            "baseline_present", True,
            "활성 baseline 없음 — 최초 배포로 간주(degenerate 아니면 통과)",
        ))
        # [NEW-M2] 최초 배포 절대 FNR floor — baseline 비교가 불가능한 첫 모델의 미탐 하한.
        # floor 미설정(None)이면 동작 보존(첫 모델 통과). cand_fnr None(미보유)이면 위
        # fnr_high_present 가 이미 fail-closed 거부하므로 여기선 값이 있을 때만 검사.
        if first_deploy_fnr_high_max is not None and cand_fnr is not None:
            ok = cand_fnr <= first_deploy_fnr_high_max
            checks.append(GateCheck(
                "first_deploy_fnr_floor", ok,
                f"최초배포 고등급 미탐율 후보={cand_fnr:.4f} ≤ floor={first_deploy_fnr_high_max:.4f} "
                f"→ {'OK' if ok else 'floor 초과-거부'}",
                cand_fnr, first_deploy_fnr_high_max,
            ))

    passed = all(c.passed for c in checks)
    failed = [c.name for c in checks if not c.passed]
    reason = (
        "all gate checks passed"
        if passed
        else "blocked by: " + ", ".join(failed)
    )
    return DeployDecision(
        passed=passed,
        reason=reason,
        checks=checks,
        candidate_version=candidate_version,
        baseline_version=baseline_version,
        had_baseline=had_baseline,
    )
