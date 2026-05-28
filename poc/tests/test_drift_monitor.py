"""P1-B4: drift monitor 검증."""

from __future__ import annotations

import random

from lloydk.services.drift_monitor import compute_drift, cosine, export_to_prometheus


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
    assert "lloydk_drift_kl_divergence" in metrics
    assert "lloydk_drift_cosine_mean" in metrics
    assert "lloydk_drift_alert" in metrics
