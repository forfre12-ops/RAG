# -*- coding: utf-8 -*-
"""평가 권위 게이트 계약 — PASS 를 만들 수 있는 경로가 하나뿐임을 잠근다.

왜(2026-09-14). 리포트에는 정보가 다 있었는데 사람이 요약하면서 빠뜨려
같은 날 두 시간 안에 두 번 잘못 보고했다. 그래서 출력이 아니라 **반환값**에 걸었고,
그 계약을 여기서 고정한다. 이 시험이 깨지면 게이트가 열린 것이다.
"""
from __future__ import annotations

import itertools

import pytest

from koipa.eval_authority import (
    CIMethod,
    ClaimStatus,
    EvalEvidence,
    MetricName,
    Representativeness,
    TargetSpec,
    TruthTier,
    assess,
    required_n,
)

PMR002 = TargetSpec(
    name="PMR-002", max_miss_rate=0.10, resolved=True,
    source="제안요청서.pdf PDF 37쪽 '성능 목표(재현율 90% 이상 등)'",
)
UNRESOLVED = TargetSpec.unresolved(
    conflicting=("RFP 재현율 90%", "KL 품질계획서 80%", "시나리오 KPI 미탐 5%")
)


def _ev(**kw) -> EvalEvidence:
    base = dict(
        suite_id="suite", metric=MetricName.SERVING_RECALL, truth_tier=TruthTier.GOLD,
        n=60, misses=1, ci_method=CIMethod.CLOPPER_PEARSON_ONE_SIDED_95,
        target=PMR002, representativeness=Representativeness.PROVEN,
        training_overlap_checked=True, training_overlap_count=0,
    )
    base.update(kw)
    return EvalEvidence(**base)


# ── 오늘 실제로 한 실수 두 개를 그대로 재현한다 ──────────────────────────────

def test_today_mistake_1_silver_truth_cannot_claim_pass() -> None:
    """holdout109 고등급 36건(GOLD 0·SILVER 26·BRONZE 10)으로 PMR-002 충족을 말했다."""
    v = assess(_ev(suite_id="holdout109", truth_tier=TruthTier.SILVER, n=36, misses=1,
                   training_overlap_checked=True, training_overlap_count=0))
    assert v.status is not ClaimStatus.PASS
    assert "충족" not in v.as_headline()
    assert "GOLD 아님" in " ".join(v.reasons)


def test_today_mistake_2_training_overlap_blocks() -> None:
    """그 36건 중 13건이 학습셋에 있었다 — 진단값으로도 쓰면 안 된다."""
    v = assess(_ev(suite_id="holdout109", truth_tier=TruthTier.SILVER, n=36, misses=1,
                   training_overlap_checked=True, training_overlap_count=13))
    assert v.status is ClaimStatus.BLOCKED
    assert any("학습 겹침 13건" in r for r in v.reasons)


# ── PASS 로 가는 길이 하나뿐임 ────────────────────────────────────────────

def test_pass_requires_every_condition() -> None:
    assert assess(_ev()).status is ClaimStatus.PASS


@pytest.mark.parametrize("tier", [t for t in TruthTier if t is not TruthTier.GOLD])
def test_non_gold_never_passes(tier: TruthTier) -> None:
    assert assess(_ev(truth_tier=tier)).status is not ClaimStatus.PASS


def test_unresolved_target_blocks_even_with_gold() -> None:
    v = assess(_ev(target=UNRESOLVED))
    assert v.status is ClaimStatus.BLOCKED
    assert any("TARGET_UNRESOLVED" in r for r in v.reasons)


def test_unchecked_overlap_is_not_zero_overlap() -> None:
    """'안 잰 것'과 '겹침 없음'이 같은 화면에서 구분되지 않던 것이 반복 결함이었다."""
    v = assess(_ev(training_overlap_checked=False, training_overlap_count=0))
    assert v.status is ClaimStatus.BLOCKED
    assert any("미검사" in r for r in v.reasons)


def test_none_tier_is_worse_than_bronze() -> None:
    """golden100 은 BRONZE 가 아니라 출처 기록 자체가 없다(NONE) — 회귀 대조도 금지."""
    assert assess(_ev(truth_tier=TruthTier.BRONZE)).status is ClaimStatus.DIAGNOSTIC_ONLY
    assert assess(_ev(truth_tier=TruthTier.NONE)).status is ClaimStatus.BLOCKED


def test_circular_tier_blocked() -> None:
    assert assess(_ev(truth_tier=TruthTier.CIRCULAR)).status is ClaimStatus.BLOCKED


# ── 대표성 ───────────────────────────────────────────────────────────────

def test_representativeness_blocks_recall_claims_only() -> None:
    """재현율 주장에는 대표성이 필요하고, 운영 안전성 지표는 면 단위로 읽는다."""
    blocked = assess(_ev(metric=MetricName.SERVING_RECALL,
                         representativeness=Representativeness.UNPROVEN))
    assert blocked.status is ClaimStatus.BLOCKED
    ok = assess(_ev(metric=MetricName.HIGH_GRADE_AUTO_CONFIRM_FN_RATE,
                    representativeness=Representativeness.UNPROVEN))
    assert ok.status is ClaimStatus.PASS


def test_unprovable_now_requires_written_blocker() -> None:
    """customer_real 0건이라 지금은 증명 경로가 없다 — 무엇이 있어야 하는지 적게 한다."""
    with pytest.raises(ValueError):
        _ev(representativeness=Representativeness.UNPROVABLE_NOW)
    ev = _ev(representativeness=Representativeness.UNPROVABLE_NOW,
             representativeness_blocker="고객사 실문서 분포(customer_real 현재 0건)")
    assert assess(ev).status is ClaimStatus.BLOCKED


# ── 합산 금지 · 표본 부족 ────────────────────────────────────────────────

def test_aggregating_faces_blocks() -> None:
    v = assess(_ev(aggregated_faces=("holdout109", "golden100")))
    assert v.status is ClaimStatus.BLOCKED
    assert any("합산" in r for r in v.reasons)


def test_small_sample_is_diagnostic_not_pass() -> None:
    """36건 1미탐이면 단측 95% 상한 12.5% — 목표 10% 를 못 넘긴다."""
    v = assess(_ev(n=36, misses=1))
    assert v.status is ClaimStatus.DIAGNOSTIC_ONLY
    assert v.ci_upper is not None and 0.12 < v.ci_upper < 0.13


def test_one_sided_and_two_sided_differ() -> None:
    one = assess(_ev(n=36, misses=1, ci_method=CIMethod.CLOPPER_PEARSON_ONE_SIDED_95))
    two = assess(_ev(n=36, misses=1, ci_method=CIMethod.CLOPPER_PEARSON_TWO_SIDED_95))
    assert one.ci_upper is not None and two.ci_upper is not None
    assert two.ci_upper > one.ci_upper


def test_required_n_matches_measured_values() -> None:
    """2026-09-14 실측: 미탐 1건 · 단측 95% 로 ≤10% 를 주장하려면 46건."""
    assert required_n(0.10, 1, CIMethod.CLOPPER_PEARSON_ONE_SIDED_95) == 46
    assert required_n(0.10, 2, CIMethod.CLOPPER_PEARSON_ONE_SIDED_95) == 61
    assert required_n(0.05, 0, CIMethod.CLOPPER_PEARSON_ONE_SIDED_95) == 59


# ── headline 이 합격 문구를 못 만든다 ───────────────────────────────────

@pytest.mark.parametrize(
    "tier,overlap,target",
    list(itertools.product(
        [TruthTier.SILVER, TruthTier.BRONZE, TruthTier.NONE, TruthTier.CIRCULAR],
        [0, 5],
        [PMR002, UNRESOLVED],
    )),
)
def test_headline_never_says_pass_without_authority(tier, overlap, target) -> None:
    v = assess(_ev(truth_tier=tier, training_overlap_count=overlap, target=target))
    head = v.as_headline()
    assert v.status is not ClaimStatus.PASS
    assert "충족" not in head
    assert head.startswith("[")


def test_verdict_is_frozen() -> None:
    v = assess(_ev())
    with pytest.raises(Exception):
        v.status = ClaimStatus.PASS  # type: ignore[misc]


def test_misses_cannot_exceed_n() -> None:
    with pytest.raises(ValueError):
        _ev(n=10, misses=11)
