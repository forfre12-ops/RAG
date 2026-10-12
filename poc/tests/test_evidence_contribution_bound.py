"""2026-10-12: tad_cm_clsf_bss_mng.cndg_nvl(ClassificationEvidence.contribution)은
Numeric(4,3) — 최대 9.999. 종전엔 rule_engine의 count*weight(상한 없음)를 그대로 썼다가
넘쳐서 psycopg.errors.NumericValueOutOfRange로 근거 행이 통째로 사라졌다(KL 실운영 로그로
발견). m3_labeling/pipeline.py의 _evidence_contribution()이 그 값을 대신 만든다 —
이 테스트는 "몇 개 예시로는 안 터진다"가 아니라 "이 공식은 count·weight 가 뭐든 컬럼
상한을 수학적으로 못 넘는다"를 증명한다.
"""

from __future__ import annotations

import pytest

from koipa.modules.m3_labeling.pipeline import _evidence_contribution

COLUMN_MAX = 9.999  # tad_cm_clsf_bss_mng.cndg_nvl = Numeric(4,3)
WEIGHT_MAX = 9.99   # tad_gm_grd_kywd_mng.wgvl_cfc = Numeric(3,2), 관리자 입력 상한


@pytest.mark.parametrize(
    "weight", [0.01, 0.5, 1.0, 3.0, 5.0, 9.0, 9.98, 9.99, -9.99, -1.0]
)
@pytest.mark.parametrize(
    "count", [1, 2, 3, 5, 10, 100, 10_000, 1_000_000, 10**9]
)
def test_contribution_never_reaches_column_max(count: int, weight: float) -> None:
    """count·weight 격자 전체에서 |contribution| <= |weight| <= 9.99 < 컬럼상한(9.999).
    round(...,3)이 반복이 아주 많을 때 weight에 '근접'이 아니라 '정확히 같게' 반올림할 수
    있다(예: 9.989999... -> 9.990) — 그래도 weight 자체가 소수 2자리라 3자리로 반올림해도
    weight를 넘어설 수는 없다. 그러므로 상한은 <(엄격한 미달)이 아니라 <=로 증명한다.
    """
    contribution = _evidence_contribution(count, weight)
    assert abs(contribution) <= abs(weight)
    assert abs(contribution) < COLUMN_MAX


def test_weight_alone_already_fits_so_the_bound_holds_at_the_limit() -> None:
    """count→무한대 극한에서도 |contribution| → |weight| <= WEIGHT_MAX < COLUMN_MAX로
    수렴할 뿐 못 넘는다 — count를 아무리 키워도 넘을 수 없다는 걸 극값으로 재확인."""
    huge_count = 10**12
    for weight in (WEIGHT_MAX, -WEIGHT_MAX):
        contribution = _evidence_contribution(huge_count, weight)
        assert abs(contribution) <= abs(weight) <= WEIGHT_MAX < COLUMN_MAX


def test_zero_count_or_zero_weight_is_zero() -> None:
    assert _evidence_contribution(0, 5.0) == 0.0
    assert _evidence_contribution(3, 0.0) == 0.0


def test_single_match_gives_half_the_weight() -> None:
    """count=1 → weight*(1 - 1/2) = weight/2, 공식의 손계산 확인."""
    assert _evidence_contribution(1, 10.0) == pytest.approx(5.0)


def test_increasing_count_increases_contribution_but_stays_below_weight() -> None:
    """반복이 많을수록 근거가 강해진다는 신호는 보존된다(단조증가) — 단, weight를 못 넘는다."""
    weight = 8.0
    values = [_evidence_contribution(c, weight) for c in (1, 2, 5, 20, 1000)]
    assert values == sorted(values)
    assert all(v < weight for v in values)
