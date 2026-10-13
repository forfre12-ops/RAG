"""자원 사용률(CPU·MEM) 판정 근거 요약 — peak 한 값만으로는 판정이 흔들린다.

왜 이 시험이 있는가(2026-09-12). PER-002 의 S11.6 CPU 사용률은 50ms 간격 표본의 **최댓값
하나**로 판정한다. 211 실측에서 같은 코드·같은 서버인데 35.6% 와 96~100% 가 나왔고
(예열 여부·측정 시각 부하 차이), 그 한 값이 통과/미달을 갈랐다. 판정 기준은 그대로 두되
**무엇을 보고 그렇게 판정했는지**(표본 수·p95·평균)를 결과에 함께 남긴다.

⚠ 이 근거 값은 KPI 가 아니다 — KPI 는 `s11_6` 처럼 이름이 정확히 맞는 키만 읽으므로
`s11_6_p95` 같은 키는 판정 줄을 만들지 않고 JSON 에만 실린다.
"""

from __future__ import annotations

import pytest

from koipa.perf.kpis import resource_evidence


def test_empty_samples_give_no_evidence():
    """표본이 없으면 근거도 없다 — 0.0 을 지어내면 '0% 였다'로 읽힌다."""
    assert resource_evidence([]) == {}


def test_peak_p95_mean_and_count():
    samples = [10.0] * 19 + [100.0]          # 20개 중 하나만 튄다
    ev = resource_evidence(samples)
    assert ev["n"] == 20
    assert ev["peak"] == 100.0
    assert ev["mean"] == pytest.approx(14.5)
    # p95 는 튀는 한 값에 끌려가지 않는다 — 이것이 근거를 함께 남기는 이유다.
    assert ev["p95"] < ev["peak"]


def test_single_sample_is_reported_as_one():
    ev = resource_evidence([42.0])
    assert ev == {"n": 1, "peak": 42.0, "p95": 42.0, "mean": 42.0}


def test_evidence_keys_are_not_kpi_keys():
    """근거 키가 KPI 키와 겹치면 판정 줄이 하나 더 생긴다 — 겹치지 않아야 한다."""
    from koipa.perf.kpis import KPIS

    kpi_keys = {k.id.lower().replace(".", "_") for k in KPIS}
    evidence_keys = {f"s11_6_{k}" for k in resource_evidence([1.0])}
    evidence_keys |= {f"s11_7_{k}" for k in resource_evidence([1.0])}
    assert not (evidence_keys & kpi_keys), sorted(evidence_keys & kpi_keys)
