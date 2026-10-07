"""cgroup 이 이 프로세스에 실제로 허용한 CPU·메모리 읽기 — 의존성 없는 공용 모듈.

[2026-09-30] 종전엔 이 로직이 api/health.py::_check_compute() 안에만 있어 **진단
(헬스체크 노출)에만 쓰이고 실제 설정값을 정하는 데는 안 쓰였다.** os.cpu_count() 는
호스트 전체 코어 수를 돌려줘 컨테이너 cgroup 한도와 다를 수 있다(실측: 호스트 16코어
컨테이너는 4코어로 묶여 있었는데 os.cpu_count() 는 16을 돌려줬다) — 그래서 cgroup 파일을
직접 읽는다. health.py 와 celery_app.py(워커 동시성·스레드 자동 설정) 양쪽이 이 모듈을
쓴다 — 로직이 두 곳에서 갈라지지 않게.
"""
from __future__ import annotations

from pathlib import Path


def detect_cpu_quota() -> float | None:
    """cgroup 이 허용한 CPU 수(소수 가능, 예: 2.5). 못 읽으면(비컨테이너 등) None."""
    try:  # cgroup v2
        raw = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if raw[0] != "max":
            return round(int(raw[0]) / int(raw[1]), 2)
    except Exception:  # noqa: BLE001
        pass
    try:  # cgroup v1
        q = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        p = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if q > 0:
            return round(q / p, 2)
    except Exception:  # noqa: BLE001
        pass
    return None


def detect_memory_limit_gb() -> float | None:
    """cgroup 이 허용한 메모리(GiB). 못 읽으면 None.

    워커 프로세스마다 분류 모델을 독립적으로 메모리에 올린다(prefork — fork 뒤에도
    각자 가중치를 들고 있음, 실측 1~2.6GiB/판) — CPU 코어 수만 보고 동시성을 올리면
    메모리가 먼저 바닥날 수 있어 동시성 자동 계산에 이 값도 같이 쓴다.
    """
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text().strip()
            if raw == "max":
                return None
            n = int(raw)
            # 비컨테이너 환경(memory.limit_in_bytes)은 종종 매우 큰 상한값을 둔다(사실상 무제한) —
            # 100TiB 넘으면 실제 제한이 아니라고 본다.
            if n >= 100 * 1024**4:
                return None
            return round(n / 1024**3, 2)
        except Exception:  # noqa: BLE001
            continue
    return None


def effective_cpu_count(host_cpus: int) -> int:
    """cgroup 한도가 있으면 그것을, 없으면 호스트 코어 수를 정수로 올림."""
    import math

    quota = detect_cpu_quota()
    if quota and quota > 0:
        return max(1, math.ceil(quota))
    return max(1, host_cpus)
