"""서빙 fail-open gate 라벨이 선언과 실제 호출부에서 어긋나지 않는지 고정.

왜 테스트로 두는가. `SERVING_GATE_FAIL_OPEN_TOTAL` 의 gate 라벨은 종전에 주석으로만
관리됐고, 주석에는 3종(agreement·llm_second_opinion·similarity_escalation)이 적혀 있는데
실제 호출부는 8종을 기록하고 있었다(2026-08-11 실측). 운영자가 대시보드에서
`metadata_floor` fail-open 을 보고 "정의에 없는 라벨"로 오판할 수 있는 상태였다.

주석은 드리프트를 막지 못하므로 계약으로 바꾼다 — 게이트를 새로 추가하면서
`SERVING_FAIL_OPEN_GATES` 에 등록하지 않으면 여기서 실패한다.

경보(infra/observability/alert_rules.yml: ServingGateFailOpen)는 `sum by (gate)` 라
라벨을 열거하지 않아 새 게이트도 자동으로 잡힌다 — 이 테스트는 **경보 커버리지**가 아니라
**선언과 실제의 일치**를 지킨다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from koipa.api.prom_metrics import SERVING_FAIL_OPEN_GATES

_SRC = Path(__file__).resolve().parents[1] / "src" / "koipa"
# 두 곳에 같은 이름의 기록 헬퍼가 있다(pipeline 모듈 함수 · ClassifyService 정적 메서드).
# 호출 형태를 모두 잡으려고 함수명 뒤의 첫 문자열 리터럴을 읽는다.
_CALL_RE = re.compile(r"_record_gate_fail_open\(\s*[\"']([a-z0-9_]+)[\"']")


def _gates_in_source() -> dict[str, list[str]]:
    """소스 전체에서 실제로 기록되는 gate 라벨 → 그 라벨을 쓰는 파일 목록."""
    found: dict[str, list[str]] = {}
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for gate in _CALL_RE.findall(text):
            found.setdefault(gate, []).append(path.name)
    return found


def test_every_recorded_gate_is_declared():
    """호출부에만 있고 선언에 없는 라벨 = 대시보드에 정체불명 라벨이 뜬다."""
    recorded = _gates_in_source()
    assert recorded, "fail-open 기록 호출을 하나도 찾지 못했다 — 정규식이 낡았는지 확인할 것"
    undeclared = {g: files for g, files in recorded.items() if g not in SERVING_FAIL_OPEN_GATES}
    assert not undeclared, (
        "SERVING_FAIL_OPEN_GATES 에 없는 gate 라벨이 기록되고 있다: "
        + ", ".join(f"{g}({'·'.join(sorted(set(f)))})" for g, f in sorted(undeclared.items()))
    )


def test_every_declared_gate_is_recorded():
    """선언에만 있고 호출부에 없는 라벨 = 죽은 선언(게이트가 사라졌는데 목록에 남음)."""
    recorded = set(_gates_in_source())
    stale = SERVING_FAIL_OPEN_GATES - recorded
    assert not stale, (
        "선언돼 있으나 기록하는 코드가 없는 gate 라벨: " + ", ".join(sorted(stale))
    )


@pytest.mark.parametrize("gate", sorted(SERVING_FAIL_OPEN_GATES))
def test_declared_gate_label_is_usable(gate):
    """선언된 라벨로 실제 카운터를 증가시킬 수 있는지(라벨 카디널리티 계약)."""
    from koipa.api.prom_metrics import SERVING_GATE_FAIL_OPEN_TOTAL

    SERVING_GATE_FAIL_OPEN_TOTAL.labels(gate=gate).inc(0)
