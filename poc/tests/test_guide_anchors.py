"""정본 가이드 골든셋 시드(guide_v2_anchors.jsonl) ↔ grade_from_svm 정합 회귀.

svm_anchor(가이드 p12 권위 예시)의 S×V×M·등급이 코드 산정식과 일치하는지 검증.
데이터↔코드가 어긋나면(예: 룰 변경, 시드 오타) 즉시 적발한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from koipa.modules.m3_labeling.rule_engine import grade_from_svm

_ANCHORS = Path(__file__).resolve().parents[1] / "datasets" / "gold" / "guide_v2_anchors.jsonl"


def _load(kind: str) -> list[dict]:
    rows = []
    for line in _ANCHORS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("kind") == kind:
            rows.append(rec)
    return rows


def test_anchor_file_exists_and_has_records():
    assert _ANCHORS.exists(), _ANCHORS
    assert len(_load("svm_anchor")) == 5            # 가이드 p12 워크드 예시 5건
    assert len(_load("factor_level_example")) == 9  # S/V/M × 0/1/2
    assert len(_load("grade_example")) >= 10


@pytest.mark.parametrize("rec", _load("svm_anchor"), ids=lambda r: r["doc_type"])
def test_svm_anchor_consistent_with_code(rec):
    s, v, m = rec["secrecy"], rec["value"], rec["management"]
    assert s * v * m == rec["svm"], f"{rec['doc_type']}: svm 곱 불일치"
    assert grade_from_svm(s, v, m) == rec["grade"], f"{rec['doc_type']}: 등급 불일치"


def test_factor_level_examples_cover_three_requisites():
    factors = {r["factor"] for r in _load("factor_level_example")}
    assert factors == {"secrecy", "value", "management"}
