"""P1-B4: Embedding drift 모니터.

production embedding 분포 vs 학습 시 분포 비교로 시간 경과 성능 저하 조기 감지.

지표:
- mean cosine similarity (centroid 대비)
- 코사인 거리 분포 KL divergence (히스토그램 기반)
- top-1 nearest-cluster purity (선택)

산출:
- DriftReport (JSON 직렬화 가능)
- Prometheus gauge로 노출 (lloydk_drift_kl, lloydk_drift_cosine_mean)

dryrun에서도 동작 — InMemory + Hash 임베딩 사용 가능.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field


@dataclass
class DriftReport:
    sample_size: int
    cosine_mean: float
    cosine_std: float
    kl_divergence: float  # 0이면 동일 분포, 클수록 drift
    bins: int = 20
    histogram_train: list[float] = field(default_factory=list)
    histogram_prod: list[float] = field(default_factory=list)
    threshold_alert: float = 0.5  # KL ≥ 0.5 시 알람 권장
    alert: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    sums = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            sums[i] += x
    n = len(vectors)
    return [s / n for s in sums]


def _histogram(values: list[float], bins: int = 20, low: float = -1.0, high: float = 1.0) -> list[float]:
    hist = [0.0] * bins
    if not values:
        return hist
    span = high - low
    for v in values:
        idx = int(min(max((v - low) / span, 0.0), 0.9999) * bins)
        hist[idx] += 1.0
    total = sum(hist)
    if total > 0:
        hist = [h / total for h in hist]
    return hist


def _kl_divergence(p: list[float], q: list[float], eps: float = 1e-9) -> float:
    """KL(P||Q). 양 분포 모두 합=1 정규화 가정. eps로 0 회피."""
    if len(p) != len(q):
        return 0.0
    kl = 0.0
    for pi, qi in zip(p, q):
        pi = max(pi, eps)
        qi = max(qi, eps)
        kl += pi * math.log(pi / qi)
    return max(0.0, kl)


def compute_drift(
    train_vectors: list[list[float]],
    prod_vectors: list[list[float]],
    *,
    bins: int = 20,
    threshold_alert: float = 0.5,
) -> DriftReport:
    """학습 시 임베딩과 운영 임베딩 비교.

    Args:
        train_vectors: 학습 데이터의 임베딩 표본 (centroid 산출)
        prod_vectors: 운영 환경 최근 임베딩 표본
        bins: 히스토그램 빈 수
        threshold_alert: KL divergence 알람 임계

    Returns:
        DriftReport (alert=True면 drift 감지)
    """
    if not train_vectors or not prod_vectors:
        return DriftReport(
            sample_size=0,
            cosine_mean=0.0,
            cosine_std=0.0,
            kl_divergence=0.0,
            bins=bins,
            threshold_alert=threshold_alert,
        )

    train_c = _centroid(train_vectors)
    train_cos = [cosine(v, train_c) for v in train_vectors]
    prod_cos = [cosine(v, train_c) for v in prod_vectors]

    p_hist = _histogram(train_cos, bins=bins)
    q_hist = _histogram(prod_cos, bins=bins)
    kl = _kl_divergence(p_hist, q_hist)

    mean = sum(prod_cos) / len(prod_cos)
    var = sum((x - mean) ** 2 for x in prod_cos) / len(prod_cos)
    std = math.sqrt(var)

    return DriftReport(
        sample_size=len(prod_vectors),
        cosine_mean=round(mean, 4),
        cosine_std=round(std, 4),
        kl_divergence=round(kl, 4),
        bins=bins,
        histogram_train=[round(h, 4) for h in p_hist],
        histogram_prod=[round(h, 4) for h in q_hist],
        threshold_alert=threshold_alert,
        alert=kl >= threshold_alert,
    )


def export_to_prometheus(report: DriftReport) -> dict[str, float]:
    """Prometheus gauge 노출용 메트릭 값.

    수집기(prom_metrics.py)가 본 dict을 읽어 Gauge.set() 호출.
    """
    return {
        "lloydk_drift_cosine_mean": report.cosine_mean,
        "lloydk_drift_cosine_std": report.cosine_std,
        "lloydk_drift_kl_divergence": report.kl_divergence,
        "lloydk_drift_alert": 1.0 if report.alert else 0.0,
        "lloydk_drift_sample_size": float(report.sample_size),
    }
