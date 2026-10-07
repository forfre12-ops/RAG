"""metamorphic 리포트 로더 계약 정합 — 생산자(중첩형)와 게이트(최상위 forward) 사이.

gen_metamorphic_pairs.py 는 {"model_dir":..., "report": {to_dict}} 로 중첩 저장한다.
summarize_metamorphic 은 최상위 forward 를 읽으므로, 로더가 중첩형을 언랩하지 않으면
forward=None → n=0 → measurable=False 로 실 위반이 **조용히 통과**한다. 이 회귀를 잠근다.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from koipa.modules.m6_evaluation.deploy_gate import summarize_metamorphic
from koipa.services.training_service import _maybe_metamorphic_report

_INNER = {
    "forward": {"name": "forward", "n": 8, "violations": 3, "violation_rate": 0.375,
                "ci": [0.12, 0.70]},
    "reverse": {"n": 0, "violations": 0, "ci": [0.0, 0.0]},
    "over_class": {"n": 5, "violations": 0, "ci": [0.0, 0.0]},
    "forward_failures": [{"anchor_id": "a1", "anchor_grade": "TS", "paraphrase_pred": "S3"}],
}


def _write(tmp_path, obj) -> str:
    p = tmp_path / "meta.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_nested_producer_shape_is_unwrapped(tmp_path):
    """생산자 중첩형 {"report": rd} → 로더가 언랩 → 게이트가 실 위반(n=8, v=3)을 본다."""
    path = _write(tmp_path, {"model_dir": "x", "provider": "ollama", "report": _INNER})
    settings = SimpleNamespace(deploy_gate_metamorphic_report_path=path)
    rep = _maybe_metamorphic_report(settings)
    assert rep is not None and rep.get("forward", {}).get("n") == 8
    summ = summarize_metamorphic(rep, forward_ceiling=0.0, min_n=5)
    assert summ["n"] == 8 and summ["violations"] == 3
    assert summ["measurable"] is True and summ["regression"] is True  # 조용한 통과 아님


def test_flat_to_dict_shape_still_works(tmp_path):
    """평면형(build_metamorphic_report.to_dict 직접) 도 그대로 소비된다(하위호환)."""
    path = _write(tmp_path, _INNER)
    settings = SimpleNamespace(deploy_gate_metamorphic_report_path=path)
    rep = _maybe_metamorphic_report(settings)
    summ = summarize_metamorphic(rep, forward_ceiling=0.0, min_n=5)
    assert summ["n"] == 8 and summ["regression"] is True


def test_missing_path_is_fail_open(tmp_path):
    settings = SimpleNamespace(deploy_gate_metamorphic_report_path="")
    assert _maybe_metamorphic_report(settings) is None
