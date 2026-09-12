"""/classify/explain 이 **등급을 어떻게 냈는지**를 사실대로 말하는가.

왜 이 시험이 있는가(2026-09-10). 응답이 `method="multiplicative(S×V×M)"` 를 실어 보내고
있었다. 그 문장은 "이 등급은 곱셈으로 산출됐다"로 읽히는데 사실이 아니다 — 배포본에서
등급은 분류기가 정하고 곱셈 단계는 실측 발동 0건이다(993건 · 평가셋 4종 · 등급 변경 0).

KL 이 "S/V/M 룰을 빼자"고 제안하게 된 배경이 이것이다. 코드와 화면이 룰을 판정자처럼
말하고 있으니, 자동확정율이 낮으면 그 판정자를 의심하는 것이 자연스럽다. 실제로 룰은
이미 등급을 정하지 않는다 — 합의 게이트에서 **이견만** 낸다.

이 시험이 지키는 것 셋.
    ① 응답이 곱셈식을 **산출 방법**으로 주장하지 않는가
    ② 요소값이 근거인지 추정인지 함께 나가는가
    ③ 추정값일 때 '제약 요소'를 말하지 않는가 — 등급에서 나온 값을 등급의 원인이라
       하는 순환이라서
"""

from __future__ import annotations

from pathlib import Path

import pytest

from koipa.api.explain import _factor_decomposition

_SRC = Path(__file__).resolve().parents[1] / "src" / "koipa" / "api" / "explain.py"


class _Factors:
    def __init__(self, s, v, m):
        self.secrecy, self.value, self.management = s, v, m


class _Result:
    def __init__(self, factors, source, decision_path="agreement"):
        self.evaluation_factors = factors
        self.factors_source = source
        self.decision_path = decision_path


def test_response_does_not_claim_the_grade_came_from_multiplication():
    out = _factor_decomposition(_Result(_Factors(2, 2, 0), "model_estimated"))
    blob = repr(out)
    assert "multiplicative" not in blob, (
        "응답이 곱셈을 산출 방법으로 주장한다 — 배포본에서 곱셈 단계는 발동 0건이다"
    )
    assert "method" not in out, "'method' 는 산출 방법으로 읽힌다 — 기준과 방법을 갈라 쓸 것"


def test_reference_rule_is_labelled_as_a_yardstick_not_a_method():
    """판정식을 아예 감추지는 않는다 — 대조 기준으로는 유효하다."""
    out = _factor_decomposition(_Result(_Factors(2, 2, 0), "rule_evidenced"))
    assert "S×V×M" in out["reference_rule"]
    assert "기준" in out["reference_rule"], "기준임을 문장 안에서 밝히지 않으면 방법으로 읽힌다"


def test_factor_source_travels_with_the_values():
    """값만 주고 출처를 안 주면 받는 쪽이 추정치를 근거로 읽는다."""
    for source in ("model_estimated", "rule_evidenced"):
        out = _factor_decomposition(_Result(_Factors(1, 1, 1), source))
        assert out["factors_source"] == source


def test_estimated_factors_do_not_get_a_limiting_factor():
    """역산값에서 최저 요소를 고르면 등급에서 나온 값을 등급의 원인이라 말하는 순환이다."""
    out = _factor_decomposition(_Result(_Factors(2, 2, 0), "model_estimated"))
    assert out["limiting_factor"] is None
    assert "limiting_factor_note" in out, "왜 못 말하는지 적지 않으면 누락으로 읽힌다"


def test_evidenced_factors_still_get_a_limiting_factor():
    """근거일 때는 여전히 유용한 정보다 — 통째로 없애면 검수자가 잃는 것이 있다."""
    out = _factor_decomposition(_Result(_Factors(2, 2, 0), "rule_evidenced"))
    assert out["limiting_factor"] == "management"


def test_no_factors_returns_empty():
    """요소가 없으면 지어내지 않는다."""
    assert _factor_decomposition(_Result(None, "rule_evidenced")) == {}


def test_module_docstring_states_the_correction():
    """다음 사람이 'method' 를 되돌리지 않도록 이유가 파일 안에 있어야 한다."""
    src = _SRC.read_text(encoding="utf-8")
    assert "발동 0건" in src, "왜 곱셈식을 방법으로 쓰지 않는지 근거가 파일에 없다"


@pytest.mark.parametrize("source", ["model_estimated", "rule_evidenced"])
def test_decided_by_is_reported(source):
    """무엇이 등급을 정했는지는 응답이 직접 말해야 한다 — 추측하게 두지 않는다."""
    out = _factor_decomposition(_Result(_Factors(0, 0, 0), source, decision_path="rule-override"))
    assert out["decided_by"] == "rule-override"
