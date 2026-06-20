"""캘리브레이션 측정 — ECE·Brier·reliability (런타임, #10).

서빙은 temperature scaling을 *적용*만 하고(m5 pipeline), 0.7 confidence 게이트가
그 위에 선다. 그러나 보정이 실제로 맞는지(ECE)를 런타임에서 측정하는 곳이 없어
OOD 문체에서 게이트 의미가 보장되지 않았다. 본 모듈은 confidence↔정답 일치율의
ECE/Brier/reliability를 src/ 런타임 함수로 제공하고, DB의 분류+교정에서 직접 계산해
report·drift_monitor가 호출할 수 있게 한다.

측정 대상은 'top-label confidence calibration' — 예측한 등급의 confidence가 그 예측이
맞을 확률과 일치하는가. 0.7 게이트가 직접 의존하는 양이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.db.models import Classification, ClassificationLevel, Correction


def _in_bin(c: float, lo: float, hi: float, last: bool) -> bool:
    """마지막 bin만 우측 경계 포함([lo,hi]), 나머지는 [lo,hi)."""
    return lo <= c <= hi if last else lo <= c < hi


def reliability_bins(
    confidences: Sequence[float], correct: Sequence[bool], n_bins: int = 10
) -> list[dict]:
    """[0,1]을 n_bins 등분해 bin별 평균 confidence·정확도·건수 산출(reliability diagram용)."""
    bins: list[dict] = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        last = b == n_bins - 1
        members = [
            (float(c), bool(y))
            for c, y in zip(confidences, correct)
            if _in_bin(float(c), lo, hi, last)
        ]
        if members:
            avg_conf = sum(c for c, _ in members) / len(members)
            acc = sum(1 for _, y in members if y) / len(members)
        else:
            avg_conf, acc = 0.0, 0.0
        bins.append({
            "lo": round(lo, 3), "hi": round(hi, 3), "count": len(members),
            "avg_confidence": round(avg_conf, 4), "accuracy": round(acc, 4),
            "gap": round(abs(avg_conf - acc), 4),
        })
    return bins


def expected_calibration_error(
    confidences: Sequence[float], correct: Sequence[bool], n_bins: int = 10
) -> float:
    """ECE = Σ (|bin| / N) · |평균신뢰도 − 정확도|. 0=완벽 보정, 클수록 과신/과소신."""
    n = len(confidences)
    if n == 0:
        return 0.0
    ece = 0.0
    for bn in reliability_bins(confidences, correct, n_bins):
        if bn["count"]:
            ece += (bn["count"] / n) * abs(bn["avg_confidence"] - bn["accuracy"])
    return round(ece, 4)


def brier_score(confidences: Sequence[float], correct: Sequence[bool]) -> float:
    """top-label Brier = mean((confidence − 1[correct])²). 낮을수록 보정·정확."""
    n = len(confidences)
    if n == 0:
        return 0.0
    s = sum((float(c) - (1.0 if y else 0.0)) ** 2 for c, y in zip(confidences, correct))
    return round(s / n, 4)


@dataclass
class CalibrationResult:
    model_version: str
    sample_count: int
    ece: float
    brier: float
    bins: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "sample_count": self.sample_count,
            "ece": self.ece,
            "brier": self.brier,
            "bins": self.bins,
        }


def compute_calibration_from_arrays(
    confidences: Sequence[float], correct: Sequence[bool],
    *, model_version: str = "n/a", n_bins: int = 10,
) -> CalibrationResult:
    return CalibrationResult(
        model_version=model_version,
        sample_count=len(confidences),
        ece=expected_calibration_error(confidences, correct, n_bins),
        brier=brier_score(confidences, correct),
        bins=reliability_bins(confidences, correct, n_bins),
    )


def compute_calibration_from_db(
    model_version: str, *, tenant_id: str | None = None, n_bins: int = 10
) -> CalibrationResult | None:
    """DB 분류+교정에서 (confidence, 정답여부) 추출 후 ECE/Brier 계산.

    정답 = 최신 correction(없으면 predicted) == predicted 이면 correct(metrics와 동일
    truth 정의). None: DB 미가용 / 해당 model_version 분류 0건.
    """
    try:
        with session_scope() as db:
            rows = _fetch_conf_correct(db, model_version, tenant_id=tenant_id)
    except SQLAlchemyError:
        return None
    if not rows:
        return None
    confs = [r[0] for r in rows]
    correct = [r[1] for r in rows]
    return compute_calibration_from_arrays(
        confs, correct, model_version=model_version, n_bins=n_bins
    )


def _fetch_conf_correct(db, model_version: str, *, tenant_id: str | None = None):
    code_by_id = {
        lv.level_id: lv.level_code
        for lv in db.execute(select(ClassificationLevel)).scalars()
    }
    stmt = select(Classification).where(Classification.model_version == model_version)
    if tenant_id:
        stmt = stmt.where(Classification.tenant_id == tenant_id)
    cls_list = list(db.execute(stmt).scalars())
    if not cls_list:
        return []

    cls_ids = [c.classification_id for c in cls_list]
    corr_rows = list(
        db.execute(
            select(Correction)
            .where(Correction.classification_id.in_(cls_ids))
            .order_by(Correction.corrected_at.desc())
        ).scalars()
    )
    latest_corr: dict = {}
    for c in corr_rows:
        latest_corr.setdefault(c.classification_id, c.corrected_level_id)

    out: list[tuple[float, bool]] = []
    for c in cls_list:
        pred_code = code_by_id.get(c.predicted_level_id)
        if pred_code is None or c.confidence is None:
            continue
        truth_level_id = latest_corr.get(c.classification_id, c.predicted_level_id)
        truth_code = code_by_id.get(truth_level_id, pred_code)
        out.append((float(c.confidence), truth_code == pred_code))
    return out
