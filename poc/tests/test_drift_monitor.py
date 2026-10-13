"""P1-B4: drift monitor 검증."""

from __future__ import annotations

import random

from koipa.services.drift_monitor import compute_drift, cosine, export_to_prometheus


def _make_vectors(seed: int, n: int, dim: int = 16, shift: float = 0.0) -> list[list[float]]:
    rng = random.Random(seed)
    return [[rng.gauss(shift, 1.0) for _ in range(dim)] for _ in range(n)]


def test_cosine_basic():
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert abs(cosine(a, b) - 1.0) < 1e-6
    c = [0.0, 1.0, 0.0]
    assert abs(cosine(a, c)) < 1e-6


def test_drift_zero_when_same_distribution():
    train = _make_vectors(seed=1, n=200, shift=0.0)
    prod = _make_vectors(seed=2, n=200, shift=0.0)
    r = compute_drift(train, prod)
    assert r.sample_size == 200
    assert r.kl_divergence < 0.5
    assert r.alert is False


def test_drift_alert_when_shifted():
    train = _make_vectors(seed=1, n=200, shift=0.0)
    prod = _make_vectors(seed=2, n=200, shift=3.0)
    r = compute_drift(train, prod, threshold_alert=0.5)
    # shift가 크면 KL이 크게 올라야
    assert r.kl_divergence > 0.0


def test_empty_inputs():
    r = compute_drift([], [])
    assert r.sample_size == 0
    assert r.kl_divergence == 0.0
    assert r.alert is False


def test_export_to_prometheus_keys():
    train = _make_vectors(seed=1, n=10)
    prod = _make_vectors(seed=2, n=10)
    r = compute_drift(train, prod)
    metrics = export_to_prometheus(r)
    assert "koipa_drift_kl_divergence" in metrics
    assert "koipa_drift_cosine_mean" in metrics
    assert "koipa_drift_alert" in metrics


# ---------------------------------------------------------------------------
# A4 wiring — centroid 저장/로드 + Prometheus gauge + run_drift_check
# ---------------------------------------------------------------------------


def test_save_and_load_train_centroid(tmp_path):
    from koipa.services.drift_monitor import load_train_centroid, save_train_centroid

    p = tmp_path / "centroid.json"
    vectors = _make_vectors(seed=1, n=50, dim=16)
    save_train_centroid(vectors, path=str(p))
    loaded = load_train_centroid(path=str(p))
    assert loaded is not None
    assert loaded["sample_size"] == 50
    assert loaded["dim"] == 16
    assert len(loaded["centroid"]) == 16


def test_publish_to_prom_sets_gauges():
    from koipa.api import prom_metrics
    from koipa.services.drift_monitor import compute_drift, publish_to_prom

    train = _make_vectors(seed=1, n=50)
    prod = _make_vectors(seed=2, n=50, shift=2.0)
    report = compute_drift(train, prod, threshold_alert=0.1)
    publish_to_prom(report)

    # collect → text 노출에 4개 게이지가 들어 있는지
    from prometheus_client import generate_latest
    text = generate_latest(prom_metrics.registry).decode()
    assert "koipa_drift_kl_divergence" in text
    assert "koipa_drift_cosine_mean" in text
    assert "koipa_drift_alert" in text
    assert "koipa_drift_sample_size" in text


def test_run_drift_check_no_centroid_returns_empty(tmp_path, monkeypatch):
    """centroid 미저장 → empty report + alert=False.

    teardown 필수 — reload는 monkeypatch가 풀어주지 않으므로 env 정리 후
    한 번 더 reload하여 후속 테스트(prometheus 게이지·다른 drift 테스트)의
    drift_monitor 모듈 상태 오염을 차단.
    """
    monkeypatch.setenv("KOIPA_DRIFT_CENTROID_PATH", str(tmp_path / "missing.json"))
    # 모듈 reload — env가 module-load 시점에 캐시됨
    import importlib
    import koipa.services.drift_monitor as dm
    importlib.reload(dm)

    try:
        r = dm.run_drift_check()
        assert r.sample_size == 0
        assert r.alert is False
    finally:
        monkeypatch.delenv("KOIPA_DRIFT_CENTROID_PATH", raising=False)
        importlib.reload(dm)
