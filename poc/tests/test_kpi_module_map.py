"""KPI→책임 모듈 매핑 drift 가드 (lite).

doc/11 §8.2가 손으로 유지하던 KPI→모듈 표가 실재하지 않는 테스트/타깃을 가리키며
stale했던 문제(2026-06-02)를 코드 단일 진실원 + CI 검증으로 차단한다. 본 테스트는
(a) 모든 KPI 시나리오에 매핑이 있고 (b) 매핑된 모든 경로가 실재하는지 검증한다 —
모듈이 이름이 바뀌거나 사라지면 CI가 즉시 잡는다(문서-only였다면 못 잡았을 drift).
"""

from __future__ import annotations

from pathlib import Path

from lloydk.perf.kpis import KPIS, modules_for_kpi, modules_for_scenario

# poc 루트 = 이 파일(tests/)의 부모.
_POC_ROOT = Path(__file__).resolve().parents[1]


def test_every_kpi_scenario_has_module_mapping():
    missing = sorted({k.scenario for k in KPIS if not modules_for_scenario(k.scenario)})
    assert not missing, f"매핑 없는 시나리오: {missing}"


def test_every_kpi_has_nonempty_responsible_modules():
    for k in KPIS:
        assert modules_for_kpi(k), f"{k.id} ({k.scenario}) 책임 모듈 비어있음"


def test_all_mapped_module_paths_exist():
    """매핑된 모든 경로가 실재해야 함 — 모듈 rename/삭제 시 drift를 CI에서 차단."""
    broken: list[str] = []
    for k in KPIS:
        for rel in modules_for_kpi(k):
            if not (_POC_ROOT / rel).exists():
                broken.append(f"{k.scenario}->{rel}")
    assert not broken, f"실재하지 않는 책임 모듈 경로: {sorted(set(broken))}"
