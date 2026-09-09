"""등급 불변성 하니스가 재는 것을 실제로 재는가.

왜 이 시험이 있는가. 불변성 측정은 **개입**이라 변형 자체가 틀리면 결과가 통째로 거짓이
된다. 예를 들어 덧붙이는 문구에 등급 어휘(대외비·기밀)나 공개 어휘(공고)가 섞이면 "내용을
바꾸지 않았다"는 전제가 깨지고, 그때 나온 변화율은 모델의 불안정성이 아니라 우리가 넣은
신호를 잰 값이 된다.

시험이 지키는 것 셋.
    ① 변형이 실제로 내용을 보존하는가(문장 집합·등급 어휘 부재)
    ② 방향 판정이 맞는가 — 하향(미탐)과 상향을 뒤집으면 결론이 반대가 된다
    ③ 도구가 분류기 없이 조용히 룰 단독 경로를 재고 끝나지 않는가
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_grade_invariance.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("measure_grade_invariance", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# 등급을 가리키는 어휘 — 덧붙이는 문구에 하나라도 있으면 축이 오염된다.
_GRADE_WORDS = ("비밀", "대외비", "기밀", "극비", "공개", "공고", "공표", "영업비밀")


def test_filler_carries_no_grade_signal(mod):
    """덧붙이는 문장은 어느 등급도 가리키면 안 된다."""
    for sentence in mod._FILLER:
        for word in _GRADE_WORDS:
            assert word not in sentence, f"채움 문장에 등급 어휘가 있다: {word!r} in {sentence!r}"


def test_content_preserving_variants_keep_every_sentence(mod):
    """공백·순서 변형은 문장을 하나도 잃거나 더하지 않는다."""
    text = "첫 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."
    base = set(mod._split_sentences(text))
    got = mod.variants(text)
    for axis in ("whitespace", "reorder"):
        mutated, _ = got[axis]
        assert set(mod._split_sentences(mutated)) == base, f"{axis} 가 내용을 바꿨다"


def test_padding_only_adds_and_never_removes(mod):
    """덧붙이기 축은 원문을 그대로 포함해야 한다 — 빼면 그건 다른 실험이다."""
    text = "공정 조건과 측정값을 정리했습니다."
    for axis in ("pad_short", "pad_long"):
        mutated, _ = mod.variants(text)[axis]
        assert text in mutated, f"{axis} 가 원문을 보존하지 않았다"
    short, _ = mod.variants(text)["pad_short"]
    long_, _ = mod.variants(text)["pad_long"]
    assert len(long_) > len(short), "pad_long 이 pad_short 보다 길어야 축이 의미를 가진다"


def test_control_axis_is_metadata_only(mod):
    """통제군은 **본문을 건드리지 않고** 출처만 바꾼다 — 두 가지를 같이 바꾸면 원인이 안 갈린다."""
    text = "공정 조건과 측정값을 정리했습니다."
    mutated, meta = mod.variants(text)["source_public"]
    assert mutated == text
    assert meta == {"source_type": "public"}


def test_direction_is_computed_from_grade_order(mod):
    """하향(미탐)과 상향을 뒤집으면 결론이 정반대가 된다 — 순서표를 고정한다."""
    order = mod._GRADE_ORDER
    assert order["TS"] < order["S1"] < order["S2"] < order["S3"], (
        "등급 순서가 뒤집혔다 — 낮은 값이 더 심각한 등급이어야 한다"
    )


def test_harness_refuses_to_measure_without_a_classifier(mod):
    """분류기 없이 재면 룰 단독 경로를 재고 배포본을 잰 것처럼 보고하게 된다."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert '_model", None) is None' in src, "분류기 미로드를 확인하는 분기가 없다"
    assert "CLASSIFIER_MODEL_DIR" in src, "무엇을 주면 되는지 말해 주지 않으면 다음 사람이 막힌다"


def test_harness_forces_deploy_profile_flags(mod):
    """배포본 거동을 잰다면서 기본 OFF 게이트를 안 켜면 다른 것을 잰다(실측 2026-09-10)."""
    import os

    assert os.environ.get("METADATA_FLOOR_ENABLED") == "true"


def test_labels_are_not_used(mod):
    """불변성은 정답이 필요 없다 — 라벨을 읽으면 봉인된 평가셋을 소모한 셈이 된다."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert '"label"' not in src and "row.get('label'" not in src, (
        "라벨을 읽고 있다 — 불변성 측정은 라벨 없이 성립해야 봉인과 무관해진다"
    )
