"""배포 rule-fallback 분류 경로의 고등급 veto 회귀 — 실 분류기 실행(하드코딩 pred 아님).

파일럿 합격선(TS/S1 미탐0)이 push/PR 자동 CI 에서 회귀검증되는 지점이 없던 갭(감사 rank1) 마감.
기존 안전 게이트 테스트(test_metamorphic_gate·test_adversarial_gate·test_deploy_gate)는 하드코딩 pred
위 순수 판정함수만 검증했다 — 실제 분류기가 고등급 텍스트를 어떻게 분류하는지는 자동 회귀검증 안 됨.

본 테스트는 InferencePipeline(model_dir=None)=CI/dryrun 이 실제로 배포·서빙하는 rule-fallback 경로를
직접 태워, 강한 고등급 신호 텍스트가 더 낮은 등급으로 미탐(severity floor 위반)되지 않는지 잠근다.
모델·GPU·DB·LLM·sentencepiece 불요(rule 경로) → test-lite 자동 수집. 시드는 커밋된 KEYWORD_SEEDS 폴백이라 결정론적.
한계: 실 학습모델의 veto 는 GPU·발주기관 제공 모델 필요(release-gate 수동 dispatch) — 본 테스트는 배포
기본 경로(rule-fallback)의 자동 안전망.
"""

from __future__ import annotations

import pytest

from lloydk.modules.m5_inference.pipeline import InferencePipeline
from lloydk.schemas.common import Grade

# 작을수록 더 민감(고등급). judge_doc(run_acceptance) 와 동일 규약.
_SEVERITY = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}

# 커밋된 KEYWORD_SEEDS 의 강한 고등급 키워드로 구성 — 룰엔진이 확실히 발화(결정론적).
_HIGH_CASES = [
    ("특급기밀. 핵심 원천기술 반도체 공정 레시피, EUV 공정 파라미터, 신약 후보물질.", "TS"),
    ("극비 M&A 계획과 인수합병 가격, 회사 매각 협상 조건.", "TS"),
    ("원자로 노심 설계와 핵연료 농축 절차, 유도무기 제어 알고리즘.", "TS"),
    ("1급 비밀 영업비밀. 고객 데이터베이스, 원가 구조, 알고리즘 소스코드, 자금계획.", "S1"),
    ("공정 노하우와 수율 개선 방법, 신제품 개발 로드맵, 특허 출원 계획.", "S1"),
    ("대외비 내부 자료. 분기 매출, 사업 계획서, 조직 개편, 거래처 명단.", "S2"),
]


@pytest.fixture(scope="module")
def rule_pipe():
    # model_dir=None → rule-fallback(배포 CI/dryrun 이 실제 서빙하는 경로). DB/모델/GPU 불요.
    return InferencePipeline(model_dir=None)


@pytest.mark.parametrize("text,expected", _HIGH_CASES)
def test_rule_fallback_never_underclassifies_high_grade(rule_pipe, text, expected):
    res = rule_pipe.run(text, return_evidence=False)
    pred = res.label.value if hasattr(res.label, "value") else str(res.label)
    exp_sev, pred_sev = _SEVERITY[expected], _SEVERITY.get(pred, 3)
    # veto(severity floor): 예측 severity 가 기대보다 낮으면 미탐 → FAIL. 안전방향 과분류(더 높은 등급)는 허용.
    assert pred_sev <= exp_sev, (
        f"미탐(veto 위반): '{expected}' 신호 텍스트가 더 낮은 등급 '{pred}' 로 분류됨"
    )


def test_high_grade_signal_never_falls_to_public_s3(rule_pipe):
    """가장 강한 불변식: 고등급 신호 텍스트는 절대 S3(공개/유출)로 자동분류되지 않는다."""
    misses = []
    for text, expected in _HIGH_CASES:
        res = rule_pipe.run(text, return_evidence=False)
        pred = res.label.value if hasattr(res.label, "value") else str(res.label)
        if pred == Grade.S3.value:
            misses.append(expected)
    assert not misses, f"고등급 미탐(S3 자동분류): {misses}"
