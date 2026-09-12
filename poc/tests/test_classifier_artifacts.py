"""CLASSIFIER_MODEL_DIR 산출물 내용물 검증 — startup 게이트 (require_real_classifier 보완).

require_real_classifier 는 '로드 성공'의 warmup 백스톱, 이 게이트는 그 전에 '어떤 산출물이
없는지' 명확히 차단(config.json + 가중치). temperature.json 부재는 경고만.
"""

from __future__ import annotations

import logging

import pytest

from koipa.config import _assert_classifier_artifacts


def test_missing_path_raises(tmp_path):
    with pytest.raises(RuntimeError, match="존재하지 않습니다"):
        _assert_classifier_artifacts(str(tmp_path / "nope"))


def test_empty_dir_lists_both_missing(tmp_path):
    with pytest.raises(RuntimeError) as ei:
        _assert_classifier_artifacts(str(tmp_path))
    msg = str(ei.value)
    assert "config.json" in msg and "가중치" in msg


def test_missing_weights_only(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError) as ei:
        _assert_classifier_artifacts(str(tmp_path))
    assert "가중치" in str(ei.value)


def test_safetensors_index_counts_as_weights(tmp_path):
    # sharded 모델(index.json)도 가중치로 인정.
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
    _assert_classifier_artifacts(str(tmp_path))  # no raise


def test_valid_dir_warns_on_missing_temperature(tmp_path, caplog):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.safetensors").write_bytes(b"\x00")
    with caplog.at_level(logging.WARNING, logger="koipa.config"):
        _assert_classifier_artifacts(str(tmp_path))  # no raise
    assert "temperature.json" in caplog.text


def test_full_valid_dir_no_temperature_warning(tmp_path, caplog):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pytorch_model.bin").write_bytes(b"\x00")
    (tmp_path / "temperature.json").write_text('{"temperature": 3.0}', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="koipa.config"):
        _assert_classifier_artifacts(str(tmp_path))
    assert "temperature.json" not in caplog.text
