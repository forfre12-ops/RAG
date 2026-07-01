"""[P0#2] 도메인→등급 누출 산출 게이트(domain_leakage_verdict) 단위 테스트.

누출 지표(trivial_domain_baseline·theil_u)를 "분석만" 하던 것을 hard 게이트로 승격한다.
순수 판정 함수만 검증 — check_domain_leakage_gate.main()은 analyze_golden_run.domain_leakage를
**런타임 import**(main 내부)하므로 이 테스트 collect는 (동시 편집 중인) 그 스크립트에 의존하지 않는다.

실측 앵커: 누출 심함=baseline 0.856·theil_u 0.808(학습 보류), 건전=baseline 0.411·theil_u 0.072.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from check_domain_leakage_gate import domain_leakage_verdict  # noqa: E402


def _leak(baseline, theil_u, n=200):
    return {"n": n, "trivial_domain_baseline": baseline, "theil_u": theil_u}


def test_blocks_leaky_generator() -> None:
    # 등급-잠금 도메인풀(누출 심함) → 두 지표 모두 초과 → 차단.
    v = domain_leakage_verdict(_leak(0.856, 0.808))
    assert v["blocked"] is True
    assert "trivial_domain_baseline" in v["reason"]
    assert "theil_u" in v["reason"]


def test_passes_healthy_cross_design() -> None:
    # 교차 재설계(SPAN_DOMAINS) → 둘 다 임계 이하 → 통과.
    v = domain_leakage_verdict(_leak(0.411, 0.072))
    assert v["blocked"] is False
    assert v["reason"] == "ok"


def test_blocks_on_baseline_only() -> None:
    v = domain_leakage_verdict(_leak(0.70, 0.10))
    assert v["blocked"] is True
    assert "trivial_domain_baseline" in v["reason"]
    assert "theil_u" not in v["reason"]


def test_blocks_on_theil_only() -> None:
    v = domain_leakage_verdict(_leak(0.30, 0.50))
    assert v["blocked"] is True
    assert "theil_u" in v["reason"]
    assert "trivial_domain_baseline" not in v["reason"]


def test_no_data_is_not_blocked() -> None:
    # 표본 없음=측정 불가는 차단 아님(fail-open) — 단 사유는 노출(무음 금지).
    assert domain_leakage_verdict({})["reason"] == "no_data"
    assert domain_leakage_verdict({})["blocked"] is False
    assert domain_leakage_verdict({"n": 0, "trivial_domain_baseline": 1.0})["blocked"] is False


def test_custom_thresholds_respected() -> None:
    # 엄격 임계(baseline 0.3)면 건전값(0.411)도 차단 — 임계가 판정을 결정함을 확인.
    v = domain_leakage_verdict(_leak(0.411, 0.072), max_baseline=0.30, max_theil_u=0.35)
    assert v["blocked"] is True
    assert v["max_baseline"] == 0.30


# ── 인라인 빌드타임 게이트(build_synthetic_golden.compute_leakage_verdict) ──────────
# 독립 스크립트(post-build)를 넘어, 빌드가 산출 즉시 gold(=학습 silver)의 누출을 판정해
# stats.json에 남기고 임계 초과 시 exit 2로 차단하도록 배선한 것을 검증(raw 레코드→verdict).
from build_synthetic_golden import compute_leakage_verdict  # noqa: E402


def test_inline_gate_blocks_domain_locked_gold() -> None:
    # 도메인이 등급을 결정(도메인-잠금) → trivial baseline·theil_u 1.0 → 차단.
    leaked = [{"domain": "tech", "label": "TS"}] * 4 + [{"domain": "public", "label": "S3"}] * 4
    v = compute_leakage_verdict(leaked)
    assert v["blocked"] is True and v["leakage"]["n"] == 8


def test_inline_gate_passes_cross_domain_gold() -> None:
    # 각 도메인이 모든 등급에 고르게 → 도메인이 등급을 못 결정 → 통과.
    healthy = [{"domain": d, "label": g}
               for d in ("finance", "tech", "business")
               for g in ("TS", "S1", "S2", "S3")]
    v = compute_leakage_verdict(healthy)
    assert v["blocked"] is False and v["reason"] == "ok"


def test_inline_gate_empty_is_fail_open() -> None:
    # 산출 0건(생성 실패 등)은 no_data(fail-open) — 데이터 부재를 차단으로 오인하지 않는다.
    v = compute_leakage_verdict([])
    assert v["blocked"] is False and v["reason"] == "no_data"


def test_inline_gate_custom_thresholds() -> None:
    # 엄격 임계면 경계 누출도 차단(운영 튜닝 가능).
    rows = [{"domain": "a", "label": "TS"}, {"domain": "a", "label": "S1"},
            {"domain": "b", "label": "TS"}, {"domain": "b", "label": "S1"}]
    strict = compute_leakage_verdict(rows, max_baseline=0.1, max_theil_u=0.01)
    assert strict["blocked"] is True
