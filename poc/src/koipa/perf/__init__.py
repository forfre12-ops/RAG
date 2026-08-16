"""PSH — Performance Scenario Harness.

KPI 정의의 진실원은 kpis.py 다(구 doc/20a 매트릭스는 2026-07 삭제).

구성:
- env.py     환경 캡처 (Python·OS·GPU·docker svc·git SHA)
- kpis.py    KPI 정의 (이름·합격선·집계함수)
- harness.py 시나리오 실행기 (반복·warmup·percentile·skip)
- recorder.py 측정 결과 → JSON
- scenarios/ S1~S8 시나리오 구현
"""

from koipa.perf.env import capture_env
from koipa.perf.harness import ScenarioRunner, Measurement, ScenarioResult
from koipa.perf.kpis import KPI, KPIS
from koipa.perf.recorder import write_report

__all__ = [
    "capture_env",
    "ScenarioRunner",
    "Measurement",
    "ScenarioResult",
    "KPI",
    "KPIS",
    "write_report",
]
