"""워커 동시성·스레드 자동감지 (2026-09-30).

고객사마다 CPU 가 달라 WORKER_CPU_LIMIT 등을 손으로 맞춰야 했다 — 설치 시점에
CELERY_WORKER_CONCURRENCY 를 안 넣으면 cgroup CPU·메모리 한도로 자동 계산한다.
명시하면 그 값을 그대로 쓴다(자동계산 안 함). 코어 수만 보면 동시성을 올려도
워커 프로세스마다 모델을 독립적으로 올려 메모리가 먼저 바닥날 수 있어 메모리도 본다.
"""
from __future__ import annotations

import koipa.workers.celery_app as capp


def test_autodetect_caps_concurrency_by_cpu(monkeypatch):
    monkeypatch.setattr(capp, "os", capp.os)  # no-op, 명시적으로 모듈 그대로 씀
    import koipa.resource_detect as rd

    monkeypatch.setattr(rd, "effective_cpu_count", lambda host_cpus: 6)  # noqa: ARG005
    monkeypatch.setattr(rd, "detect_memory_limit_gb", lambda: None)
    monkeypatch.setattr(rd, "detect_cpu_quota", lambda: 6.0)
    concurrency, threads, reason = capp._autodetect_worker_scaling()
    assert concurrency == 6
    assert threads == 1  # 6 // 6
    assert "concurrency=6" in reason


def test_autodetect_caps_concurrency_by_memory_even_with_many_cpus(monkeypatch):
    import koipa.resource_detect as rd

    monkeypatch.setattr(rd, "effective_cpu_count", lambda host_cpus: 16)  # noqa: ARG005
    monkeypatch.setattr(rd, "detect_memory_limit_gb", lambda: 5.0)  # (5-1)//2 = 2
    monkeypatch.setattr(rd, "detect_cpu_quota", lambda: 16.0)
    concurrency, threads, _reason = capp._autodetect_worker_scaling()
    assert concurrency == 2, "메모리가 코어 수보다 먼저 동시성을 제한해야 한다"
    assert threads == 8  # 16 // 2


def test_autodetect_hard_cap_at_eight(monkeypatch):
    import koipa.resource_detect as rd

    monkeypatch.setattr(rd, "effective_cpu_count", lambda host_cpus: 64)  # noqa: ARG005
    monkeypatch.setattr(rd, "detect_memory_limit_gb", lambda: None)
    monkeypatch.setattr(rd, "detect_cpu_quota", lambda: 64.0)
    concurrency, _threads, _reason = capp._autodetect_worker_scaling()
    assert concurrency == 8, "검증 없이 무제한 확장하지 않는다 — 상한 8"


def test_resolve_respects_explicit_env_var(monkeypatch):
    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", "3")
    monkeypatch.setenv("OMP_NUM_THREADS", "5")
    monkeypatch.setattr(capp.settings, "celery_worker_concurrency", 3, raising=False)
    concurrency, threads = capp._resolve_worker_scaling()
    assert (concurrency, threads) == (3, 5)


def test_resolve_auto_detects_when_nothing_set(monkeypatch):
    monkeypatch.delenv("CELERY_WORKER_CONCURRENCY", raising=False)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)
    import koipa.resource_detect as rd

    monkeypatch.setattr(rd, "effective_cpu_count", lambda host_cpus: 4)  # noqa: ARG005
    monkeypatch.setattr(rd, "detect_memory_limit_gb", lambda: None)
    monkeypatch.setattr(rd, "detect_cpu_quota", lambda: 4.0)
    concurrency, threads = capp._resolve_worker_scaling()
    assert concurrency == 4
    assert threads == 1
    assert __import__("os").environ["OMP_NUM_THREADS"] == "1"


def test_resolve_falls_back_safely_on_detection_failure(monkeypatch):
    monkeypatch.delenv("CELERY_WORKER_CONCURRENCY", raising=False)
    monkeypatch.setattr(
        capp,
        "_autodetect_worker_scaling",
        lambda: (_ for _ in ()).throw(RuntimeError("cgroup 못 읽음")),
    )
    monkeypatch.setattr(capp.settings, "celery_worker_concurrency", 2, raising=False)
    concurrency, threads = capp._resolve_worker_scaling()
    assert (concurrency, threads) == (2, 2), "감지 실패는 기존 기본값으로 안전하게 폴백해야 한다"
