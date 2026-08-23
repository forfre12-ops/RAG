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

from koipa.modules.m6_evaluation.metrics import (
    DEFAULT_LABELS,
    MetricsResult,
    _GRADE_SEVERITY,
    compute_metrics_from_arrays,
)

logger = logging.getLogger(__name__)


def _label_code(label: Any) -> str:
    return label.value if hasattr(label, "value") else str(label)


def _least_severe_grade(labels: Optional[Sequence[str]]) -> str:
    """가장 낮은 심각도(=least sensitive) 등급 코드 — 서빙 실패의 보수적 대체 예측.

    _GRADE_SEVERITY 값이 가장 큰(=덜 민감한) 등급. labels 가 심각도 맵에 없으면
    마지막 라벨로 폴백. 이 값을 실패 pred 로 쓰면 고등급 정답 문서의 크래시가
    under-classification(미탐)으로 집계된다(아래 설명 참조).
    """
    candidates = list(labels) if labels else list(DEFAULT_LABELS)
    known = [g for g in candidates if g in _GRADE_SEVERITY]
    if known:
        return max(known, key=lambda g: _GRADE_SEVERITY[g])
    return candidates[-1] if candidates else "S3"


def predict_via_serving(
    rows: Sequence[dict],
    *,
    pipeline: Optional[Any] = None,
    model_dir: Optional[str] = None,
    use_rag: bool = False,
    metadata: Optional[dict] = None,
    labels: Optional[Sequence[str]] = None,
) -> list[dict]:
    """rows를 실서빙 InferencePipeline.run() 경로로 통과시켜 **레코드마다 예측을 붙여** 반환.

    evaluate_via_serving(집계)과 build_eval_cards(출처×등급 카드)가 **같은 서빙 경로**를
    공유하도록 per-record 예측을 분리한 단계다. 반환은 입력 row의 얕은 복사 + 아래 필드:
      - "label": 정규화된 정답 코드(label/expected_grade 중 존재하는 것).
      - "pred":  서빙 예측 코드(실패 시 fail_pred=최저심각도).
      - "serving_failed": run()이 예외였는지(True면 pred는 미탐 보수 대체).
    원본 키(source/anchor_grade/domain 등)는 그대로 보존 → 카드의 slice_of/true_of로 슬라이싱 가능.

    label이 없는 row는 건너뛴다(집계와 동일).
    """
    if pipeline is None:
        from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415
        pipeline = InferencePipeline(model_dir=model_dir)

    # [NEW-M1] 서빙 실패 시 보수적 대체 예측 = 최저심각도 등급. (사유는 except 블록 참조)
    fail_pred = _least_severe_grade(labels)
    out: list[dict] = []
    error_count = 0
    for row in rows:
        label = row.get("label") or row.get("expected_grade")
        text = row.get("text", "")
        if label is None:
            continue
        failed = False
        try:
            result = pipeline.run(text, use_rag=use_rag, metadata=metadata)
            pred = _label_code(result.label)
        except Exception as exc:  # noqa: BLE001
            # 서빙 실패 = 운영에서도 그 문서를 분류 못 함. 이전엔 pred='TS'(최고등급)를 줘
            # "fail-secure"라 했으나, **게이트 평가에서는** 고등급(TS/S1) 정답 문서의
            # 크래시가 '정탐'(예: TS 정답을 TS로 맞힘)으로 둔갑해 FNR(미탐율)을 거짓으로
            # 낮춘다 → deploy_gate 가 크래시-회피 모델을 통과시키는 안전 구멍이었다(NEW-M1).
            # 평가에서는 가장 보수적으로 — 분류 불능을 '미탐'으로 본다: 최저심각도 등급을
            # 부여해 고등급 정답이면 under-classification(FN)으로 잡히게 한다.
            error_count += 1
            failed = True
            logger.debug("serving eval: run() failed on a doc → miss(%s): %s", fail_pred, exc)
            pred = fail_pred
        rec = dict(row)
        rec["label"] = _label_code(label)
        rec["pred"] = pred
        rec["serving_failed"] = failed
        out.append(rec)

    if error_count:
        logger.warning(
            "serving eval: %d/%d 문서 run() 실패 → '%s'(최저심각도)로 미탐 집계 "
            "(게이트 보수화 — 크래시가 FNR을 거짓 inflate하지 않도록)",
            error_count, len(rows), fail_pred,
        )
    return out


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
    per-record 예측은 predict_via_serving이 담당(카드 평가와 경로 공유) — 여기선 그 위에 집계만.

    Args:
        rows: [{"text":.., "label":..}, ..] (label은 expected_grade도 허용).
        pipeline: 주입 시 그대로 사용(테스트/재사용). 미주입 시 InferencePipeline(model_dir) 생성.
        use_rag/metadata: run()에 전달(소스-프라이어 게이트 등 메타 의존 동작 평가).
    """
    preds = predict_via_serving(
        rows, pipeline=pipeline, model_dir=model_dir,
        use_rag=use_rag, metadata=metadata, labels=labels,
    )
    y_true = [r["label"] for r in preds]
    y_pred = [r["pred"] for r in preds]
    return compute_metrics_from_arrays(
        y_true, y_pred, labels=labels, model_version=model_version
    )
