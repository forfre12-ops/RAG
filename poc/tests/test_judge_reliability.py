"""judge_reliability (B) — 출처별 층화 심판 신뢰도·proxy 양가 플래그·라벨 미수정 테스트.

회귀 락: 실 rejudged.jsonl(591건)이 있으면 'Claude 고등급 0건'을 CI에 고정한다.
"""
from __future__ import annotations

import copy
from pathlib import Path

from koipa.modules.m6_evaluation.judge_reliability import (
    FLAG_HIGH_DISAGREEMENT,
    FLAG_PROXY_CAVEAT,
    FLAG_SYSTEMATIC_DOWNGRADE,
    build_judge_reliability,
    load_rejudge_jsonl,
)

REJUDGE_PATH = (
    Path(__file__).resolve().parents[1]
    / "datasets" / "gold_real" / "_rejudge_claude" / "rejudged.jsonl"
)


def _rec(qwen, claude, source="기타", domain="기타"):
    return {"qwen_grade": qwen, "claude_grade": claude, "source": source, "domain": domain}


def test_proxy_downgrade_flags():
    # 판례 출처: 6건 하향(S2→S3), 4건 동의 → downgrade 0.6, proxy 양가
    recs = [_rec("S2", "S3", source="판례") for _ in range(6)]
    recs += [_rec("S3", "S3", source="판례") for _ in range(4)]
    rep = build_judge_reliability(recs)
    proxy = [s for s in rep.by_source if s.key == "source=판례"][0]
    assert proxy.downgrade_rate == 0.6
    assert FLAG_SYSTEMATIC_DOWNGRADE in proxy.flags
    assert FLAG_PROXY_CAVEAT in proxy.flags  # 하향이 정정일 수도 — 편향 단정 금지


def test_high_disagreement_flag():
    recs = [_rec("S1", "S3", source="내부") for _ in range(7)]
    recs += [_rec("S1", "S1", source="내부") for _ in range(3)]
    rep = build_judge_reliability(recs)
    s = [x for x in rep.by_source if x.key == "source=내부"][0]
    assert FLAG_HIGH_DISAGREEMENT in s.flags
    assert s.agreement_rate == 0.3


def test_does_not_modify_labels():
    recs = [_rec("S2", "S3", source="판례"), _rec("S1", "S1")]
    snapshot = copy.deepcopy(recs)
    build_judge_reliability(recs)
    assert recs == snapshot  # 입력 불변 — 폭로만, 보정 아님


def test_agreement_ci_is_valid_interval():
    rep = build_judge_reliability([_rec("S1", "S1"), _rec("S2", "S3")])
    lo, hi = rep.overall.agreement_ci
    assert 0.0 <= lo <= hi <= 1.0


def test_overall_distributions_present():
    rep = build_judge_reliability([_rec("S1", "S3"), _rec("S2", "S2")])
    assert rep.overall.a_dist == {"S1": 1, "S2": 1}
    assert rep.overall.b_dist == {"S3": 1, "S2": 1}


def test_real_591_claude_has_zero_high_grade():
    """실 데이터 회귀 락: Claude 재판정엔 고등급(TS/S1)이 0건이어야 함(summary.json 정합).

    이 사실이 무너지면(=Claude가 고등급을 내기 시작) '단일 LLM으로 고등급 정답 못 만듦'
    전제가 바뀐 것이므로 평가 설계를 재검토해야 한다 → 테스트로 고정.
    """
    rows = load_rejudge_jsonl(REJUDGE_PATH)
    if not rows:
        import pytest

        pytest.skip("rejudged.jsonl 없음 — 실데이터 회귀 락 생략")
    rep = build_judge_reliability(rows)
    b = rep.overall.b_dist
    assert b.get("TS", 0) == 0          # 핵심: Claude 고등급 0건
    assert b.get("S1", 0) == 0
    assert rep.overall.n >= 580         # 591건 근방
    # 비대칭 하향(Claude가 S3로 몰아넣음): 하향 ≫ 상향. summary.json: 하향 208 vs 상향 1.
    assert rep.overall.downgrade_rate > 0.30
    assert rep.overall.upgrade_rate < 0.02
    # proxy(공개·판례)성 출처 어딘가에 하향 양가 플래그가 떠야 함(편향 단정 금지)
    assert any(FLAG_PROXY_CAVEAT in s.flags for s in rep.by_source)
