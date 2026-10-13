"""resource_detect.py — cgroup CPU·메모리 판독 (2026-09-30).

health.py::_check_compute() 와 celery_app.py 의 워커 자동 동시성 계산이 같은 로직을
쓴다 — 여기서 한 번만 검증하면 둘 다 보장된다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from koipa import resource_detect as rd


@pytest.fixture(autouse=True)
def _fresh_env(monkeypatch):
    # Path.read_text 를 몽키패치하는 시험들이 서로 새지 않게 모듈 함수를 직접 부른다.
    yield


def test_detect_cpu_quota_reads_cgroup_v2(monkeypatch):
    def _fake_read_text(self, *a, **kw):  # noqa: ARG001
        # Windows 의 WindowsPath 는 str() 이 백슬래시로 렌더링돼 하드코드 문자열과 안
        # 맞는다 — .as_posix() 로 OS 무관하게 비교한다(실제 동작은 리눅스 컨테이너).
        if self.as_posix() == "/sys/fs/cgroup/cpu.max":
            return "400000 100000"
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", _fake_read_text)
    assert rd.detect_cpu_quota() == 4.0


def test_detect_cpu_quota_unlimited_returns_none(monkeypatch):
    def _fake_read_text(self, *a, **kw):  # noqa: ARG001
        if str(self) == "/sys/fs/cgroup/cpu.max":
            return "max 100000"
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", _fake_read_text)
    assert rd.detect_cpu_quota() is None


def test_detect_cpu_quota_missing_files_returns_none(monkeypatch):
    def _raise(self, *a, **kw):  # noqa: ARG001
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", _raise)
    assert rd.detect_cpu_quota() is None


def test_detect_memory_limit_reads_cgroup_v2(monkeypatch):
    def _fake_read_text(self, *a, **kw):  # noqa: ARG001
        if self.as_posix() == "/sys/fs/cgroup/memory.max":
            return str(4 * 1024**3)
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", _fake_read_text)
    assert rd.detect_memory_limit_gb() == 4.0


def test_detect_memory_limit_treats_huge_value_as_unlimited(monkeypatch):
    def _fake_read_text(self, *a, **kw):  # noqa: ARG001
        if self.as_posix() == "/sys/fs/cgroup/memory.max":
            return str(200 * 1024**4)  # 200 TiB — 사실상 무제한
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", _fake_read_text)
    assert rd.detect_memory_limit_gb() is None


def test_effective_cpu_count_prefers_cgroup_over_host(monkeypatch):
    monkeypatch.setattr(rd, "detect_cpu_quota", lambda: 2.5)
    assert rd.effective_cpu_count(host_cpus=16) == 3  # ceil(2.5)


def test_effective_cpu_count_falls_back_to_host(monkeypatch):
    monkeypatch.setattr(rd, "detect_cpu_quota", lambda: None)
    assert rd.effective_cpu_count(host_cpus=8) == 8
