"""GET /metrics/* — 운영 모델 성능 지표 (W4 M6에서 풀로 채움)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from koipa.api._jwt_auth import require_auth
from koipa.schemas.metrics import ConfusionMatrix, MetricsHistory, MetricsReport
from koipa.services.metrics_service import MetricsService

router = APIRouter(tags=["metrics"], dependencies=[Depends(require_auth)])


@router.get("/metrics/latest", response_model=MetricsReport)
def metrics_latest():
    res = MetricsService().latest()
    if res is None:
        raise HTTPException(status_code=404, detail="no active model")
    return res


@router.get("/metrics/history", response_model=MetricsHistory)
def metrics_history(limit: int = Query(default=20, ge=1, le=100)):
    return MetricsService().history(limit=limit)


@router.get("/metrics/confusion-matrix/{model_version}", response_model=ConfusionMatrix)
def metrics_cm(model_version: str):
    res = MetricsService().confusion_matrix(model_version)
    if res is None:
        raise HTTPException(status_code=404, detail="model_version not found")
    return res


@router.get("/dashboard/summary")
def dashboard_summary() -> dict:
    """운영 현황 실측 카운트 — 총 분류수·등급분포·검수큐·교정·검증라벨.

    모두 DB 실측 COUNT(가공/추정 없음). '정밀도'는 실문서 정답 부재로 산출 불가 →
    측정 가능한 지표(자동확정률·검수큐·교정수·검수반영수)만 노출한다(정직성).
    """
    from sqlalchemy import func, select  # noqa: PLC0415

    zero = {
        "total_classifications": 0, "by_grade": {}, "by_status": {},
        "review_queue": 0, "auto_confirmed": 0, "reviewed": 0,
        "corrections": 0, "verified_labels": 0, "db": "unavailable",
    }
    try:
        from koipa.db import SessionLocal  # noqa: PLC0415
        from koipa.db.models import (  # noqa: PLC0415
            Classification, ClassificationLevel, Correction, DocumentLabel,
        )
    except Exception:  # noqa: BLE001
        return zero
    try:
        db = SessionLocal()
    except Exception:  # noqa: BLE001
        return zero
    try:
        total = int(db.execute(select(func.count()).select_from(Classification)).scalar() or 0)
        grade_rows = db.execute(
            select(ClassificationLevel.level_code, func.count(Classification.classification_id))
            .join(ClassificationLevel, Classification.predicted_level_id == ClassificationLevel.level_id)
            .group_by(ClassificationLevel.level_code)
        ).all()
        by_grade = {code: int(n) for code, n in grade_rows}
        status_rows = db.execute(
            select(Classification.status, func.count()).group_by(Classification.status)
        ).all()
        by_status = {str(s): int(n) for s, n in status_rows}
        corrections = int(db.execute(select(func.count()).select_from(Correction)).scalar() or 0)
        verified = int(
            db.execute(
                select(func.count()).select_from(DocumentLabel).where(DocumentLabel.is_verified.is_(True))
            ).scalar() or 0
        )
        return {
            "total_classifications": total,
            "by_grade": by_grade,
            "by_status": by_status,
            "review_queue": by_status.get("needs_review", 0),
            "auto_confirmed": by_status.get("staging", 0),
            "reviewed": by_status.get("confirmed", 0) + by_status.get("corrected", 0),
            "corrections": corrections,
            "verified_labels": verified,
            "db": "ok",
        }
    except Exception:  # noqa: BLE001
        return zero
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
