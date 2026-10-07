"""MIL(다중 인스턴스) 윈도 학습 — 2026-09-30.

trainer.py 는 512토큰 초과 문서를 truncation 으로만 학습해 서빙(overflow 윈도잉)과
어긋난다는 지적을 검증(chunk-overlap-not-the-lever-overflow-window-is-2026-09-30)한 뒤,
대안으로 MIL 방식을 구현했다. chunk_expand(2026-09-13 폐기 — 조각에 부모 라벨을 그대로
물려줘 꼬리조각이 평범한 글을 고등급으로 배우게 함)와 달리 윈도별 라벨을 주지 않고,
서빙과 같은 severe-max 집계로 문서당 확률벡터 하나를 만든 뒤 loss도 문서당 1회만 낸다.

이 파일은 순수 함수(_mil_aggregate_batch·_weighted_nll_from_logprobs)만 검증한다 —
end-to-end 학습 스모크는 무겁다(모델 로드·forward·backward). 그건 실측 스크립트
(scripts/run_mil_windowed_training_eval.py)가 표준 3평가면으로 맡는다.
"""
from __future__ import annotations

import pytest

from koipa.modules.m4_training.trainer import (
    _mil_aggregate_batch,
    _weighted_nll_from_logprobs,
)


def test_single_real_window_passes_through_unchanged():
    """실제 윈도가 1개뿐(나머지 패딩)이면 문서 확률은 그 윈도 확률 그대로다 —
    truncation 만 하던 기존 동작과 동일한 결과를 내야 회귀가 아니다."""
    torch = pytest.importorskip("torch")
    probs = torch.tensor([[
        [0.7, 0.1, 0.1, 0.1],
        [0.0, 0.0, 0.0, 1.0],  # 패딩(window_mask=0) — 값은 무의미
    ]])
    window_mask = torch.tensor([[1.0, 0.0]])
    window_weight = torch.tensor([[50.0, 0.0]])

    out = _mil_aggregate_batch(probs, window_mask, window_weight, severe_ids={0, 1})

    assert torch.allclose(out[0], torch.tensor([0.7, 0.1, 0.1, 0.1]), atol=1e-5)


def test_own_argmax_severe_window_boosts_that_class_above_the_mean():
    """한 윈도가 TS(0번)를 own-argmax로 강하게 확신하면, 그 윈도가 평범한 다른 윈도와
    평균되어 희석되지 않고 TS 열이 그 윈도의 확률까지 승격돼야 한다(서빙과 동일 규칙
    — pipeline.py._aggregate_chunk_probs, own-argmax 후보만 인정, 2026-09-27)."""
    torch = pytest.importorskip("torch")
    probs = torch.tensor([[
        [0.9, 0.05, 0.03, 0.02],  # own-argmax=TS, TS 확률 0.9
        [0.05, 0.05, 0.10, 0.80],  # own-argmax=S3
    ]])
    window_mask = torch.tensor([[1.0, 1.0]])
    window_weight = torch.tensor([[100.0, 100.0]])

    out = _mil_aggregate_batch(probs, window_mask, window_weight, severe_ids={0, 1})

    simple_mean_ts = (0.9 + 0.05) / 2
    assert out[0, 0].item() > simple_mean_ts + 1e-4
    assert abs(out[0, 0].item() - 0.9) < 1e-5


def test_non_severe_window_does_not_boost_a_severe_class_it_did_not_top():
    """어느 윈도도 TS를 own-argmax로 뽑지 않았으면(둘 다 S3가 1등), TS 열은 단순
    길이가중평균 그대로여야 한다 — 근거 없는 열에 승격이 새어 들어가면 안 된다."""
    torch = pytest.importorskip("torch")
    probs = torch.tensor([[
        [0.20, 0.05, 0.05, 0.70],
        [0.10, 0.05, 0.05, 0.80],
    ]])
    window_mask = torch.tensor([[1.0, 1.0]])
    window_weight = torch.tensor([[50.0, 50.0]])

    out = _mil_aggregate_batch(probs, window_mask, window_weight, severe_ids={0, 1})

    expected_ts_mean = (0.20 + 0.10) / 2
    assert abs(out[0, 0].item() - expected_ts_mean) < 1e-5


def test_padding_windows_are_excluded_from_the_length_weighted_mean():
    """window_mask=0 인 패딩 윈도는 길이가중평균에도 severe-max 후보에도 안 들어가야
    한다 — mil_max_windows 가 실제 윈도 수보다 큰(대다수) 문서에서 이게 안 지켜지면
    패딩의 attention_mask=0 forward 결과가 학습 신호를 오염시킨다."""
    torch = pytest.importorskip("torch")
    probs = torch.tensor([[
        [0.4, 0.2, 0.2, 0.2],
        [0.99, 0.0, 0.0, 0.01],  # 패딩 — TS 를 own-argmax 로 강하게 주장해도 무시돼야 함
        [0.99, 0.0, 0.0, 0.01],
    ]])
    window_mask = torch.tensor([[1.0, 0.0, 0.0]])
    window_weight = torch.tensor([[30.0, 0.0, 0.0]])

    out = _mil_aggregate_batch(probs, window_mask, window_weight, severe_ids={0, 1})

    assert torch.allclose(out[0], torch.tensor([0.4, 0.2, 0.2, 0.2]), atol=1e-5)


def test_aggregation_is_differentiable_through_severe_max_and_weighted_mean():
    """severe_ids 가 2개 이상(기본 TS·S1)일 때도 backward 가 죽지 않아야 한다 —
    2026-09-30 에 인플레이스 열 대입(doc_prob[:, c] = ...)이 두 번째 반복에서
    autograd 버전 카운터와 충돌해 RuntimeError 로 죽었던 실제 버그의 회귀 방지."""
    torch = pytest.importorskip("torch")
    probs = torch.rand(3, 3, 4, requires_grad=True)
    window_mask = torch.tensor([[1.0, 1.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    window_weight = torch.tensor([[10.0, 20.0, 0.0], [15.0, 0.0, 0.0], [5.0, 5.0, 5.0]])

    out = _mil_aggregate_batch(probs, window_mask, window_weight, severe_ids={0, 1})
    out.sum().backward()

    assert probs.grad is not None
    assert torch.isfinite(probs.grad).all()


def test_weighted_nll_matches_plain_nll_when_weights_are_uniform():
    torch = pytest.importorskip("torch")
    functional = pytest.importorskip("torch.nn.functional")
    log_probs = torch.log(
        torch.tensor([[0.9, 0.05, 0.03, 0.02], [0.1, 0.1, 0.1, 0.7]]).clamp(min=1e-9)
    )
    labels = torch.tensor([0, 3])

    actual = _weighted_nll_from_logprobs(log_probs, labels, sample_weights=torch.ones(2))
    expected = functional.nll_loss(log_probs, labels)

    assert torch.allclose(actual, expected, atol=1e-6)


def test_weighted_nll_downweights_a_low_weight_row():
    torch = pytest.importorskip("torch")
    log_probs = torch.log(
        torch.tensor([[0.9, 0.05, 0.03, 0.02], [0.01, 0.01, 0.01, 0.97]]).clamp(min=1e-9)
    )
    labels = torch.tensor([0, 3])  # 둘 다 정답 맞춤 — 손실은 작다

    full_weight = _weighted_nll_from_logprobs(
        log_probs, labels, sample_weights=torch.tensor([1.0, 1.0])
    )
    half_second = _weighted_nll_from_logprobs(
        log_probs, labels, sample_weights=torch.tensor([1.0, 0.1])
    )

    # 두 번째 행 가중을 낮추면 그 행의 손실 기여가 줄어 전체 손실 구성이 달라져야 한다
    # (완전히 같은 값이면 가중이 반영되지 않은 것).
    assert abs(float(full_weight) - float(half_second)) > 1e-6
