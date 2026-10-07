"""평가 지표 계산 — Accuracy·Precision·Recall·F1·FNR per-grade.

설계:
- compute_metrics_from_arrays(y_true, y_pred, labels) — 순수 함수 (테스트 편의)
- compute_metrics_from_db(model_version) — DB의 classifications + corrections에서
  진실 라벨 추출 (corrections 최신 > predicted) 후 위 함수 호출

FNR per-grade는 본 사업 핵심 KPI. 보안 미탐(undeclass) = 고등급 정답을 저등급으로
예측 = row 합에서 대각선 빼고 = 미탐 건. 등급별 분리 보고.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from koipa.db import session_scope
from koipa.db.models import Classification, ClassificationLevel, Correction
from koipa.modules.m3_labeling.seeds import GRADE_ORDER

DEFAULT_LABELS = list(GRADE_ORDER)  # ["TS","S1","S2","S3"] — 단일 소스(seeds.GRADE_ORDER)에서 파생


@dataclass
class MetricsResult:
    model_version: str
    labels: list[str]
    sample_count: int
    accuracy: float
    precision_macro: float
    recall_macro: float
    f1_macro: float
    fnr_overall: float
    fnr_by_grade: dict[str, float] = field(default_factory=dict)
    # [A4] 방향성 미탐(고등급→저등급)만 측정 — RFP FUN-024 핵심 KPI. 과분류(저→고) 제외.
    fnr_underclass_overall: float = 0.0
    fnr_underclass_by_grade: dict[str, float] = field(default_factory=dict)
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)
    confusion_matrix: list[list[int]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "labels": self.labels,
            "sample_count": self.sample_count,
            "accuracy": self.accuracy,
            "precision_macro": self.precision_macro,
            "recall_macro": self.recall_macro,
            "f1_macro": self.f1_macro,
            "fnr_overall": self.fnr_overall,
            "fnr_by_grade": self.fnr_by_grade,
            "fnr_underclass_overall": self.fnr_underclass_overall,
            "fnr_underclass_by_grade": self.fnr_underclass_by_grade,
            "per_class": self.per_class,
            "confusion_matrix": self.confusion_matrix,
        }


def compute_metrics_from_arrays(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    *,
    labels: Sequence[str] | None = None,
    model_version: str = "n/a",
) -> MetricsResult:
    """y_true·y_pred는 등급 코드 문자열 (TS·S1·S2·S3) 시퀀스.

    [M-metrics-len] 길이 불일치(n != len(y_pred))는 데이터/파이프라인 버그다 —
    예전엔 조용히 all-zeros(0.0 통과)를 반환해 버그를 은폐했다(예: FNR=0.0으로 보여
    미탐 회귀를 게이트가 못 잡음). 이제 ValueError를 raise한다. n==0만 N/A(빈 결과)로
    두고 caller/report가 N/A로 취급한다.
    """
    label_list = list(labels) if labels else list(DEFAULT_LABELS)
    n = len(y_true)
    if n != len(y_pred):
        raise ValueError(
            f"y_true/y_pred length mismatch: {n} != {len(y_pred)} "
            "(data/pipeline bug — refusing to silently return all-zeros)"
        )
    if n == 0:
        return _empty_result(model_version, label_list)

    cm = confusion_matrix(y_true, y_pred, labels=label_list)
    acc = float(accuracy_score(y_true, y_pred))
    p, r, f, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=label_list, average="macro", zero_division=0
    )
    p_per, r_per, f_per, support_per = precision_recall_fscore_support(
        y_true, y_pred, labels=label_list, average=None, zero_division=0
    )

    fnr_overall, fnr_by = _fnr_from_cm(cm, label_list)
    # [A4] 방향성 미탐(고등급→저등급) — RFP FUN-024 핵심 KPI. 과분류(저→고)는 제외.
    fnr_uc_overall, fnr_uc_by = _fnr_underclass_from_cm(cm, label_list)
    per_class = {
        lbl: {
            "precision": float(p_per[i]),
            "recall": float(r_per[i]),
            "f1": float(f_per[i]),
            "support": int(support_per[i]),
            "fnr": float(fnr_by.get(lbl, 0.0)),
        }
        for i, lbl in enumerate(label_list)
    }

    return MetricsResult(
        model_version=model_version,
        labels=label_list,
        sample_count=n,
        accuracy=acc,
        precision_macro=float(p),
        recall_macro=float(r),
        f1_macro=float(f),
        fnr_overall=fnr_overall,
        fnr_by_grade=fnr_by,
        fnr_underclass_overall=fnr_uc_overall,
        fnr_underclass_by_grade=fnr_uc_by,
        per_class=per_class,
        confusion_matrix=cm.tolist(),
    )


def compute_metrics_from_db(
    model_version: str,
    *,
    labels: Sequence[str] | None = None,
) -> MetricsResult | None:
    """DB의 classifications + corrections로 진실 라벨 vs 예측 라벨 페어 추출.

    진실 라벨:
      - 가장 최신 correction의 corrected_level → 정답 (direction 무관 — confirm 포함).
        [M-metrics-confirm] confirm은 '사람이 그 값으로 최종 동의'한 신호라 무시하면 안 됨.
      - 보정 없으면 → predicted_level이 정답 (확정 가정)

    None 반환: DB 미가용 / 해당 model_version 분류 0건.
    """
    try:
        with session_scope() as db:
            pairs = _fetch_truth_pred_pairs(db, model_version)
    except SQLAlchemyError:
        return None

    if not pairs:
        return None

    y_true, y_pred = zip(*pairs)
    return compute_metrics_from_arrays(
        list(y_true), list(y_pred), labels=labels, model_version=model_version
    )


# ------------------------------------------------------------
# internals
# ------------------------------------------------------------


def _fnr_from_cm(cm: np.ndarray, labels: Sequence[str]) -> tuple[float, dict[str, float]]:
    """행=정답, 열=예측. FNR_i = (row_sum - tp) / row_sum."""
    fnr_by: dict[str, float] = {}
    fn_total = 0
    pos_total = 0
    for i, name in enumerate(labels):
        row_sum = int(cm[i].sum())
        tp = int(cm[i, i])
        fn = row_sum - tp
        fnr_by[name] = float(fn / row_sum) if row_sum else 0.0
        fn_total += fn
        pos_total += row_sum
    overall = float(fn_total / pos_total) if pos_total else 0.0
    return overall, fnr_by


# 영업비밀 등급 심각도 순서 (낮은 값=고등급). under-class = 예측이 정답보다 *낮은 등급*.
_GRADE_SEVERITY = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}


def _fnr_underclass_from_cm(cm: np.ndarray, labels: Sequence[str]) -> tuple[float, dict[str, float]]:
    """방향성 미탐(under-classification)만 측정 — RFP FUN-024 핵심 KPI.

    행=정답, 열=예측. 정답보다 *낮은 등급(심각도↓)*으로 예측된 것만 FN으로 센다.
    과분류(저등급→고등급)는 보안상 안전한 오류이므로 제외 — 일반 FNR(1-recall)과 구별.
    심각도 순서에 없는 커스텀 등급은 under-class 계산에서 안전하게 제외.
    """
    fnr_by: dict[str, float] = {}
    fn_total = 0
    pos_total = 0
    for i, name in enumerate(labels):
        row_sum = int(cm[i].sum())
        true_sev = _GRADE_SEVERITY.get(name)
        under = 0
        if true_sev is not None:
            for j, pname in enumerate(labels):
                pred_sev = _GRADE_SEVERITY.get(pname)
                if pred_sev is not None and pred_sev > true_sev:  # 더 낮은 등급으로 예측 = 미탐
                    under += int(cm[i, j])
        fnr_by[name] = float(under / row_sum) if row_sum else 0.0
        fn_total += under
        pos_total += row_sum
    overall = float(fn_total / pos_total) if pos_total else 0.0
    return overall, fnr_by


def _fetch_truth_pred_pairs(
    db: Session,
    model_version: str,
) -> list[tuple[str, str]]:
    # level_id → code 매핑
    code_by_id = {
        lv.level_id: lv.level_code
        for lv in db.execute(select(ClassificationLevel)).scalars()
    }

    # classifications 조회
    stmt = select(Classification).where(Classification.model_version == model_version)
    cls_list = list(db.execute(stmt).scalars())
    if not cls_list:
        return []

    # 각 classification의 가장 최신 correction 1건 lookup.
    # [M-metrics-confirm] direction 무관. 예전엔 direction!='confirm'만 봤는데,
    # '처음 교정(S3→TS) 후 나중에 그 값(TS)으로 confirm'된 경우 confirm을 무시하면
    # 더 오래된(또는 다른) correction을 정답으로 잡아 정답이 왜곡됐다. confirm은
    # '사람이 그 값으로 최종 동의'한 가장 신뢰할 신호이므로 최신 correction의
    # corrected_level_id를 그대로 정답으로 쓴다.
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

    pairs: list[tuple[str, str]] = []
    for c in cls_list:
        pred_code = code_by_id.get(c.predicted_level_id)
        if pred_code is None:
            continue
        truth_level_id = latest_corr.get(c.classification_id, c.predicted_level_id)
        truth_code = code_by_id.get(truth_level_id, pred_code)
        pairs.append((truth_code, pred_code))
    return pairs


def _empty_result(model_version: str, labels: list[str]) -> MetricsResult:
    return MetricsResult(
        model_version=model_version,
        labels=labels,
        sample_count=0,
        accuracy=0.0,
        precision_macro=0.0,
        recall_macro=0.0,
        f1_macro=0.0,
        fnr_overall=0.0,
        fnr_by_grade={lbl: 0.0 for lbl in labels},
        per_class={},
        confusion_matrix=[],
    )
