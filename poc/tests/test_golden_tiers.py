"""golden_tiers.tier_of 파생 + consumer 필터(eval=locked/floor, train≠locked) 계약 테스트."""
from lloydk.golden_tiers import (
    TIER_CANDIDATE,
    TIER_LEGAL_FLOOR,
    TIER_LOCKED,
    TIER_SILVER,
    eval_records,
    partition_by_tier,
    tier_of,
    train_records,
)


def test_locked_requires_human_signoff():
    assert tier_of({"label_source": "human_review", "reviewer_id": "admin_kim"}) == TIER_LOCKED


def test_human_review_with_machine_reviewer_not_locked():
    # 머신/플레이스홀더 reviewer는 locked 불가 → 평가 정답에서 배제(silver).
    assert tier_of({"label_source": "human_review", "reviewer_id": "ai_assist"}) == TIER_SILVER
    assert tier_of({"label_source": "human_review", "reviewer_id": ""}) == TIER_SILVER


def test_legal_floor_sources():
    for s in ("public_definitive", "koipa_case_based", "nkt_designated", "codex_review"):
        assert tier_of({"label_source": s}) == TIER_LEGAL_FLOOR


def test_gold_candidate_from_new_gate():
    assert tier_of({"label_source": "rule_llm_agreement", "review_status": "gold_candidate"}) == TIER_CANDIDATE
    assert tier_of({"label_source": "x", "review_status": "gold_candidate"}) == TIER_CANDIDATE


def test_silver_for_llm_judge_and_needs_review():
    assert tier_of({"label_source": "llm_judge_primary", "review_status": "accepted"}) == TIER_SILVER
    assert tier_of({"label_source": "needs_review", "review_status": "needs_review"}) == TIER_SILVER


def test_partition_and_filters():
    recs = [
        {"label_source": "human_review", "reviewer_id": "admin_kim"},
        {"label_source": "nkt_designated"},
        {"label_source": "rule_llm_agreement", "review_status": "gold_candidate"},
        {"label_source": "llm_judge_primary"},
    ]
    p = partition_by_tier(recs)
    assert len(p[TIER_LOCKED]) == 1
    assert len(p[TIER_LEGAL_FLOOR]) == 1
    assert len(p[TIER_CANDIDATE]) == 1
    assert len(p[TIER_SILVER]) == 1
    # train = locked 제외 전부(silver+candidate+legal_floor) = 3
    assert len(train_records(recs)) == 3
    # eval = locked 존재 → locked만
    ev, used = eval_records(recs)
    assert used == TIER_LOCKED and len(ev) == 1


def test_eval_floor_fallback_when_no_locked():
    recs = [{"label_source": "nkt_designated"}, {"label_source": "llm_judge_primary"}]
    ev, used = eval_records(recs)
    assert used == TIER_LEGAL_FLOOR and len(ev) == 1  # locked 없음 → floor interim


def test_eval_no_fallback_returns_empty():
    recs = [{"label_source": "nkt_designated"}]
    ev, used = eval_records(recs, allow_floor_fallback=False)
    assert used == TIER_LOCKED and ev == []
