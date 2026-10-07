"""서빙 추론 장치 — 설정(classifier_device)이 정한다. 기본은 CPU.

왜 이 모듈이 있는가(2026-09-11). 서빙 추론기(분류 모델·요소 모델)는 `torch.cuda.is_available()`
이면 무조건 GPU 를 썼다. 설계는 "서빙은 CPU" 다 — 고객사 배포본이 CPU 전용이고, 분류 1건이
150ms 라 GPU 이득이 작다. 그런데 211 서버가 학습을 위해 워커를 GPU 이미지로 바꾸자
(2026-09-09) 워커의 비동기 분류까지 GPU 로 옮겨 갔다. 그 GPU 는 vLLM 이 대부분을 잡고 있어
99쪽 문서의 비동기 분류가 CUDA 메모리 부족으로 재시도 2회 끝에 결과 없이 끝났다(status=partial).
짧은 문서는 남은 메모리에 들어가 드러나지 않았다.

값
    cpu   (기본) 항상 CPU
    auto  GPU 가 보이면 GPU — 추론 전용 GPU 가 따로 있을 때만 쓴다
    cuda  GPU 를 요구한다. 보이지 않으면 CPU 로 가고 경고를 남긴다 — 서빙은 멈추지 않는다
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def serving_device(torch_mod) -> str:
    """추론 모델을 올릴 장치 이름("cpu" | "cuda")."""
    from koipa.config import settings  # noqa: PLC0415

    want = str(getattr(settings, "classifier_device", "cpu") or "cpu").strip().lower()
    if want == "cpu":
        return "cpu"
    has_gpu = bool(torch_mod.cuda.is_available())
    if want == "cuda" and not has_gpu:
        logger.warning("classifier_device=cuda 인데 GPU 가 보이지 않아 CPU 로 추론합니다")
        return "cpu"
    return "cuda" if has_gpu else "cpu"
