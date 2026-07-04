"""특허 시간축 prepub_floor → 앵커 카드 배선 — 격리 slice·S2 floor FNR 의미론·기본경로 무유입 가드.

constructed_floor와의 결정적 차이: witness(in-text 토큰)를 요구하지 않는다 — floor 근거가 본문
토큰이 아니라 특허법 §64 공개일이라는 비인적 법적 사실이기 때문(build_patent_timeaxis_pairs).
"""
from __future__ import annotations

import json
from pathlib import Path

from lloydk.modules.m6_evaluation.anchor_corpus import (
    DEFAULT_ANCHOR_SOURCES,
    load_anchor_corpus,
    normalize_patent_timeaxis_prepub,
    patent_timeaxis_prepub_source,
)
from lloydk.modules.m6_evaluation.anchor_eval import run_anchor_cards


def _rec(doc_id, grade="S2", app_no=None):
    # prepub_floor_eval.jsonl 계약(materialize_views): witness 없음, floor 근거=§64 공개일.
    return {
        "doc_id": doc_id,
        "app_no": app_no or f"102026{doc_id}",
        "text": "리튬 이차전지용 고체 전해질 제조 방법\n\n반응 온도 범위와 촉매 농도를 제어하는 공정을 제공한다.",
        "label": grade,
        "floor_label": True,
        "label_source": "patent_timeaxis_prepub",
        "open_date": "20260601",
    }


def _write_prepub(tmp_path: Path, rows: list[dict]) -> Path:
    run = tmp_path / "tx1"
    run.mkdir()
    (run / "prepub_floor_eval.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return run


def test_normalizer_maps_floor_grade_without_witness():
    # ★핵심: witness 없이도 정규화된다(§64 공개일이 floor 근거) — constructed_floor와 다른 점.
    r = normalize_patent_timeaxis_prepub(_rec("d1", "S2"))
    assert r is not None
    assert r.anchor_grade == "S2" and r.source == "patent_timeaxis_prepub"
    assert r.anchor_id == "ptx:102026d1"
    assert "§64" in r.grade_basis and "20260601" in r.grade_basis
    # 잘못된 등급/빈 텍스트는 None (등급 앵커 부적격)
    assert normalize_patent_timeaxis_prepub(_rec("d2", "X")) is None
    assert normalize_patent_timeaxis_prepub({"label": "S2", "text": ""}) is None
    # doc_id/app_no 둘 다 없어도 텍스트 해시로 안정 id (skip 아님)
    r2 = normalize_patent_timeaxis_prepub({"label": "S2", "text": "본문만 있는 특허 초록"})
    assert r2 is not None and r2.anchor_id.startswith("ptx:")


def test_not_in_default_sources():
    # 기본 앵커에 patent_timeaxis_prepub가 없어야 한다(deploy gate 자동 유입 방지 — opt-in).
    kinds = {s.kind for s in DEFAULT_ANCHOR_SOURCES}
    assert "patent_timeaxis_prepub" not in kinds


def test_source_helper_accepts_dir_and_file(tmp_path):
    run = _write_prepub(tmp_path, [_rec("d1", "S2")])
    src_dir = patent_timeaxis_prepub_source(run)
    src_file = patent_timeaxis_prepub_source(run / "prepub_floor_eval.jsonl")
    assert src_dir.path.endswith("prepub_floor_eval.jsonl")
    assert src_dir.kind == "patent_timeaxis_prepub" and src_dir.source == "patent_timeaxis_prepub"
    assert load_anchor_corpus([src_dir]) and load_anchor_corpus([src_file])


def test_floor_fnr_card_semantics(tmp_path):
    # floor=S2: S3 예측=under(위반), TS 예측=위반 아님(floor 이상). fake 파이프라인으로 판정 확인.
    run = _write_prepub(tmp_path, [_rec(f"s2-{i}", "S2", app_no=f"1020260{i:04d}") for i in range(4)])

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
        sources=[patent_timeaxis_prepub_source(run)],
        min_n=1,
    )
    cards = {(c["slice"], c["grade"]): c for c in out["report"]["cards"]}
    s2 = cards[("patent_timeaxis_prepub", "S2")]
    assert s2["n"] == 4
    # 2건 under(S3), 2건 above(TS) → under-class FNR = 2/4 = 0.5 (TS는 floor 이상이라 위반 아님)
    assert abs(s2["underclass_fnr"] - 0.5) < 1e-9
