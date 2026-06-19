"""서빙경로 평가 단위 테스트 — C-eval.

InferencePipeline 주입(fake)으로 모델·GPU 없이 오케스트레이션만 검증:
실서빙 run()을 거쳐 예측을 모으고 compute_metrics_from_arrays로 지표를 낸다.
"""

from __future__ import annotations

from dataclasses import dataclass

from lloydk.modules.m6_evaluation.serving_eval import evaluate_via_serving
from lloydk.schemas.common import Grade


@dataclass
class _Res:
    label: object


class _FakePipe:
    """text→label 매핑으로 run()을 흉내. metadata/use_rag는 무시."""
    def __init__(self, mapping, raise_on=None):
        self.mapping = mapping
        self.raise_on = raise_on
        self.calls = 0

    def run(self, text, use_rag=False, metadata=None):
        self.calls += 1
        if self.raise_on is not None and text == self.raise_on:
            raise RuntimeError("boom")
        return _Res(label=self.mapping[text])


def test_runs_each_row_through_serving_and_scores():
    rows = [
        {"text": "a", "label": "TS"},
        {"text": "b", "label": "S1"},
        {"text": "c", "label": "S3"},
    ]
    pipe = _FakePipe({"a": Grade.TS, "b": Grade.S1, "c": Grade.S3})
    m = evaluate_via_serving(rows, pipeline=pipe)
    assert pipe.calls == 3
    assert m.sample_count == 3
    assert m.accuracy == 1.0
    assert m.fnr_underclass_overall == 0.0


def test_underclassification_counts_in_fnr():
    # 진짜 TS를 S3로 예측 → 방향성 미탐(fnr_underclass) 잡혀야
    rows = [{"text": "a", "label": "TS"}, {"text": "b", "label": "S1"}]
    pipe = _FakePipe({"a": Grade.S3, "b": Grade.S1})
    m = evaluate_via_serving(rows, pipeline=pipe)
    assert m.fnr_underclass_by_grade["TS"] == 1.0
    assert m.accuracy == 0.5


def test_run_failure_is_fail_secure_TS():
    # run() 실패 시 조용히 빼지 않고 TS로 집계(fail-secure) → 정답 S3면 과분류(안전)로 카운트
    rows = [{"text": "x", "label": "S3"}]
    pipe = _FakePipe({}, raise_on="x")
    m = evaluate_via_serving(rows, pipeline=pipe)
    assert m.sample_count == 1
    # S3 정답인데 TS로 예측 = 과분류(저→고). under-class FNR엔 안 잡힘.
    assert m.fnr_underclass_overall == 0.0


def test_accepts_expected_grade_key():
    rows = [{"text": "a", "expected_grade": "S2"}]
    pipe = _FakePipe({"a": Grade.S2})
    m = evaluate_via_serving(rows, pipeline=pipe)
    assert m.sample_count == 1
    assert m.accuracy == 1.0
