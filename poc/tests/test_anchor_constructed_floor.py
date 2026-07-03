"""constructed_floor → 앵커 카드 배선 — 격리 slice·floor FNR 의미론·기본경로 무유입 가드."""
from __future__ import annotations

import json
from pathlib import Path

from lloydk.modules.m6_evaluation.anchor_corpus import (
    DEFAULT_ANCHOR_SOURCES,
    constructed_floor_source,
    load_anchor_corpus,
    normalize_constructed_floor,
)
from lloydk.modules.m6_evaluation.anchor_eval import run_anchor_cards


def _write_admitted(tmp_path: Path, rows: list[dict]) -> Path:
    run = tmp_path / "run1"
    run.mkdir()
    (run / "admitted.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return run


def _rec(doc_id, grade, text="witness 사실 문장 12345 및 관련 실무 맥락"):
    return {
        "doc_id": doc_id, "text": text, "label": grade, "intended_grade": grade,
        "witnesses": [{"token": "witness 사실 문장 12345", "basis": "가이드 v2.2 §근거"}],
        "tier": "constructed_floor_eval", "label_source": "constructed_floor",
    }


def test_normalizer_maps_floor_grade_and_tokens():
    r = normalize_constructed_floor(_rec("d1", "S2"))
    assert r is not None
    assert r.anchor_grade == "S2" and r.source == "constructed_floor"
    assert r.anchor_id == "cf:d1"
    assert r.required_tokens == ["witness 사실 문장 12345"]
    # 잘못된 등급/빈 텍스트/무witness는 None(무근거 floor 주장 금지)
    assert normalize_constructed_floor(_rec("d2", "X")) is None
    assert normalize_constructed_floor({"label": "S1", "text": ""}) is None
    assert normalize_constructed_floor({"label": "S1", "text": "본문", "witnesses": []}) is None


def test_not_in_default_sources():
    # 기본 앵커에 constructed_floor가 없어야 한다(deploy gate 자동 유입 방지)
    kinds = {s.kind for s in DEFAULT_ANCHOR_SOURCES}
    assert "constructed_floor" not in kinds


def test_source_helper_accepts_dir_and_file(tmp_path):
    run = _write_admitted(tmp_path, [_rec("d1", "S2")])
    src_dir = constructed_floor_source(run)
    src_file = constructed_floor_source(run / "admitted.jsonl")
    assert src_dir.path.endswith("admitted.jsonl")
    assert src_dir.kind == "constructed_floor" and src_dir.source == "constructed_floor"
    assert load_anchor_corpus([src_dir]) and load_anchor_corpus([src_file])


def test_floor_fnr_card_semantics(tmp_path):
    # floor=S2 문서: S3 예측=under(위반), TS 예측=위반 아님(floor 이상). fake 파이프라인으로 확인.
    run = _write_admitted(tmp_path, [_rec(f"s2-{i}", "S2") for i in range(4)])

    class _FakePipe:
        """i<2 → S3(미탐 위반), i>=2 → TS(floor 초과, 위반 아님). predict_via_serving 계약=run()."""
        def __init__(self):
            self.n = 0

        def run(self, text, use_rag=False, metadata=None):  # noqa: ARG002
            import types
            grade = "S3" if self.n < 2 else "TS"
            self.n += 1
            return types.SimpleNamespace(label=grade)

    out = run_anchor_cards(
        pipeline=_FakePipe(),
        sources=[constructed_floor_source(run)],
        min_n=1,  # 소표본이라도 이 테스트는 판정 로직만 본다
    )
    cards = {(c["slice"], c["grade"]): c for c in out["report"]["cards"]}
    s2 = cards[("constructed_floor", "S2")]
    assert s2["n"] == 4
    # 2건 under(S3), 2건 above(TS) → under-class FNR = 2/4 = 0.5 (TS는 위반 아님)
    assert abs(s2["underclass_fnr"] - 0.5) < 1e-9
