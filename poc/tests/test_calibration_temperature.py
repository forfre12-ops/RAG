"""Fix 3 — 서빙 softmax temperature scaling(신뢰도 캘리브레이션) 회귀."""

from __future__ import annotations

import pytest

from lloydk.modules.m5_inference.pipeline import InferencePipeline


@pytest.fixture(autouse=True)
def _stub_pipeline_db(monkeypatch):
    """InferencePipeline 생성이 PG에 매달리지 않게 DB 의존(get_codes·rule engine)을 스텁."""
    from lloydk.schemas import common as _common

    monkeypatch.setattr(
        _common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"]
    )
    from lloydk.modules.m3_labeling import pipeline as _m3

    monkeypatch.setattr(_m3, "build_rule_engine_from_db", lambda *a, **k: object())


def test_temperature_reads_settings(monkeypatch):
    from lloydk import config as cfg

    pipe = InferencePipeline()
    monkeypatch.setattr(cfg.settings, "classifier_temperature", 1.5, raising=False)
    assert pipe._temperature == pytest.approx(1.5)


def test_temperature_guards_nonpositive(monkeypatch):
    from lloydk import config as cfg

    pipe = InferencePipeline()
    monkeypatch.setattr(cfg.settings, "classifier_temperature", 0.0, raising=False)
    assert pipe._temperature == 1.0  # 비정상(<=0) → 1.0 폴백(무보정)
    monkeypatch.setattr(cfg.settings, "classifier_temperature", -2.0, raising=False)
    assert pipe._temperature == 1.0


def test_temperature_softens_confidence():
    """T>1이면 최대 softmax 확률이 감소(과신 완화) — 캘리브레이션의 핵심 성질."""
    torch = pytest.importorskip("torch")
    import torch.nn.functional as F

    logits = torch.tensor([[3.0, 1.0, 0.5, 0.2]])
    p_t1 = F.softmax(logits, dim=-1).max().item()
    p_t2 = F.softmax(logits / 2.0, dim=-1).max().item()
    assert p_t2 < p_t1
