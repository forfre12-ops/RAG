# -*- coding: utf-8 -*-
"""지표 4분할 계산 계약 — '미탐률' 이 가리키던 넷이 서로 다른 값임을 잠근다.

왜(2026-09-14). 같은 36건에서 16.67% · 5.56% · 2.78% 가 나왔는데 셋 다 "미탐률" 이라
불렸다. 계약 목표 '재현율 90%' 를 어느 지표로 재느냐에 따라 미달이 되기도 통과가 되기도
한다. 그래서 셋을 섞을 수 없게 계산을 고정한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "scripts"))

from measure_four_metrics import compute  # noqa: E402


def _r(truth, model, predicted, status="staging"):
    return {"truth": truth, "model_grade": model, "predicted": predicted, "status": status}


def test_three_metrics_can_differ_on_same_docs() -> None:
    """모델이 놓친 것을 서빙 가드가 건지고, 그중 일부가 검수로 간다 — 셋이 갈린다."""
    rows = [
        _r("TS", "S3", "S3", "staging"),        # 모델·서빙 미탐 · 자동확정까지 됨
        _r("TS", "S3", "S3", "needs_review"),   # 모델·서빙 미탐이나 검수로 잡힘
        _r("S1", "S2", "S1", "staging"),        # 모델은 놓쳤고 서빙 가드가 되살림
        _r("TS", "TS", "TS", "staging"),        # 정상
        _r("S3", "S3", "S3", "staging"),        # 고등급 아님 — 분모에서 빠진다
    ]
    m = compute(rows)
    assert m["n_all"] == 5
    assert m["n_high_grade"] == 4
    assert m["model_recall"]["misses"] == 3          # TS→S3 둘 + S1→S2 하나
    assert m["serving_recall"]["misses"] == 2        # 가드가 되살린 것은 빠진다
    assert m["high_grade_auto_confirm_fn_rate"]["misses"] == 1   # 검수로 간 것은 빠진다
    assert m["model_recall"]["rate"] > m["serving_recall"]["rate"] \
        > m["high_grade_auto_confirm_fn_rate"]["rate"]


def test_needs_review_counts_as_miss_for_serving_recall() -> None:
    """검수로 보냈어도 **등급은 틀린 것**이다 — 재현율에서는 성공으로 세지 않는다."""
    rows = [_r("TS", "S3", "S3", "needs_review")]
    m = compute(rows)
    assert m["serving_recall"]["misses"] == 1
    assert m["high_grade_auto_confirm_fn_rate"]["misses"] == 0
    assert m["review_capture_rate"]["captured"] == 1
    assert m["review_capture_rate"]["rate"] == 1.0


def test_overclassification_is_not_a_miss() -> None:
    """낮은 등급을 높게 부른 것은 미탐이 아니다(과탐은 별도 축이다)."""
    rows = [_r("S3", "TS", "TS", "staging"), _r("S2", "S1", "S1", "staging")]
    m = compute(rows)
    assert m["n_high_grade"] == 0
    assert m["model_recall"]["misses"] == 0


def test_empty_high_grade_gives_none_not_zero() -> None:
    """분모가 0이면 0% 가 아니라 '못 잼' 이다 — 0% 로 적으면 통과처럼 읽힌다."""
    m = compute([_r("S3", "S3", "S3")])
    assert m["n_high_grade"] == 0
    assert m["model_recall"]["rate"] is None
    assert m["high_grade_auto_confirm_fn_rate"]["rate"] is None


def test_missing_model_grade_falls_back_and_is_counted() -> None:
    """model_grade 가 없으면 predicted 로 대신하되, 그런 행이 몇 건인지 남긴다."""
    rows = [{"truth": "TS", "predicted": "S3", "status": "staging"}]
    m = compute(rows)
    assert m["model_recall"]["misses"] == 1
    assert m["model_grade_missing"] == 1


def test_review_load_uses_all_documents() -> None:
    """검수 업무량은 고등급이 아니라 **전체** 분모다."""
    rows = [_r("TS", "TS", "TS", "needs_review"), _r("S3", "S3", "S3", "staging"),
            _r("S3", "S3", "S3", "staging"), _r("S3", "S3", "S3", "staging")]
    m = compute(rows)
    assert m["review_load"]["reviewed"] == 1
    assert m["review_load"]["n"] == 4
    assert m["review_load"]["rate"] == 0.25


def test_measured_values_2026_09_14(tmp_path) -> None:
    """2026-09-14 실측을 고정한다 — holdout109 고등급 36건에서 6 / 2 / 1."""
    import json
    p = POC / "reports/TIEBREAK_OFF/holdout109.records.jsonl"
    if not p.exists():
        import pytest
        pytest.skip("측정 산출물 없음(reports/ 는 git 밖) — measure_serving_fnr 로 재생성")
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    m = compute(rows)
    assert m["n_high_grade"] == 36
    assert m["model_recall"]["misses"] == 6
    assert m["serving_recall"]["misses"] == 2
    assert m["high_grade_auto_confirm_fn_rate"]["misses"] == 1


# ── 과탐 축 — EVAL_CRITERIA 제4조 "동반 필수: 미탐만 보면 속는다" ──────────

def test_overclass_axes_are_computed_on_low_grade_docs() -> None:
    rows = [
        _r("S3", "TS", "S1", "staging"),      # 격상 + 자동확정
        _r("S3", "S3", "S2", "needs_review"), # 과탐이나 검수로 감
        _r("S3", "S3", "S3", "staging"),      # 정상
        _r("TS", "TS", "TS", "staging"),      # 고등급 — 과탐 분모에서 빠진다
    ]
    m = compute(rows)
    assert m["n_low_grade"] == 3
    assert m["model_overclass"]["hits"] == 1
    assert m["serving_overclass"]["hits"] == 2
    assert m["severe_overclass"]["hits"] == 1          # S1 으로 격상된 1건
    assert m["auto_confirmed_overclass"]["hits"] == 1  # 검수로 간 것은 빠진다


def test_severe_overclass_counts_only_high_grade_predictions() -> None:
    """S3 를 S2 라 부른 것과 TS 라 부른 것은 무게가 다르다 — 감리 185(가)가 후자를 지목했다."""
    rows = [_r("S3", "S2", "S2"), _r("S3", "TS", "TS")]
    m = compute(rows)
    assert m["serving_overclass"]["hits"] == 2
    assert m["severe_overclass"]["hits"] == 1


def test_no_low_grade_gives_none_not_zero() -> None:
    m = compute([_r("TS", "TS", "TS")])
    assert m["n_low_grade"] == 0
    assert m["serving_overclass"]["rate"] is None
