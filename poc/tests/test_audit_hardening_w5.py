"""W5 하드닝 감사 수정 검증 — A1(calibration 자동로드)·A3/B2(factor 정합)·A4(방향성 FNR).

로직 감사(2026-06)에서 발견한 결함의 회귀 방지:
- svm_levels_for_grade 가 grade_from_svm 을 역으로 재구성(곱↔등급 정합)
- 룰 라벨의 factor_scores 가 최종 등급과 정합(‘S0V0M0인데 TS’ 모순 방지)
- _fnr_underclass_from_cm 이 방향성 미탐만 카운트(과분류 제외, RFP FUN-024)
- temperature.json 자동로드 폴백(settings 미지정 시)
"""

from __future__ import annotations

import numpy as np
import pytest

from koipa.modules.m3_labeling.rule_engine import grade_from_svm, svm_levels_for_grade


# ── B2/A3: svm_levels_for_grade ↔ grade_from_svm 정합 ───────────────────────────
@pytest.mark.parametrize("grade", ["TS", "S1", "S2", "S3"])
def test_svm_levels_reconstruct_grade(grade):
    s, v, m = svm_levels_for_grade(grade)
    assert grade_from_svm(s, v, m) == grade, f"{grade}: S{s}V{v}M{m} 곱={s*v*m} 재구성 실패"


def test_rule_factors_consistent_with_final_grade():
    """룰 라벨의 표시 S/V/M 곱이 최종 등급과 정합해야 한다(모순 표기 방지)."""
    from koipa.modules.m3_labeling.pipeline import LabelingPipeline
    p = LabelingPipeline()
    r = p.label("암호 키 관리 보고서. HSM·FIPS 운영. master key root CA. 외부 유출 금지 기밀.")
    g = r.grade.value if hasattr(r.grade, "value") else str(r.grade)
    if r.rule_result is None:
        pytest.skip("rule_result 없음")
    fs = r.rule_result.factor_scores
    s, v, m = int(fs.get("SECRECY", 0)), int(fs.get("VALUE", 0)), int(fs.get("MANAGEMENT", 0))
    assert grade_from_svm(s, v, m) == g, f"표시 S{s}V{v}M{m}→{grade_from_svm(s,v,m)} ≠ 최종 {g}"


# ── A4: 방향성 미탐(under-class)만 카운트, 과분류 제외 ──────────────────────────
def test_fnr_underclass_excludes_overclassification():
    from koipa.modules.m6_evaluation.metrics import _fnr_from_cm, _fnr_underclass_from_cm
    labels = ["TS", "S1", "S2", "S3"]
    # 행=정답, 열=예측. S3 정답행: TS로 2건 과분류(저→고) + S3 8건.
    cm = np.array([
        [8, 1, 1, 0],   # TS 정답: S1·S2로 2건 미탐(under)
        [0, 9, 1, 0],   # S1 정답: S2로 1건 미탐
        [0, 0, 10, 0],  # S2 정답: 완벽
        [2, 0, 0, 8],   # S3 정답: TS로 2건 과분류(over) — under-class 아님
    ])
    _, fnr_by = _fnr_from_cm(cm, labels)             # 1-recall (양방향)
    _, uc_by = _fnr_underclass_from_cm(cm, labels)   # 방향성(미탐만)
    # S3는 최저등급 → under-class 불가. 과분류 2건은 방향성 FNR에서 제외.
    assert uc_by["S3"] == 0.0, "과분류가 방향성 미탐에 잘못 포함됨"
    assert fnr_by["S3"] == pytest.approx(0.2), "1-recall은 과분류 포함(0.2)이어야"
    # TS: S1+S2로 2건 미탐 / row 10
    assert uc_by["TS"] == pytest.approx(0.2)
    # S1: S2로 1건 미탐 / row 10
    assert uc_by["S1"] == pytest.approx(0.1)


# ── A1: temperature.json 자동로드 폴백 ─────────────────────────────────────────
def test_temperature_autoload_fallback():
    from koipa.config import settings
    from koipa.modules.m5_inference.pipeline import InferencePipeline
    if abs(float(settings.classifier_temperature) - 1.0) > 1e-9:
        pytest.skip(".env가 classifier_temperature를 명시 → 폴백 경로 비활성")
    pipe = InferencePipeline()  # 모델 없음
    assert pipe._temperature == 1.0  # 모델 temp 없음 → 무보정
    pipe._model_temperature = 2.5    # 모델 동봉 보정값 시뮬
    assert pipe._temperature == 2.5  # settings 미지정 시 모델 temp 사용
