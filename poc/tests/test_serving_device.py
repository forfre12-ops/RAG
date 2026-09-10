"""서빙 추론 장치는 설정이 정한다 — GPU 가 보인다고 옮겨 가지 않는다(2026-09-11).

211 워커가 학습용 GPU 이미지로 바뀐 뒤 비동기 분류까지 GPU 로 옮겨 가, vLLM 과 메모리를
나눠 쓰다 99쪽 문서에서 CUDA 메모리 부족으로 재시도 끝에 결과 없이 끝났다. 짧은 문서는
남은 메모리에 들어가 드러나지 않았다. 기본값이 CPU 이고, GPU 는 명시해야만 쓰이는지 본다.
"""
from __future__ import annotations

import inspect
import logging

import pytest

from koipa.config import Settings, settings
from koipa.modules.m5_inference import device, factor_model, pipeline
from koipa.modules.m5_inference.device import serving_device


class _Torch:
    def __init__(self, gpu: bool) -> None:
        self.cuda = type("cuda", (), {"is_available": staticmethod(lambda: gpu)})


def test_default_is_cpu() -> None:
    assert Settings.model_fields["classifier_device"].default == "cpu"


def test_cpu_stays_cpu_even_when_gpu_is_visible(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classifier_device", "cpu")
    assert serving_device(_Torch(gpu=True)) == "cpu"


@pytest.mark.parametrize(("gpu", "expected"), [(True, "cuda"), (False, "cpu")])
def test_auto_follows_availability(monkeypatch, gpu: bool, expected: str) -> None:
    monkeypatch.setattr(settings, "classifier_device", "auto")
    assert serving_device(_Torch(gpu=gpu)) == expected


def test_cuda_without_gpu_falls_back_to_cpu_loudly(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "classifier_device", "cuda")
    with caplog.at_level(logging.WARNING, logger=device.__name__):
        assert serving_device(_Torch(gpu=False)) == "cpu"
    assert any("GPU 가 보이지 않아" in r.getMessage() for r in caplog.records)


def test_unknown_value_is_rejected_at_boot() -> None:
    with pytest.raises(ValueError, match="classifier_device"):
        Settings(classifier_device="gpu")


@pytest.mark.parametrize("mod", [pipeline, factor_model])
def test_serving_models_ask_the_setting(mod) -> None:
    # 종전 줄("cuda" if torch.cuda.is_available() else "cpu")이 되살아나면 기본값이 무의미해진다.
    src = inspect.getsource(mod)
    assert "serving_device(torch)" in src
    assert 'torch.cuda.is_available() else "cpu"' not in src
