"""회귀 게이트가 **환경 불일치를 회귀로 세지 않는가**.

왜 이 시험이 있는가(실측 2026-09-10). 기준선을 `METADATA_FLOOR_ENABLED` 없이 떠 놓고 그
변수를 준 채 비교했더니 축 ③ 에 `metadata_floor_enabled: False → True` 가 **회귀 후보 1건**
으로 잡혔다. 코드는 한 줄도 그 값을 건드리지 않았다.

게이트는 이미 프로파일 불일치를 막고 있었는데(deploy_profile 검사), **개별 환경변수는 안
막고 있었다.** 둘은 같은 문제다 — 판정에 영향을 주는 조건이 다르면 델타는 코드 회귀가
아니다. 대응도 다르다: 코드 회귀는 코드를 고치고, 환경 불일치는 조건을 맞춘다.

이 시험이 지키는 것 셋.
    ① 판정에 영향을 주는 환경변수를 스냅샷이 **기록하는가**
    ② 그 값이 다르면 회귀로 세지 않고 **비교 불가로 끊는가**
    ③ 그 기록 자체가 파라미터 축에서 **또 세어지지 않는가**(같은 사실의 이중 계상)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "regression_gate.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("regression_gate", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _snap(gate, env: dict[str, str], **settings) -> dict:
    """비교에 필요한 최소 스냅샷 — 축 ①②④⑤ 는 비우고 ③ 만 본다."""
    base = {"deploy_profile": "onprem-local", **settings, gate._ENV_KEY: env}
    return {
        "decisions": {},
        "gates": {},
        "api": {"paths": [], "schemas": {}},
        "settings": base,
        "model": {},
    }


def test_judgment_env_is_recorded(gate, monkeypatch):
    """기록하지 않으면 비교할 수가 없다."""
    monkeypatch.setenv("METADATA_FLOOR_ENABLED", "true")
    monkeypatch.delenv("AGREEMENT_GATE_ENABLED", raising=False)
    snap = gate.snap_settings()
    assert gate._ENV_KEY in snap, "판정 환경변수를 스냅샷이 기록하지 않는다"
    assert snap[gate._ENV_KEY].get("METADATA_FLOOR_ENABLED") == "true"
    assert "AGREEMENT_GATE_ENABLED" not in snap[gate._ENV_KEY], "설정 안 된 값을 지어내면 안 된다"


def test_metadata_floor_is_watched(gate):
    """실제로 유령 회귀를 만든 그 변수가 감시 목록에 있어야 한다."""
    assert "METADATA_FLOOR_ENABLED" in gate._JUDGMENT_ENV


def test_env_mismatch_stops_comparison_instead_of_counting_regressions(gate, capsys):
    """환경이 다르면 **세지 말고 끊는다.** 세면 코드 회귀와 구분이 안 된다."""
    base = _snap(gate, {"METADATA_FLOOR_ENABLED": "true"}, metadata_floor_enabled=True)
    now = _snap(gate, {}, metadata_floor_enabled=False)

    rc = gate.compare(base, now)
    out = capsys.readouterr().out

    assert rc == 2, "환경이 다른데 비교를 계속했다"
    assert "비교 불가" in out and "환경변수가 다르다" in out
    assert "METADATA_FLOOR_ENABLED=true" in out, "무엇을 주면 되는지 말해 주지 않는다"
    assert "회귀" not in out.split("비교 불가")[0], "환경 불일치를 회귀로 셌다"


def test_same_env_compares_normally(gate, capsys):
    """조건이 같으면 평소대로 비교한다 — 게이트가 통째로 막히면 아무도 안 쓴다."""
    env = {"METADATA_FLOOR_ENABLED": "true"}
    base = _snap(gate, env, metadata_floor_enabled=True)
    now = _snap(gate, env, metadata_floor_enabled=True)

    rc = gate.compare(base, now)
    out = capsys.readouterr().out
    assert rc == 0
    assert "비교 불가" not in out


def test_env_record_is_not_counted_as_a_parameter(gate, capsys):
    """기록 자체를 파라미터로 세면 같은 사실이 회귀 1건으로 둔갑한다.

    이 검사가 없으면 환경 검사를 넣은 것이 오히려 유령 회귀를 하나 더 만든다.
    """
    env = {"METADATA_FLOOR_ENABLED": "true"}
    base = _snap(gate, env, metadata_floor_enabled=True)
    now = _snap(gate, env, metadata_floor_enabled=True)
    gate.compare(base, now)
    out = capsys.readouterr().out
    assert gate._ENV_KEY not in out, "환경 기록이 운영 파라미터 델타로 출력됐다"
