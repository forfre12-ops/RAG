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

from lloydk.modules.m6_evaluation.eval_cards import (
    VERDICT_FAIL,
    VERDICT_INCONCLUSIVE,
    VERDICT_PASS,
)

# 게이트 기본 허용오차 — config.py(Settings)에서 주입 가능. 안전 기본:
#   fnr_high는 악화 0 허용(tolerance=0.0)이 가장 안전하나, 평가셋 표본분산(±1~2건)으로
#   동급 모델이 반려되는 것을 막기 위해 소폭(0.02) 허용. f1은 5%p 하락까지만 용인.
DEFAULT_FNR_HIGH_TOLERANCE = 0.02
DEFAULT_F1_DROP_TOLERANCE = 0.05
DEFAULT_DEGENERATE_COL_SHARE = 0.99
# 메타모픽 순방향(미탐 FN) 회귀 hard-block 기준. 위반율 Wilson CI 하한이 ceiling 초과면 거부.
# ceiling=0.0(기본) = CI로 뒷받침되는 순방향 회귀가 하나라도 있으면 거부(FN-first, 최고 보수).
DEFAULT_METAMORPHIC_FORWARD_CEILING = 0.0
DEFAULT_METAMORPHIC_MIN_N = 5  # 순방향 쌍 표본이 이 미만이면 측정 불가(게이트 안 깸)


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


DEFAULT_ANCHOR_HIGH_GRADES = ("TS", "S1")


def summarize_anchor_high_grade(
    anchor_report: Optional[Mapping],
    *,
    high_grades: Sequence[str] = DEFAULT_ANCHOR_HIGH_GRADES,
) -> dict:
    """앵커 평가 카드(build_eval_cards.to_dict)에서 **고등급(TS/S1) under-class 미탐** 신호 요약.

    합성 test.jsonl만 보던 게이트(죽음의 나선 #4)에 **외부 사실 앵커**로 측정한 미탐을
    주입하기 위한 순수 해석기. 카드 verdict 의미를 그대로 존중한다:
      - FAIL = 미탐 하한 CI가 천장 초과(앵커가 실제로 고등급 미탐을 보임) → 게이트 hard block.
      - INCONCLUSIVE = 표본부족/CI과대 → 측정 불가(PASS 아님). 이진 게이트를 깨진 않되 surfaced.
        ('측정 못 함'으로 자동활성을 막는 일은 eval_ready locked 게이트 소관 — 역할 분리.)
    """
    cards = list(anchor_report.get("cards", [])) if anchor_report else []
    hi = [c for c in cards if c.get("grade") in set(high_grades)]
    fails = [c for c in hi if c.get("verdict") == VERDICT_FAIL]
    passes = [c for c in hi if c.get("verdict") == VERDICT_PASS]
    incon = [c for c in hi if c.get("verdict") == VERDICT_INCONCLUSIVE]
    worst = max((float(c.get("underclass_fnr", 0.0)) for c in hi), default=None)
    return {
        "high_grade_cards": len(hi),
        "fail": len(fails),
        "pass": len(passes),
        "inconclusive": len(incon),
        "worst_underclass_fnr": worst,
        "fail_cells": [f"{c.get('slice')}/{c.get('grade')}" for c in fails],
    }


def summarize_metamorphic(
    metamorphic_report: Optional[Mapping],
    *,
    forward_ceiling: float = DEFAULT_METAMORPHIC_FORWARD_CEILING,
    min_n: int = DEFAULT_METAMORPHIC_MIN_N,
) -> dict:
    """메타모픽 리포트(build_metamorphic_report.to_dict)에서 **순방향(미탐 FN) 회귀** 신호 요약.

    순방향 위반 = 사실을 유지한 채 문체만 바꿨는데 등급이 내려감(고등급 미탐 — 위험). 메타모픽은
    '실패만 정보' 원칙이라 통과(violations=0)는 신호가 아니다. 위반율 Wilson CI 하한이 ceiling을
    초과하면 회귀 확정(hard block). 표본이 min_n 미만이면 measurable=False(측정 불가 — 게이트를
    깨지 않되 surfaced; 자동활성 veto는 locked-eval 소관, 역할 분리). 역방향/과분류는 미탐이 아니라
    (과분류=FP) deploy hard-block에는 쓰지 않는다(미탐-우선 원칙 유지) — 필요 시 별도 신호로 확장.
    """
    fwd = dict((metamorphic_report or {}).get("forward") or {})
    n = int(fwd.get("n", 0) or 0)
    v = int(fwd.get("violations", 0) or 0)
    ci = fwd.get("ci") or [0.0, 0.0]
    try:
        ci_low = float(ci[0])
    except (TypeError, ValueError, IndexError):
        ci_low = 0.0
    measurable = n >= max(1, min_n)
    regression = bool(measurable and v > 0 and ci_low > forward_ceiling)
    failures = list((metamorphic_report or {}).get("forward_failures", []) or [])[:5]
    return {
        "n": n,
        "violations": v,
        "ci_low": ci_low,
        "measurable": measurable,
        "regression": regression,
        "failures": failures,
    }


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
    anchor_report: Optional[Mapping] = None,
    anchor_high_grades: Sequence[str] = DEFAULT_ANCHOR_HIGH_GRADES,
    require_anchor: bool = False,
    metamorphic_report: Optional[Mapping] = None,
    metamorphic_forward_ceiling: float = DEFAULT_METAMORPHIC_FORWARD_CEILING,
    metamorphic_min_n: int = DEFAULT_METAMORPHIC_MIN_N,
    require_metamorphic: bool = False,
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
        anchor_report: [앵커 연결] 후보를 **외부 사실 앵커 코퍼스**로 측정한 평가 카드
            (build_eval_cards.to_dict). 합성 test.jsonl만 보던 게이트(죽음의 나선 #4)에 실측
            미탐 신호를 주입한다. 고등급(TS/S1) 카드가 **FAIL**(미탐 하한 CI>천장)이면 hard block.
            INCONCLUSIVE는 깨지 않음(측정 불가 — 자동활성 veto는 eval_ready locked 게이트 소관).
            None(기본)이면 검사 생략(동작 보존).
        anchor_high_grades: 앵커에서 미탐을 hard-block 기준으로 볼 고등급 집합(기본 TS·S1).
        metamorphic_report: [메타모픽 연결] 후보를 **최소쌍 메타모픽 회귀**로 측정한 리포트
            (build_metamorphic_report.to_dict). 사실 유지·문체 변경에 등급이 내려가는 **순방향 미탐(FN)**
            회귀가 CI로 뒷받침되면 hard block. anchor_report(외부 사실 미탐)와 상보 — 정답셋 아닌
            회귀테스트(실패만 정보)라 합성 천장과 무관. None(기본)이면 검사 생략(동작 보존).
        metamorphic_forward_ceiling: 순방향 위반율 CI 하한이 이 값을 초과하면 회귀 확정(기본 0.0).
        metamorphic_min_n: 순방향 쌍 표본이 이 미만이면 측정 불가(게이트 안 깸, 기본 5).
        require_anchor / require_metamorphic: [강제 옵트인] True 면 해당 리포트 '미제공=생략'을
            통과(passed=True)가 아니라 hard block(passed=False)으로 승격한다 — 앵커/메타모픽을 켜둔
            운영에서 리포트 생성이 조용히 깨져 게이트가 통과하는 fail-open 을 막는다(fail-closed).
            기본 False=동작 보존(리포트 없으면 '검사 생략'으로 가시화만 하고 통과). 리포트가 있으나
            표본 부족(측정 불가)인 경우는 강제하지 않는다 — 그건 데이터 천장이지 생성 실패가 아니다.
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

    # ── 4. 앵커 고등급 미탐 (외부 사실 앵커 측정 — 합성 맹목 보완) ─────────────────
    if anchor_report is not None:
        a = summarize_anchor_high_grade(anchor_report, high_grades=anchor_high_grades)
        if a["fail"] > 0:
            checks.append(GateCheck(
                "anchor_high_grade_miss", False,
                f"앵커 고등급 미탐 측정됨(FAIL {a['fail']}칸: {a['fail_cells']}) — "
                f"worst under-class FNR={a['worst_underclass_fnr']} → 거부",
                a["worst_underclass_fnr"],
            ))
        elif a["high_grade_cards"] == 0:
            checks.append(GateCheck(
                "anchor_high_grade_miss", True,
                "앵커 고등급(TS/S1) 카드 없음 — 앵커 미탐 검사 생략",
            ))
        else:
            checks.append(GateCheck(
                "anchor_high_grade_miss", True,
                f"앵커 고등급 미탐 FAIL 없음(PASS {a['pass']}·INCONCLUSIVE {a['inconclusive']}칸; "
                f"INCONCLUSIVE는 측정불가 — 자동활성 veto는 locked-eval 소관)",
                a["worst_underclass_fnr"],
            ))
    elif require_anchor:
        # [강제 옵트인 fail-closed] 앵커를 켜둔(require_anchor) 운영에서 리포트가 없다 = 앵커 생성이
        # 조용히 깨졌는데 게이트가 통과하는 fail-open 을 차단. '미측정'을 통과로 숨기지 않고 hard block.
        checks.append(GateCheck(
            "anchor_eval_skipped", False,
            "앵커(외부 사실) 리포트 미제공인데 require_external_fact_eval=ON — 외부 사실 미탐 검사가 "
            "강제인데 앵커 리포트가 없어 거부(생성 실패 가능성; fail-closed).",
        ))
    else:
        # [게이트=가시성] 앵커 미제공(deploy_gate_anchor_eval_enabled=False 기본 또는 리포트 부재)
        # 이면 외부 사실 미탐 검사를 **하지 않았음**을 명시 기록 — 통과 결정이 '외부 사실을 본 적
        # 없음'을 무음으로 숨기지 않게 한다. passed=True(강제 ON 아님 — 앵커는 CPU 추론비용 opt-in).
        checks.append(GateCheck(
            "anchor_eval_skipped", True,
            "앵커(외부 사실) 리포트 미제공 — 고등급 미탐 앵커 검사 미수행. 게이트는 합성/기준 "
            "지표만으로 판정(외부 사실 신호 부재 가시화; 운영 자동활성 veto는 locked-eval 소관).",
        ))

    # ── 5. 메타모픽 순방향 회귀 (문체만 바꿔도 등급↓ = 미탐 — 실패만 정보) ─────────────
    if metamorphic_report is not None:
        m = summarize_metamorphic(
            metamorphic_report,
            forward_ceiling=metamorphic_forward_ceiling,
            min_n=metamorphic_min_n,
        )
        if m["regression"]:
            ids = [f.get("anchor_id") for f in m["failures"][:3]]
            checks.append(GateCheck(
                "metamorphic_forward_regression", False,
                f"메타모픽 순방향 미탐 회귀(사실 유지·문체 변경에 등급↓): 위반 {m['violations']}/{m['n']} "
                f"· 위반율 CI하한={m['ci_low']:.4f} > ceiling {metamorphic_forward_ceiling:.3f} → 거부 "
                f"(예: {ids})",
                m["ci_low"],
            ))
        elif not m["measurable"]:
            checks.append(GateCheck(
                "metamorphic_forward_regression", True,
                f"메타모픽 순방향 쌍 부족(n={m['n']} < {metamorphic_min_n}) — 측정 불가(검사 생략)",
            ))
        else:
            checks.append(GateCheck(
                "metamorphic_forward_regression", True,
                f"메타모픽 순방향 미탐 회귀 없음(위반 {m['violations']}/{m['n']}, CI하한={m['ci_low']:.4f})",
            ))
    elif require_metamorphic:
        # [강제 옵트인 fail-closed] 리포트 경로를 설정한(require_metamorphic) 운영에서 리포트가 없다 =
        # 메타모픽 생성이 조용히 깨졌는데 게이트가 통과하는 fail-open 을 차단. hard block(fail-closed).
        checks.append(GateCheck(
            "metamorphic_eval_skipped", False,
            "메타모픽 리포트 미제공인데 require_external_fact_eval=ON — 문체변경 순방향 미탐 회귀 "
            "검사가 강제인데 리포트가 없어 거부(생성 실패 가능성; fail-closed).",
        ))
    else:
        # [게이트=가시성] 메타모픽 미제공(deploy_gate_metamorphic_report_path 미설정 기본)이면
        # 문체변경 순방향 미탐 회귀 검사를 미수행 — 통과가 이 신호 부재 하 판정임을 명시.
        checks.append(GateCheck(
            "metamorphic_eval_skipped", True,
            "메타모픽 리포트 미제공 — 문체변경 순방향 미탐 회귀 검사 미수행. 게이트 통과는 이 "
            "외부 회귀신호 부재 하에서의 판정임을 가시화(강제 아님 — 리포트 경로 opt-in).",
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
