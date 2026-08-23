"""P1-B4: Embedding drift 모니터.

production embedding 분포 vs 학습 시 분포 비교로 시간 경과 성능 저하 조기 감지.

지표:
- mean cosine similarity (centroid 대비)
- 코사인 거리 분포 KL divergence (히스토그램 기반)
- top-1 nearest-cluster purity (선택)

산출:
- DriftReport (JSON 직렬화 가능)
- Prometheus gauge로 노출 (koipa_drift_kl, koipa_drift_cosine_mean)

dryrun에서도 동작 — InMemory + Hash 임베딩 사용 가능.
"""

from __future__ import annotations

import hashlib as _hashlib
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
    # --- #39: 보수적 drift 판정 보조 필드 (모두 기본값 → 기존 생성자 호출 호환) ---
    # alert_status: KL/cosine 판정의 신뢰 상태. "ok"=정상 산출, "alert"=임계 초과,
    #   "unknown"=bin 불일치/히스토그램 부재 등으로 KL 신뢰 불가(false-negative 위험 표기).
    alert_status: str = "ok"
    # raw_violation: 이번 점검에서 임계(단발 기준)를 초과했는지. 히스테리시스 판정 입력.
    raw_violation: bool = False
    # consecutive_violations: run_drift_check가 영속한 연속 위반 횟수(히스테리시스).
    consecutive_violations: int = 0
    # consecutive_required: alert=True가 되기 위해 필요한 연속 위반 횟수(설정 가능).
    consecutive_required: int = 1
    # cosine_mean_shift: 학습 분포 중심(=cos 1.0 기준) 대비 운영 cosine_mean 이탈량.
    #   |cosine_mean_shift| ≥ cosine_shift_threshold면 분포중심 이동 의심.
    cosine_mean_shift: float = 0.0
    cosine_shift_threshold: float = 0.0  # 0이면 cosine 이동 점검 비활성

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


def _resample_histogram(hist: list[float], target_bins: int) -> list[float]:
    """#39: 저장 histogram의 bin 수가 현재와 다를 때 선형보간으로 target_bins에 맞춘다.

    기존 코드는 bin 불일치 시 KL=0(=drift 없음)으로 폴백해 false-negative로
    drift를 은폐했다. 여기서는 [low, high] 구간을 공유한다는 가정 하에 누적분포(CDF)를
    선형보간으로 재샘플링하여 동일 bin 수의 근사 분포를 만든 뒤 비교에 사용한다.
    완벽한 재구성은 아니지만 'KL=0 은폐'보다 보수적(=drift를 놓치지 않는 방향).
    """
    src_bins = len(hist)
    if src_bins == 0 or target_bins <= 0:
        return [0.0] * max(target_bins, 0)
    if src_bins == target_bins:
        return list(hist)
    # 원본 누적분포(우측 경계 기준).
    cdf = []
    acc = 0.0
    for h in hist:
        acc += h
        cdf.append(acc)
    total = cdf[-1] if cdf else 0.0
    if total <= 0:
        return [0.0] * target_bins
    # target bin 경계에서 CDF를 선형보간 → 각 target bin 질량 = CDF 증분.
    def cdf_at(x: float) -> float:
        # x in [0,1] (구간 정규화 위치) → 보간된 누적값(0..total).
        pos = x * src_bins  # 원본 bin 단위 위치
        if pos <= 0:
            return 0.0
        if pos >= src_bins:
            return total
        lo = int(pos)
        frac = pos - lo
        left = cdf[lo - 1] if lo >= 1 else 0.0
        right = cdf[lo] if lo < src_bins else total
        return left + (right - left) * frac

    out = []
    prev = 0.0
    for b in range(1, target_bins + 1):
        c = cdf_at(b / target_bins)
        out.append(max(0.0, c - prev))
        prev = c
    s = sum(out)
    if s > 0:
        out = [v / s for v in out]
    return out


# #39: 연속위반(히스테리시스) 상태 영속 경로.
#   KOIPA_DRIFT_STATE_PATH가 있으면 그것을, 없으면 centroid 파일과 같은 디렉터리에
#   drift_state.json으로 둔다(centroid 경로를 tmp로 바꾸는 테스트가 자연히 격리됨).
def _default_state_path() -> str:
    explicit = _os.environ.get("KOIPA_DRIFT_STATE_PATH")
    if explicit:
        return explicit
    return str(_Path(_DEFAULT_CENTROID_PATH).resolve().parent / "drift_state.json")


def _centroid_fingerprint(train: dict | None) -> str:
    """드리프트 baseline(train centroid) 식별 지문 — baseline이 바뀌면 값이 바뀐다.

    히스테리시스 연속위반 카운트는 '이 baseline 대비' 누적이라, 재학습으로 centroid가
    덮어써지면(save_train_centroid는 고정 경로 overwrite) 옛 카운트는 무효 → 지문 불일치로
    리셋한다. 부동소수 직렬화 민감도를 피해 메타(sample_size·dim·len) + 앞 16성분 라운딩만 해시.
    """
    if not train:
        return ""
    c = train.get("centroid") or []
    basis = f"{train.get('sample_size')}:{train.get('dim')}:{len(c)}:"
    basis += ",".join(f"{float(x):.6f}" for x in c[:16])
    return _hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _load_violation_state(path: str | None = None, *, version: str | None = None) -> int:
    """직전까지 누적된 연속 위반 횟수를 로드. 없거나 읽기 실패 시 0.

    version(centroid 지문)이 주어지고 저장된 version과 다르면 baseline이 바뀐 것 → 0으로
    리셋(옛 baseline 대비 위반은 무효). 저장된 version이 없으면(구 상태파일) 그대로 사용(하위호환).
    """
    path = path or _default_state_path()
    p = _Path(path)
    if not p.exists():
        return 0
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
        stored_version = data.get("version")
        if version is not None and stored_version is not None and stored_version != version:
            _logger.info(
                "drift state reset: centroid baseline changed (was=%s now=%s) — 연속위반 0",
                stored_version, version,
            )
            return 0
        return int(data.get("consecutive_violations", 0))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("drift state load failed: path=%s err=%s", path, exc)
        return 0


def _save_violation_state(count: int, path: str | None = None, *, version: str | None = None) -> None:
    """연속 위반 횟수를 영속. 위반 아니면 0으로 리셋해 단발 스파이크가 누적 안 되게.

    version(centroid 지문)을 함께 저장해, 다음 로드에서 baseline 변경을 감지·리셋한다.
    """
    path = path or _default_state_path()
    p = _Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload: dict = {"consecutive_violations": int(count)}
        if version is not None:
            payload["version"] = version
        p.write_text(_json.dumps(payload), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _logger.warning("drift state save failed: path=%s err=%s", path, exc)


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

    kl_violation = kl >= threshold_alert
    return DriftReport(
        sample_size=len(prod_vectors),
        cosine_mean=round(mean, 4),
        cosine_std=round(std, 4),
        kl_divergence=round(kl, 4),
        bins=bins,
        histogram_train=[round(h, 4) for h in p_hist],
        histogram_prod=[round(h, 4) for h in q_hist],
        threshold_alert=threshold_alert,
        # 동작보존: compute_drift는 단발(single-shot) 판정 유지(기존 테스트 호환).
        alert=kl_violation,
        # #39: 단발 위반 플래그/상태 — run_drift_check 히스테리시스 입력으로도 사용.
        alert_status="alert" if kl_violation else "ok",
        raw_violation=kl_violation,
        consecutive_violations=1 if kl_violation else 0,
    )


def compute_drift_from_centroid(
    train_centroid: list[float],
    train_histogram: list[float],
    prod_vectors: list[list[float]],
    *,
    bins: int = 20,
    threshold_alert: float = 0.5,
    cosine_shift_threshold: float = 0.0,
) -> DriftReport:
    """저장된 학습 centroid + histogram으로 운영 표본 drift 비교.

    버그 수정(2026-06): 기존 run_drift_check는 centroid '한 점'을 train_vectors로
    compute_drift에 넘겼다. 그러면 _centroid([c])=c → train_cos=[cosine(c,c)]=[1.0]
    이 되어 학습분포 히스토그램이 value=1.0의 델타함수가 되고, 운영 cos가 1.0 근처가
    아니면 KL이 폭주하거나(혹은 prod도 1.0 부근이면 0) 사실상 무의미한 값이 됐다.
    여기서는 학습 시 저장해 둔 진짜 histogram을 p_hist로 직접 쓰고, 운영 표본을 같은
    centroid에 대해 코사인 → q_hist로 만들어 KL을 산출한다(분포 대 분포).

    Args:
        train_centroid: 학습 표본의 centroid (load_train_centroid["centroid"]).
        train_histogram: 학습 표본의 cos 분포 histogram (load_train_centroid["histogram"]).
        prod_vectors: 운영 환경 최근 임베딩 표본.
    """
    if not train_centroid or not prod_vectors:
        return DriftReport(
            sample_size=len(prod_vectors) if prod_vectors else 0,
            cosine_mean=0.0,
            cosine_std=0.0,
            kl_divergence=0.0,
            bins=bins,
            threshold_alert=threshold_alert,
        )

    prod_cos = [cosine(v, train_centroid) for v in prod_vectors]
    q_hist = _histogram(prod_cos, bins=bins)

    # #39: bin 불일치 KL=0 은폐 방지.
    #   (기존) 저장 histogram bin 수가 현재와 다르면 train 자리에 prod 자기 histogram을
    #          넣어 KL≈0 → drift를 은폐(false-negative)했다.
    #   (개선) 저장 histogram을 현재 bin 수로 선형보간 재샘플링해 분포 대 분포 KL을 산출.
    #          재샘플링이 불가능한 병리적 경우에만 alert_status="unknown"으로 표기해
    #          'drift 없음'으로 오인되지 않게 한다.
    status_unknown = False
    if len(train_histogram) == len(q_hist):
        p_hist = list(train_histogram)
    elif train_histogram and sum(train_histogram) > 0:
        _logger.warning(
            "drift: stored histogram bins=%d != current bins=%d — 선형보간 재샘플링 적용",
            len(train_histogram), len(q_hist),
        )
        p_hist = _resample_histogram(train_histogram, len(q_hist))
    else:
        # 재샘플링 불가(저장 histogram 비어있음/합=0) — KL=0 폴백 대신 unknown 표기.
        _logger.warning(
            "drift: stored histogram 재샘플링 불가(empty) — KL 신뢰 불가, alert_status=unknown",
        )
        p_hist = list(q_hist)
        status_unknown = True

    kl = _kl_divergence(p_hist, q_hist)

    mean = sum(prod_cos) / len(prod_cos)
    var = sum((x - mean) ** 2 for x in prod_cos) / len(prod_cos)
    std = math.sqrt(var)

    # #39: cosine_mean(분포 중심) 이동 점검 — KL과 별개 신호.
    #   학습 표본은 자기 centroid 대비 cos가 1.0 부근에 몰리므로(분포 중심≈1.0),
    #   운영 cosine_mean이 1.0에서 멀어질수록 중심 이동(=drift) 의심.
    cosine_shift = round(1.0 - mean, 4)
    cosine_violation = (
        cosine_shift_threshold > 0.0 and abs(cosine_shift) >= cosine_shift_threshold
    )
    kl_violation = kl >= threshold_alert
    raw_violation = kl_violation or cosine_violation

    if status_unknown:
        alert_status = "unknown"
        # KL 신뢰 불가 시 cosine 이동 신호로만 판단(보수적: cosine 위반이면 alert).
        single_alert = cosine_violation
    else:
        alert_status = "alert" if raw_violation else "ok"
        single_alert = raw_violation

    return DriftReport(
        sample_size=len(prod_vectors),
        cosine_mean=round(mean, 4),
        cosine_std=round(std, 4),
        kl_divergence=round(kl, 4),
        bins=bins,
        histogram_train=[round(h, 4) for h in p_hist],
        histogram_prod=[round(h, 4) for h in q_hist],
        threshold_alert=threshold_alert,
        # 동작보존: 단발 판정 유지(히스테리시스는 run_drift_check에서 적용).
        alert=single_alert,
        alert_status=alert_status,
        raw_violation=raw_violation,
        consecutive_violations=1 if raw_violation else 0,
        cosine_mean_shift=cosine_shift,
        cosine_shift_threshold=cosine_shift_threshold,
    )


def export_to_prometheus(report: DriftReport) -> dict[str, float]:
    """Prometheus gauge 노출용 메트릭 값.

    수집기(prom_metrics.py)가 본 dict을 읽어 Gauge.set() 호출.
    """
    return {
        "koipa_drift_cosine_mean": report.cosine_mean,
        "koipa_drift_cosine_std": report.cosine_std,
        "koipa_drift_kl_divergence": report.kl_divergence,
        "koipa_drift_alert": 1.0 if report.alert else 0.0,
        "koipa_drift_sample_size": float(report.sample_size),
    }


def publish_to_prom(report: DriftReport) -> None:
    """A4: prom_metrics.py의 Gauge에 직접 set. tasks.drift_tick이 호출.

    prom_metrics import는 함수 안에서 — module-load 시점에 circular 회피.
    """
    try:
        from koipa.api import prom_metrics  # noqa: PLC0415
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
    "KOIPA_DRIFT_CENTROID_PATH",
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
        from koipa.adapters.vectorstore import build_store  # noqa: PLC0415
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


# #39: 연속위반 임계 기본값.
#   동작보존: 기본 1(=기존 단발 alert). 기존 테스트(test_audit_w2_c의
#   test_run_drift_check_detects_shift_end_to_end)가 단발 alert를 기대하므로 깨지 않는다.
#   운영에서 단발 스파이크 억제가 필요하면 env KOIPA_DRIFT_CONSECUTIVE_REQUIRED=2~3으로
#   히스테리시스 활성(연속 위반 카운트는 drift_state.json에 영속). drift_tick은
#   limit/threshold만 넘기므로 본 env 기본값이 그대로 적용된다.
_DEFAULT_CONSECUTIVE_REQUIRED = int(
    _os.environ.get("KOIPA_DRIFT_CONSECUTIVE_REQUIRED", "1")
)
# cosine_mean 이동 임계 기본값 — 0(비활성)이면 KL만으로 판정(동작보존).
_DEFAULT_COSINE_SHIFT_THRESHOLD = float(
    _os.environ.get("KOIPA_DRIFT_COSINE_SHIFT_THRESHOLD", "0.0")
)


def run_drift_check(
    *,
    limit: int = 200,
    threshold: float = 0.5,
    consecutive_required: int | None = None,
    cosine_shift_threshold: float | None = None,
) -> DriftReport:
    """train centroid + 최근 운영 표본으로 drift 비교 → 게이지 갱신 후 보고서 반환.

    centroid 미저장 또는 운영 표본 부재 시 빈 DriftReport (alert=False) 반환.

    #39 (히스테리시스): 단발 위반에 바로 alert를 켜지 않고, N회(consecutive_required)
    연속 위반 시에만 alert=True. 연속 위반 카운트는 drift_state.json에 영속한다.
    위반이 아니면 카운트를 0으로 리셋(스파이크가 누적 안 되게). consecutive_required=1이면
    기존 단발 동작과 동일.

    Args:
        consecutive_required: alert에 필요한 연속 위반 횟수. None이면 env/기본(2) 사용.
        cosine_shift_threshold: cosine_mean 이동 임계(KL과 별개). None이면 env/기본(0=비활성).
    """
    if consecutive_required is None:
        consecutive_required = _DEFAULT_CONSECUTIVE_REQUIRED
    if cosine_shift_threshold is None:
        cosine_shift_threshold = _DEFAULT_COSINE_SHIFT_THRESHOLD
    consecutive_required = max(1, int(consecutive_required))

    train = load_train_centroid()
    prod = fetch_recent_prod_embeddings(limit=limit)
    if not train or not prod:
        report = DriftReport(
            sample_size=len(prod),
            cosine_mean=0.0,
            cosine_std=0.0,
            kl_divergence=0.0,
            threshold_alert=threshold,
            consecutive_required=consecutive_required,
            cosine_shift_threshold=cosine_shift_threshold,
        )
        publish_to_prom(report)
        _logger.info(
            "drift check skipped: train_centroid=%s prod_n=%d",
            "yes" if train else "no", len(prod),
        )
        return report

    # 저장된 centroid + histogram을 직접 사용 — centroid 한 점을 train_vectors로
    # 넘기면 학습분포가 1.0 델타함수가 돼 드리프트가 무의미해지는 버그를 회피.
    train_centroid_vec = train.get("centroid") or []
    train_hist = train.get("histogram") or []
    if not train_hist:
        # 과거(히스토그램 미저장) centroid 파일 폴백 — 최소한 cosine_mean은 의미 있게
        # 산출하되 KL은 신뢰 불가. #39: KL=0 'drift 없음' 오인을 막기 위해
        # alert_status="unknown"으로 표기하고, cosine 이동 임계가 켜져 있으면 그걸로 판정.
        _logger.warning("drift: stored centroid has no histogram — KL 신뢰 불가, alert_status=unknown")
        cmean = round(
            sum(cosine(v, train_centroid_vec) for v in prod) / len(prod), 4
        ) if train_centroid_vec else 0.0
        cshift = round(1.0 - cmean, 4)
        cviol = cosine_shift_threshold > 0.0 and abs(cshift) >= cosine_shift_threshold
        report = DriftReport(
            sample_size=len(prod),
            cosine_mean=cmean,
            cosine_std=0.0,
            kl_divergence=0.0,
            threshold_alert=threshold,
            alert_status="unknown",
            raw_violation=cviol,
            consecutive_violations=1 if cviol else 0,
            consecutive_required=consecutive_required,
            cosine_mean_shift=cshift,
            cosine_shift_threshold=cosine_shift_threshold,
        )
    else:
        report = compute_drift_from_centroid(
            train_centroid_vec, train_hist, prod,
            threshold_alert=threshold,
            cosine_shift_threshold=cosine_shift_threshold,
        )

    # #39 히스테리시스 적용 — 영속 카운트 갱신 후 N회 연속 위반일 때만 alert.
    # 상태를 centroid 지문으로 스코핑 — 재학습으로 baseline이 바뀌면 옛 위반 누적은 무효 리셋.
    fp = _centroid_fingerprint(train)
    prev = _load_violation_state(version=fp)
    if report.raw_violation:
        new_count = prev + 1
    else:
        new_count = 0
    _save_violation_state(new_count, version=fp)
    report.consecutive_violations = new_count
    report.consecutive_required = consecutive_required
    report.alert = new_count >= consecutive_required
    if report.alert_status != "unknown":
        report.alert_status = "alert" if report.alert else "ok"

    publish_to_prom(report)
    return report
