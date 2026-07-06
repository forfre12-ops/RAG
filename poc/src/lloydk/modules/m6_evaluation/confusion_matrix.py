"""Confusion Matrix + 고등급→저등급 미탐(underclass) 알람.

핵심 위험:
- 정답 TS(특급기밀)인 문서를 S3(공개)로 분류 → 영업비밀 유출
- 정답 S1(1급 비밀)를 S2(대외비)로 분류 → 권한 약화
이런 셀의 절댓값·비율이 알람 임계 초과 시 high-priority 보정 요청.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from lloydk.modules.m3_labeling.seeds import GRADE_ORDER

DEFAULT_LABELS = list(GRADE_ORDER)  # ["TS","S1","S2","S3"] — 단일 소스(seeds.GRADE_ORDER)에서 파생


@dataclass
class UnderclassAlarm:
    """고등급 정답을 저등급으로 분류한 셀."""
    truth: str
    pred: str
    count: int
    rate: float           # 해당 truth 등급 row 합 대비 비율
    severity: str         # critical/high/medium/low — order 차이로 결정

    def to_dict(self) -> dict:
        return {
            "truth": self.truth,
            "pred": self.pred,
            "count": self.count,
            "rate": self.rate,
            "severity": self.severity,
        }


@dataclass
class ConfusionMatrixResult:
    labels: list[str]
    matrix: list[list[int]]              # rows = truth, cols = pred
    alarms: list[UnderclassAlarm] = field(default_factory=list)
    total_underclass: int = 0
    total_samples: int = 0

    def to_dict(self) -> dict:
        return {
            "labels": self.labels,
            "matrix": self.matrix,
            "alarms": [a.to_dict() for a in self.alarms],
            "total_underclass": self.total_underclass,
            "total_samples": self.total_samples,
        }


def build_confusion_matrix(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    *,
    labels: Sequence[str] | None = None,
    alarm_rate_threshold: float = 0.05,
    alarm_count_threshold: int = 1,
) -> ConfusionMatrixResult:
    """CM 생성 + underclass 알람 탐지.

    파라미터:
    - alarm_rate_threshold: 해당 truth 등급 row 합 대비 알람 셀 비율 임계 (기본 5%)
    - alarm_count_threshold: 절댓값 임계 (기본 1건 — 단 1건이라도 critical은 알람)
    """
    label_list = list(labels) if labels else list(DEFAULT_LABELS)
    n = len(y_true)
    if n == 0 or n != len(y_pred):
        return ConfusionMatrixResult(labels=label_list, matrix=[], alarms=[], total_underclass=0, total_samples=0)

    # 직접 계산 — sklearn 의존 줄임 (이 모듈은 metrics.py와 별개 경량 사용 가능)
    idx = {lbl: i for i, lbl in enumerate(label_list)}
    n_lbl = len(label_list)
    cm = np.zeros((n_lbl, n_lbl), dtype=int)
    for t, p in zip(y_true, y_pred):
        if t in idx and p in idx:
            cm[idx[t], idx[p]] += 1

    alarms = detect_underclass_alarms(
        cm,
        labels=label_list,
        alarm_rate_threshold=alarm_rate_threshold,
        alarm_count_threshold=alarm_count_threshold,
    )
    total_underclass = sum(a.count for a in alarms)

    return ConfusionMatrixResult(
        labels=label_list,
        matrix=cm.tolist(),
        alarms=alarms,
        total_underclass=total_underclass,
        total_samples=n,
    )


def detect_underclass_alarms(
    cm: np.ndarray,
    *,
    labels: Sequence[str],
    alarm_rate_threshold: float = 0.05,
    alarm_count_threshold: int = 1,
) -> list[UnderclassAlarm]:
    """row=truth(고등급), col=pred(저등급) 셀이 0이 아니면 알람.

    severity:
    - critical: order 차이 ≥ 3 (예: TS→S3) — 최악의 미탐
    - high: order 차이 = 2 (예: TS→S2, S1→S3)
    - medium: order 차이 = 1 (예: TS→S1, S1→S2, S2→S3)
    - low: 같은 등급(대각선)에서 발생할 수 없음 — 미사용
    """
    alarms: list[UnderclassAlarm] = []
    label_list = list(labels)
    for i, t in enumerate(label_list):
        row_sum = int(cm[i].sum())
        if row_sum == 0:
            continue
        for j, p in enumerate(label_list):
            if i == j:
                continue
            t_order = GRADE_ORDER.get(t)
            p_order = GRADE_ORDER.get(p)
            if t_order is None or p_order is None:
                continue
            if p_order <= t_order:
                continue  # 동급 또는 과탐(overclass)은 알람 대상 아님
            count = int(cm[i, j])
            if count == 0:
                continue
            rate = count / row_sum
            sev = _severity_from_order_diff(p_order - t_order)
            # [M-confusion-gate] critical·high severity 미탐은 count>=1이면 무조건 알람
            # (rate 게이팅 제거). 고위험 미탐(예: TS→S2 = high)이 소량이면 예전엔 rate
            # 미달로 경보가 누락됐다 — fail-SECURE 위반. rate는 medium/low 같은 저위험
            # 셀의 노이즈 게이팅에만 쓴다.
            if sev in ("critical", "high"):
                _fire = count >= 1
            else:
                _fire = count >= alarm_count_threshold and rate >= alarm_rate_threshold
            if _fire:
                alarms.append(
                    UnderclassAlarm(
                        truth=t,
                        pred=p,
                        count=count,
                        rate=float(rate),
                        severity=sev,
                    )
                )
    # 심각도 순으로 정렬 (critical 먼저)
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    alarms.sort(key=lambda a: (sev_order.get(a.severity, 99), -a.count))
    return alarms


def _severity_from_order_diff(diff: int) -> str:
    if diff >= 3:
        return "critical"
    if diff == 2:
        return "high"
    if diff == 1:
        return "medium"
    return "low"
