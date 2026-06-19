"""서빙 escalation τ 단위 테스트 — C-esc.

InferencePipeline._select_pred_idx의 severity-ordered escalation을 검증.
모델·GPU 없이(model_dir=None → rule fallback, _id2label=기본 4등급) 순수 선택 로직만 테스트.
기본(τ=None)은 argmax(동작 보존), τ 설정 시 고등급 적극 승격(FNR-safe).
"""

from __future__ import annotations

import pytest

from lloydk.modules.m5_inference.pipeline import InferencePipeline
from lloydk.schemas.common import Grade


@pytest.fixture
def pipe():
    # model_dir=None → 모델 미로드. _id2label은 기본 [TS,S1,S2,S3].
    p = InferencePipeline(model_dir=None)
    # GradeRegistry 순서 의존 제거 — 고정 매핑으로 결정론 보장.
    p._id2label = {0: Grade.TS, 1: Grade.S1, 2: Grade.S2, 3: Grade.S3}
    return p


def _probs(ts, s1, s2, s3):
    return [ts, s1, s2, s3]


def test_tau_none_is_argmax(pipe, monkeypatch):
    from lloydk.config import settings
    monkeypatch.setattr(settings, "classifier_escalation_tau", None)
    # S3가 최대 → argmax = idx3
    assert pipe._select_pred_idx(_probs(0.30, 0.05, 0.15, 0.50)) == 3


def test_tau_escalates_to_most_severe_above_threshold(pipe, monkeypatch):
    from lloydk.config import settings
    monkeypatch.setattr(settings, "classifier_escalation_tau", 0.25)
    # TS=0.30 ≥ 0.25 → 가장 심각한 등급 TS(idx0)로 승격 (argmax는 S3였음)
    assert pipe._select_pred_idx(_probs(0.30, 0.05, 0.15, 0.50)) == 0


def test_tau_skips_below_threshold_picks_next_severe(pipe, monkeypatch):
    from lloydk.config import settings
    monkeypatch.setattr(settings, "classifier_escalation_tau", 0.25)
    # TS=0.10(<τ) skip, S1=0.30(≥τ) → S1(idx1)
    assert pipe._select_pred_idx(_probs(0.10, 0.30, 0.10, 0.50)) == 1


def test_tau_none_match_falls_back_to_argmax(pipe, monkeypatch):
    from lloydk.config import settings
    monkeypatch.setattr(settings, "classifier_escalation_tau", 0.6)
    # 아무 등급도 0.6 못 넘음 → argmax(S3, idx3)
    assert pipe._select_pred_idx(_probs(0.30, 0.05, 0.15, 0.50)) == 3


def test_invalid_tau_treated_as_none(pipe, monkeypatch):
    from lloydk.config import settings
    monkeypatch.setattr(settings, "classifier_escalation_tau", 1.5)  # 범위 밖 → None 취급
    assert pipe._escalation_tau is None
    assert pipe._select_pred_idx(_probs(0.30, 0.05, 0.15, 0.50)) == 3
