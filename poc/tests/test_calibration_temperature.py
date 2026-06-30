"""Fix 3 — 서빙 softmax temperature scaling(신뢰도 캘리브레이션) 회귀."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from lloydk.modules.m5_inference.pipeline import InferencePipeline


def _load_calibrate_module():
    """scripts/calibrate_classifier.py를 파일경로로 로드(scripts는 sys.path에 없음)."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "calibrate_classifier.py"
    spec = importlib.util.spec_from_file_location("_calibrate_classifier", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


# ---------------------------------------------------------------------------
# 보정 배선 회귀 — trainer가 남긴 실 logits로 temperature.json을 산출하는 경로.
# (torch 불필요: calibrate_classifier는 순수 파이썬 softmax/NLL로 T를 적합.)
# ---------------------------------------------------------------------------


def _overconfident_rows() -> list[dict]:
    """trainer.py가 val_logits.jsonl에 쓰는 포맷({logits, label_idx})의 과신 표본."""
    return [
        {"logits": [3.0, 1.0, 0.5, 0.2], "label_idx": 0},
        {"logits": [2.5, 1.2, 0.7, 0.1], "label_idx": 0},
        {"logits": [3.5, 0.8, 0.3, 0.1], "label_idx": 1},  # 일부러 틀린 정답(과신 유도)
        {"logits": [1.0, 3.0, 0.5, 0.2], "label_idx": 1},
        {"logits": [0.8, 2.5, 1.0, 0.3], "label_idx": 2},  # 과신/오답
        {"logits": [0.5, 0.8, 3.0, 1.0], "label_idx": 2},
        {"logits": [0.3, 1.0, 2.8, 0.9], "label_idx": 3},  # 과신/오답
        {"logits": [0.2, 0.3, 0.5, 3.0], "label_idx": 3},
    ]


def test_calibrate_reads_model_dir_val_logits(tmp_path, monkeypatch):
    """trainer가 남긴 <model-dir>/val_logits.jsonl을 --val 없이 자동 사용해
    실 logits로 temperature.json을 산출(더미/--allow-dummy 우회, exit 0)."""
    mod = _load_calibrate_module()
    model_dir = tmp_path / "v-abcd1234"
    model_dir.mkdir()
    with (model_dir / "val_logits.jsonl").open("w", encoding="utf-8") as f:
        for row in _overconfident_rows():
            f.write(json.dumps(row) + "\n")

    # --allow-dummy 없이, --val 없이 — model dir 동봉 logits만으로 성공해야 한다.
    monkeypatch.setattr(
        "sys.argv",
        ["calibrate_classifier.py", "--model-dir", str(model_dir)],
    )
    rc = mod.main()
    assert rc == 0

    tpath = model_dir / "temperature.json"
    assert tpath.exists(), "보정이 모델 dir에 temperature.json을 남겨야 함(서빙 자동로드)"
    report = json.loads(tpath.read_text(encoding="utf-8"))
    assert report["temperature"] > 0
    # 실 표본 개수 그대로 — 더미(12건) 분기를 타지 않았음을 확인.
    assert report["n_samples"] == len(_overconfident_rows())


def test_calibrate_fails_loud_without_logits(tmp_path, monkeypatch):
    """val_logits.jsonl도 --val도 없으면 fail-loud(rc=1) — 더미 T 배포 금지 보존."""
    mod = _load_calibrate_module()
    model_dir = tmp_path / "v-empty"
    model_dir.mkdir()
    monkeypatch.setattr(
        "sys.argv",
        ["calibrate_classifier.py", "--model-dir", str(model_dir)],
    )
    assert mod.main() == 1
    assert not (model_dir / "temperature.json").exists()


# ---------------------------------------------------------------------------
# C17 — 무보정(T=1.0) 서빙 가시화. 실모델 로드 시 동봉 temperature.json이 없고
# classifier_temperature=1.0이면 종전엔 *완전 무음*으로 무보정 서빙됐다(OOD 과신
# → 고등급 무음미탐). _apply_bundle_calibration이 보정 출처를 판정·loud-warn.
# ---------------------------------------------------------------------------


def test_bundle_calibration_loads_temperature_json(tmp_path, monkeypatch):
    from lloydk import config as cfg

    monkeypatch.setattr(cfg.settings, "classifier_temperature", 1.0, raising=False)
    model_dir = tmp_path / "v-cal"
    model_dir.mkdir()
    (model_dir / "temperature.json").write_text(
        json.dumps({"temperature": 2.0}), encoding="utf-8"
    )
    pipe = InferencePipeline()
    pipe.model_dir = model_dir
    src = pipe._apply_bundle_calibration()
    assert src == "bundle"
    assert pipe._model_temperature == pytest.approx(2.0)
    assert pipe.calibrated is True


def test_uncalibrated_serving_warns_loud(tmp_path, monkeypatch, caplog):
    """동봉 temperature.json 부재 + classifier_temperature=1.0 → 무보정 + loud-warn."""
    import logging

    from lloydk import config as cfg

    monkeypatch.setattr(cfg.settings, "classifier_temperature", 1.0, raising=False)
    model_dir = tmp_path / "v-nocal"
    model_dir.mkdir()  # temperature.json 없음
    pipe = InferencePipeline()
    pipe.model_dir = model_dir
    with caplog.at_level(logging.WARNING, logger="lloydk.modules.m5_inference.pipeline"):
        src = pipe._apply_bundle_calibration()
    assert src == "uncalibrated"
    assert pipe.calibrated is False
    assert pipe._model_temperature is None
    assert any("calibration" in r.message and "무보정" in r.message for r in caplog.records)


def test_env_temperature_counts_as_calibrated(tmp_path, monkeypatch):
    """동봉이 없어도 classifier_temperature(≠1.0) 주입이 있으면 보정으로 간주(경고 없음)."""
    from lloydk import config as cfg

    monkeypatch.setattr(cfg.settings, "classifier_temperature", 1.5, raising=False)
    model_dir = tmp_path / "v-envcal"
    model_dir.mkdir()
    pipe = InferencePipeline()
    pipe.model_dir = model_dir
    src = pipe._apply_bundle_calibration()
    assert src == "env"
    assert pipe.calibrated is True


def test_nonpositive_bundle_temperature_falls_back_uncalibrated(tmp_path, monkeypatch):
    """temperature<=0 동봉은 무시하고 무보정 폴백(주입도 없으면 uncalibrated)."""
    from lloydk import config as cfg

    monkeypatch.setattr(cfg.settings, "classifier_temperature", 1.0, raising=False)
    model_dir = tmp_path / "v-zero"
    model_dir.mkdir()
    (model_dir / "temperature.json").write_text(
        json.dumps({"temperature": 0}), encoding="utf-8"
    )
    pipe = InferencePipeline()
    pipe.model_dir = model_dir
    src = pipe._apply_bundle_calibration()
    assert src == "uncalibrated"
    assert pipe.calibrated is False
