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


def test_run_failure_on_high_grade_counts_as_miss():
    # [NEW-M1 회귀] 고등급(TS) 정답에서 run() 크래시는 '미탐'으로 잡혀야 한다.
    # (옛 버그: pred='TS' fail-secure라 TS=TS '정탐'으로 둔갑 → FNR 0.0 거짓 → 게이트 통과)
    rows = [{"text": "x", "label": "TS"}]
    pipe = _FakePipe({}, raise_on="x")
    m = evaluate_via_serving(rows, pipeline=pipe)
    assert m.sample_count == 1
    # 최저심각도(S3)로 집계 → TS 정답에 대해 under-classification(미탐) = 1.0
    assert m.fnr_underclass_by_grade["TS"] == 1.0
    assert m.accuracy == 0.0          # 크래시를 '정탐'으로 카운트하지 않음


def test_run_failure_on_low_grade_no_false_underclass():
    # 최저등급(S3) 정답의 크래시는 S3로 집계 → 허위 under-class를 만들지 않음(보수성 유지).
    rows = [{"text": "x", "label": "S3"}]
    pipe = _FakePipe({}, raise_on="x")
    m = evaluate_via_serving(rows, pipeline=pipe)
    assert m.sample_count == 1
    assert m.fnr_underclass_overall == 0.0   # S3는 더 낮은 등급이 없어 under-class 불가


def test_accepts_expected_grade_key():
    rows = [{"text": "a", "expected_grade": "S2"}]
    pipe = _FakePipe({"a": Grade.S2})
    m = evaluate_via_serving(rows, pipeline=pipe)
    assert m.sample_count == 1
    assert m.accuracy == 1.0
