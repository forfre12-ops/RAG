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

import json as _json
import logging as _logging
import math
import os as _os
from dataclasses import dataclass, asdict, field
from pathlib import Path as _Path


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


def publish_to_prom(report: DriftReport) -> None:
    """A4: prom_metrics.py의 Gauge에 직접 set. tasks.drift_tick이 호출.

    prom_metrics import는 함수 안에서 — module-load 시점에 circular 회피.
    """
    try:
        from lloydk.api import prom_metrics  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger = __import__("logging").getLogger(__name__)
        logger.warning("publish_to_prom skipped (prom_metrics unavailable): %s", exc)
        return

    prom_metrics.DRIFT_KL_DIVERGENCE.set(report.kl_divergence)
    prom_metrics.DRIFT_COSINE_MEAN.set(report.cosine_mean)
    prom_metrics.DRIFT_COSINE_STD.set(report.cosine_std)
    prom_metrics.DRIFT_ALERT.set(1.0 if report.alert else 0.0)
    prom_metrics.DRIFT_SAMPLE_SIZE.set(float(report.sample_size))


# ---------------------------------------------------------------------------
# A4: train centroid 영속 저장 + 운영 임베딩 표본 fetch
# ---------------------------------------------------------------------------

_logger = _logging.getLogger(__name__)

# 기본 저장 위치 — env로 override 가능. 운영에서는 MinIO/S3로 옮길 수 있음.
_DEFAULT_CENTROID_PATH = _os.environ.get(
    "LLOYDK_DRIFT_CENTROID_PATH",
    str(_Path(__file__).resolve().parents[3] / "datasets" / "drift_train_centroid.json"),
)


def save_train_centroid(vectors: list[list[float]], *, path: str | None = None) -> str:
    """학습 시점 임베딩 표본으로부터 centroid + 히스토그램을 JSON으로 저장.

    drift 비교 기준점. 학습 파이프라인이 학습 종료 시 1회 호출.
    """
    path = path or _DEFAULT_CENTROID_PATH
    centroid = _centroid(vectors)
    cos = [cosine(v, centroid) for v in vectors]
    hist = _histogram(cos, bins=20)
    payload = {
        "centroid": centroid,
        "histogram": hist,
        "sample_size": len(vectors),
        "dim": len(centroid),
    }
    p = _Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(payload), encoding="utf-8")
    _logger.info("drift centroid saved: path=%s n=%d dim=%d", path, len(vectors), len(centroid))
    return str(p)


def load_train_centroid(path: str | None = None) -> dict | None:
    """저장된 centroid + histogram을 로드. 미존재 시 None."""
    path = path or _DEFAULT_CENTROID_PATH
    p = _Path(path)
    if not p.exists():
        return None
    try:
        return _json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("drift centroid load failed: path=%s err=%s", path, exc)
        return None


def fetch_recent_prod_embeddings(*, limit: int = 200) -> list[list[float]]:
    """최근 운영 임베딩 표본을 vectorstore에서 가져온다.

    구현 노트: 현 PoC의 InMemoryVectorStore에는 시간 인덱스가 없어
    전체 청크 중 마지막 N개를 그대로 표본으로 사용. ES 백엔드 도입 후
    timestamp range로 교체할 것 (P2).
    """
    try:
        from lloydk.adapters.vectorstore import build_store  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        _logger.debug("fetch_recent_prod_embeddings skipped (vectorstore unavailable): %s", exc)
        return []
    try:
        # force_memory=False면 env VECTOR_BACKEND 따름. ES 미가용 시 InMemory 폴백.
        store = build_store()
    except Exception as exc:  # noqa: BLE001
        _logger.debug("vectorstore init failed: %s", exc)
        return []

    # 어댑터별 표본 메서드 (모든 백엔드가 구현하진 않음 — 폴백 빈 리스트)
    sample_fn = getattr(store, "sample_vectors", None)
    if callable(sample_fn):
        try:
            return list(sample_fn(limit=limit))
        except Exception as exc:  # noqa: BLE001
            _logger.debug("sample_vectors failed: %s", exc)
            return []
    return []


def run_drift_check(*, limit: int = 200, threshold: float = 0.5) -> DriftReport:
    """train centroid + 최근 운영 표본으로 drift 비교 → 게이지 갱신 후 보고서 반환.

    centroid 미저장 또는 운영 표본 부재 시 빈 DriftReport (alert=False) 반환.
    """
    train = load_train_centroid()
    prod = fetch_recent_prod_embeddings(limit=limit)
    if not train or not prod:
        report = DriftReport(
            sample_size=len(prod),
            cosine_mean=0.0,
            cosine_std=0.0,
            kl_divergence=0.0,
            threshold_alert=threshold,
        )
        publish_to_prom(report)
        _logger.info(
            "drift check skipped: train_centroid=%s prod_n=%d",
            "yes" if train else "no", len(prod),
        )
        return report

    # train_vectors가 직접 필요한 게 아니라 centroid 한 점이면 충분.
    # compute_drift는 vectors 리스트를 받으므로 centroid 1점을 train_vectors로 전달.
    train_centroid_vec = train["centroid"]
    report = compute_drift([train_centroid_vec], prod, threshold_alert=threshold)
    publish_to_prom(report)
    return report
