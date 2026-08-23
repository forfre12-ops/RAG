"""온도 스케일링(temperature scaling) 핵심 — logits 기반 T 적합.

서빙 보정(m5 InferencePipeline 이 model_dir/temperature.json 자동 로드)이 쓰는 T 산출용.
trainer(학습 종료 시 자동 호출)와 scripts/calibrate_classifier.py(수동/검증)가 공유한다 —
이전엔 동일 로직이 스크립트에만 있어 trainer 가 재구현/호출할 수 없었다(중복·자동연결 부재).

순수 math 의존(외부 불요) → DB·GPU 없이 단위 테스트 가능.
주의: 여기 expected_calibration_error 는 **logits 기반**(temperature 스윕용)으로,
m6_evaluation.calibration.expected_calibration_error(confidence·correct 기반, 평가 리포트용)
와 입력이 다르다 — 이름이 같아도 용도가 분리돼 있다.
"""

from __future__ import annotations

import math
from typing import Sequence


def softmax(logits: Sequence[float], temperature: float = 1.0) -> list[float]:
    if temperature <= 0:
        temperature = 1e-3
    scaled = [v / temperature for v in logits]
    m = max(scaled)
    exps = [math.exp(s - m) for s in scaled]
    z = sum(exps)
    return [e / z for e in exps]


def neg_log_likelihood(
    logits_list: Sequence[Sequence[float]], labels: Sequence[int], T: float
) -> float:
    nll = 0.0
    for logits, y in zip(logits_list, labels):
        probs = softmax(logits, temperature=T)
        p = max(probs[y], 1e-9)
        nll -= math.log(p)
    return nll / max(len(labels), 1)


def expected_calibration_error(
    logits_list: Sequence[Sequence[float]], labels: Sequence[int], T: float, n_bins: int = 10
) -> float:
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for logits, y in zip(logits_list, labels):
        probs = softmax(logits, temperature=T)
        pred = max(range(len(probs)), key=lambda i: probs[i])
        conf = probs[pred]
        bin_idx = min(int(conf * n_bins), n_bins - 1)
        bins[bin_idx].append((conf, 1 if pred == y else 0))

    ece = 0.0
    n = max(len(labels), 1)
    for bucket in bins:
        if not bucket:
            continue
        acc = sum(b[1] for b in bucket) / len(bucket)
        conf = sum(b[0] for b in bucket) / len(bucket)
        ece += (len(bucket) / n) * abs(conf - acc)
    return ece


def find_best_temperature(
    logits_list: Sequence[Sequence[float]],
    labels: Sequence[int],
    lo: float = 0.05,
    hi: float = 5.0,
    steps: int = 200,
) -> float:
    """NLL 최소화 grid search 로 최적 T 탐색(낮은 T=날카롭게, 높은 T=부드럽게).

    over-confident 모델은 T>1 로 분포를 부드럽게 해 OOD 과신을 완화(MEMORY: 서빙 T≈3).
    """
    best_T = 1.0
    best_nll = neg_log_likelihood(logits_list, labels, 1.0)
    for i in range(steps + 1):
        T = lo + (hi - lo) * (i / steps)
        nll = neg_log_likelihood(logits_list, labels, T)
        if nll < best_nll:
            best_nll = nll
            best_T = T
    return best_T


def fit_temperature_report(
    logits_list: Sequence[Sequence[float]], labels: Sequence[int], *, n_bins: int = 10
) -> dict:
    """T 적합 + ECE/NLL before/after 를 temperature.json 스키마 dict 로 반환."""
    T_star = find_best_temperature(logits_list, labels)
    return {
        "temperature": round(T_star, 4),
        "ece_before": round(expected_calibration_error(logits_list, labels, 1.0, n_bins), 4),
        "ece_after": round(expected_calibration_error(logits_list, labels, T_star, n_bins), 4),
        "nll_before": round(neg_log_likelihood(logits_list, labels, 1.0), 4),
        "nll_after": round(neg_log_likelihood(logits_list, labels, T_star), 4),
        "n_samples": len(labels),
    }
