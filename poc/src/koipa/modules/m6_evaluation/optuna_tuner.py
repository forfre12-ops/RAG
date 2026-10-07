"""Optuna 게이트·온도·fusion 튜너 — P1 하네스 (스켈레톤).

목적
  골든셋 기준으로 **재학습 없는 후처리 노브**(온도·게이트 임계·escalation τ 등)를 자동 탐색.
  maximize F1(자동확정 품질 대리) **subject to** FNR_고등급(TS·S1) ≤ target.
  전부 CPU·무GPU(평가만). 평가는 기존 `evaluate_via_serving`를 그대로 재사용한다.

전제
  정밀 튜닝은 **N 충분한 1,000 사람검수 골든**에서. 작은 holdout(77건)은 파이프라인 검증용
  (작은 N에서의 임계 튜닝은 과적합 — driver가 val/test 분리로 점검).

노브 동적성(중요)
  - `classifier_escalation_tau` 등 대부분은 `evaluate_via_serving`이 settings에서 **즉시 읽음**
    → 파이프라인 1회 로드 후 재사용(빠름). (eval_serving_path.py에서 확인)
  - `rule_high_risk_weight_multiplier`만 **룰 엔진 생성 시점**에 반영 → 재생성 필요(느림).
    기본 search space에서 제외(REBUILD_KNOBS). 켜려면 driver에서 추가.

의존성: optuna (Apache-2.0). 폐쇄망은 wheel vendoring. import는 run_study에서 지연.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, Sequence

# 파이프라인 재생성이 필요한(=동적이지 않은) 노브. 기본 search space에는 넣지 않는다.
REBUILD_KNOBS = {"rule_high_risk_weight_multiplier"}

# 고등급(방향성 FNR을 0으로 묶을 대상)
GRADES_HIGH = ("TS", "S1")


@dataclasses.dataclass
class TuneConfig:
    model_dir: str
    fnr_high_target: float = 0.0   # 고등급 방향성 FNR 상한(제약). 0 = 미탐 0 요구.
    penalty: float = 10.0          # 제약 위반 페널티 계수(부드러운 수렴용)
    n_trials: int = 50
    seed: int = 42


def suggest_knobs(trial: Any) -> dict:
    """search space — 재학습 없는 후처리 노브(기본=동적 노브만).

    반환 key = settings 필드명, value = trial 제안값.
    (param 이름은 짧게, settings 키는 정확히.)
    """
    return {
        "classifier_temperature": trial.suggest_float("temperature", 1.0, 5.0),
        "classifier_escalation_tau": trial.suggest_float("escalation_tau", 0.10, 0.50),
        "review_confidence_threshold": trial.suggest_float("conf_threshold", 0.50, 0.95),
        "rule_fallback_min_evidence": trial.suggest_float("fallback_min_evidence", 0.5, 1.5),
        "agreement_gate_enabled": trial.suggest_categorical("agreement_gate", [True, False]),
        # 재생성 필요 노브(옵션) — 켜면 trial마다 모델 재로드(느림):
        # "rule_high_risk_weight_multiplier": trial.suggest_float("rule_mult", 0.5, 2.0),
    }


def _apply_knobs(settings: Any, knobs: dict) -> dict:
    """settings에 노브 적용, 원복용 이전값 dict 반환."""
    prev = {}
    for k, v in knobs.items():
        prev[k] = getattr(settings, k)
        setattr(settings, k, v)
    return prev


def _restore(settings: Any, prev: dict) -> None:
    for k, v in prev.items():
        setattr(settings, k, v)


def make_objective(rows: Sequence[dict], cfg: TuneConfig) -> Callable[[Any], float]:
    """Optuna objective 생성. 동적 노브만이면 파이프라인 1회 로드 후 재사용."""
    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415
    from koipa.modules.m6_evaluation.serving_eval import evaluate_via_serving  # noqa: PLC0415

    base_pipeline = InferencePipeline(model_dir=cfg.model_dir)  # 모델 1회 로드

    def objective(trial: Any) -> float:
        knobs = suggest_knobs(trial)
        need_rebuild = bool(set(knobs) & REBUILD_KNOBS)
        prev = _apply_knobs(settings, knobs)
        try:
            pipe = InferencePipeline(model_dir=cfg.model_dir) if need_rebuild else base_pipeline
            m = evaluate_via_serving(rows, pipeline=pipe, model_version=f"optuna_t{trial.number}")
            fnr_high = sum(m.fnr_underclass_by_grade.get(g, 0.0) for g in GRADES_HIGH) / len(GRADES_HIGH)
            trial.set_user_attr("f1_macro", round(m.f1_macro, 4))
            trial.set_user_attr("accuracy", round(m.accuracy, 4))
            trial.set_user_attr("fnr_high", round(fnr_high, 4))
            trial.set_user_attr("fnr_overall", round(m.fnr_underclass_overall, 4))
            # 제약: 고등급 FNR ≤ target. 위반분만큼 페널티(작을수록 좋음 → maximize에서 차감).
            violation = max(0.0, fnr_high - cfg.fnr_high_target)
            return m.f1_macro - cfg.penalty * violation
        finally:
            _restore(settings, prev)

    return objective


def run_study(rows: Sequence[dict], cfg: TuneConfig):
    """TPE 샘플러로 study 실행 후 반환."""
    import optuna  # noqa: PLC0415

    sampler = optuna.samplers.TPESampler(seed=cfg.seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(make_objective(rows, cfg), n_trials=cfg.n_trials)
    return study
