"""[정직성 P1/P2] /classify/explain method 라벨 — rule-fallback 을 'model' 로 오표기 방지.

배경(전수조사): 과거 `method = 'rule+rag' if model_version=='poc' else 'model+rule+rag'` 는
실제 rule-fallback 의 model_version('rule-fallback-v0'/'rule-fallback')이 'poc' 가 아니라서
룰 폴백 판정을 'model+rule+rag'(학습 분류기 사용)로 둔갑시켰다. 검수자에게 "모델이 판정했다"고
잘못 표시 → 근거 신뢰 왜곡. _method_label 이 폴백 마커를 정확히 룰 경로로 판정하는지 잠근다.
"""

from __future__ import annotations

import pytest

from lloydk.api.explain import _method_label


@pytest.mark.parametrize(
    "model_version",
    ["rule-fallback-v0", "rule-fallback", "poc", "none", "", None, "RULE-FALLBACK-V0"],
)
def test_rule_paths_labeled_rule(model_version):
    assert _method_label(model_version) == "rule+rag"


@pytest.mark.parametrize(
    "model_version",
    ["v-dd3abab9", "v-f9b5cedb", "bpilot-2.0", "model", "human_review:admin01"],
)
def test_model_paths_labeled_model(model_version):
    assert _method_label(model_version) == "model+rule+rag"


def test_rule_fallback_not_mislabeled_as_model():
    # 회귀 핵심: 실제 폴백 문자열이 model 로 둔갑하면 안 된다.
    assert _method_label("rule-fallback-v0") != "model+rule+rag"
