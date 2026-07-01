"""anchor_eval (C/Task3 글루) — 앵커→서빙→카드 dict 산출 + cap + 게이트 투입 가능성 테스트.

fake 파이프라인 주입으로 모델·GPU 없이 글루 오케스트레이션만 검증.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from lloydk.modules.m6_evaluation.anchor_corpus import AnchorSource
from lloydk.modules.m6_evaluation.anchor_eval import cap_per_cell, run_anchor_cards
from lloydk.modules.m6_evaluation.deploy_gate import evaluate_deploy_gate
from lloydk.schemas.common import Grade


@dataclass
class _Res:
    label: object


class _FakePipe:
    """text 접두로 등급 매핑하는 가짜 서빙(모델 불요)."""

    def __init__(self, mapping, default=Grade.S3):
        self.mapping = mapping
        self.default = default

    def run(self, text, use_rag=False, metadata=None):
        for key, g in self.mapping.items():
            if key in text:
                return _Res(label=g)
        return _Res(label=self.default)


_TS_N = 100   # PASS엔 충분한 표본 필요(wilson(0,100) 상한<0.05; n=40이면 0.088>천장=INCONCLUSIVE)
_S1_N = 40    # 전부 미탐이면 하한>천장 → FAIL (n=40로 충분)


def _write_corpus(tmp_path):
    nkt = tmp_path / "nkt.jsonl"
    rows = []
    for i in range(_TS_N):
        rows.append({"text": f"TSDOC {i}", "label": "TS", "app_no": f"T{i}"})
    for i in range(_S1_N):
        rows.append({"text": f"S1DOC {i}", "label": "S1", "app_no": f"S{i}"})
    nkt.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return [AnchorSource("nkt.jsonl", "nkt", "anchor_nkt")]


def test_run_anchor_cards_produces_gate_ready_report(tmp_path):
    sources = _write_corpus(tmp_path)
    # TS는 'TSDOC'→TS(정탐), S1은 'S1DOC'→S3(미탐). default S3.
    pipe = _FakePipe({"TSDOC": Grade.TS, "S1DOC": Grade.S3})
    out = run_anchor_cards(pipeline=pipe, sources=sources, root=str(tmp_path), min_n=30)

    assert out["evaluated"] == _TS_N + _S1_N
    rep = out["report"]
    cards = {(c["slice"], c["grade"]): c for c in rep["cards"]}
    # TS: 미탐 0 → PASS, S1: 전부 S3 미탐 → FAIL
    assert cards[("anchor_nkt", "TS")]["verdict"] == "PASS"
    assert cards[("anchor_nkt", "S1")]["verdict"] == "FAIL"

    # 이 리포트를 그대로 deploy_gate에 투입 → 고등급 미탐 FAIL이므로 hard block.
    dec = evaluate_deploy_gate(
        {"fnr_high": 0.0, "f1_macro": 0.9}, {"fnr_high": 0.0, "f1_macro": 0.9},
        anchor_report=rep,
    )
    assert dec.passed is False
    assert "anchor_high_grade_miss" in dec.reason


def test_cap_per_cell_deterministic_and_logs_drop(tmp_path):
    sources = _write_corpus(tmp_path)
    pipe = _FakePipe({"TSDOC": Grade.TS, "S1DOC": Grade.S1})
    out = run_anchor_cards(pipeline=pipe, sources=sources, root=str(tmp_path), cap_per_cell_n=10)
    # 칸당 10건 → TS 10 + S1 10 = 20 평가, TS는 90·S1은 30 절단
    assert out["evaluated"] == 20
    assert out["dropped_by_cap"] == {"anchor_nkt/TS": _TS_N - 10, "anchor_nkt/S1": _S1_N - 10}


def test_cap_zero_is_full(tmp_path):
    sources = _write_corpus(tmp_path)
    pipe = _FakePipe({"TSDOC": Grade.TS, "S1DOC": Grade.S1})
    out = run_anchor_cards(pipeline=pipe, sources=sources, root=str(tmp_path), cap_per_cell_n=0)
    assert out["evaluated"] == _TS_N + _S1_N and out["dropped_by_cap"] == {}
