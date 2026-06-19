"""서빙경로 평가 — C-eval (doc/36 본개발).

기존 평가(metrics.compute_metrics_from_*)는 DB의 저장 예측이나 모델 raw argmax를 비교해,
실서빙 `InferencePipeline.run()`이 거치는 전체 경로(temperature 보정·청크 most-severe 집계·
escalation τ·FNR-safe override·source-prior 게이트)를 **우회**했다 → "측정값 ≠ 배포동작".

이 모듈은 홀드아웃 문서를 **실서빙 파이프라인 그대로** 통과시켜 예측을 얻고 지표를 계산해
eval↔serving 정합을 보장한다. 게이트 평가(배포 합격선)도 이 경로로 측정하면 실배포와 일치한다.

순수 오케스트레이션 — pipeline 주입 시 모델·GPU 불요(테스트 용이). 미주입 시 InferencePipeline 생성.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from lloydk.modules.m6_evaluation.metrics import MetricsResult, compute_metrics_from_arrays

logger = logging.getLogger(__name__)


def _label_code(label: Any) -> str:
    return label.value if hasattr(label, "value") else str(label)


def evaluate_via_serving(
    rows: Sequence[dict],
    *,
    pipeline: Optional[Any] = None,
    model_dir: Optional[str] = None,
    use_rag: bool = False,
    metadata: Optional[dict] = None,
    labels: Optional[Sequence[str]] = None,
    model_version: str = "serving",
) -> MetricsResult:
    """rows({text,label})를 InferencePipeline.run() 경유로 평가 → MetricsResult.

    각 문서를 실서빙과 동일 경로로 분류(보정·집계·게이트·override·escalation 전부 반영)한 뒤
    compute_metrics_from_arrays로 FNR(방향성 포함)·F1 등을 산출한다. 서빙↔평가 정합(C-eval).

    Args:
        rows: [{"text":.., "label":..}, ..] (label은 expected_grade도 허용).
        pipeline: 주입 시 그대로 사용(테스트/재사용). 미주입 시 InferencePipeline(model_dir) 생성.
        use_rag/metadata: run()에 전달(소스-프라이어 게이트 등 메타 의존 동작 평가).
    """
    if pipeline is None:
        from lloydk.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415
        pipeline = InferencePipeline(model_dir=model_dir)

    y_true: list[str] = []
    y_pred: list[str] = []
    for row in rows:
        label = row.get("label") or row.get("expected_grade")
        text = row.get("text", "")
        if label is None:
            continue
        try:
            result = pipeline.run(text, use_rag=use_rag, metadata=metadata)
            pred = _label_code(result.label)
        except Exception as exc:  # noqa: BLE001
            # 서빙 실패 = 운영에서도 분류 못 함. 평가에서 조용히 빼지 않고 가장 안전한
            # 방향(미탐 회피)으로 최고등급(TS)을 부여해 fail-SECURE로 집계한다.
            logger.warning("serving eval: run() failed on a doc → fail-secure TS: %s", exc)
            pred = "TS"
        y_true.append(_label_code(label))
        y_pred.append(pred)

    return compute_metrics_from_arrays(
        y_true, y_pred, labels=labels, model_version=model_version
    )
