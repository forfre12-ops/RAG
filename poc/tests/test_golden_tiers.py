"""golden_tiers.tier_of 파생 + consumer 필터(eval=locked/floor, train≠locked) 계약 테스트."""
from lloydk.golden_tiers import (
    EXTERNAL_AUTHORITY_SOURCES,
    SYNTHETIC_PROXY_SOURCES,
    TIER_CANDIDATE,
    TIER_LEGAL_FLOOR,
    TIER_LOCKED,
    TIER_SILVER,
    eval_readiness,
    eval_records,
    is_external_authority,
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


def test_common_model_reviewer_ids_are_not_human():
    for reviewer_id in ("claude-review", "gpt-4o", "qwen_judge", "gemini-pro", "bot1", "judge_local"):
        assert tier_of({"label_source": "human_review", "reviewer_id": reviewer_id}) == TIER_SILVER


def test_legal_floor_sources():
    for s in ("public_definitive", "koipa_case_based", "nkt_designated", "codex_review",
              "curated_scenario"):
        assert tier_of({"label_source": s}) == TIER_LEGAL_FLOOR


# ── 권위 분류(2026-07-03 감사) — 외부권위 vs 큐레이트 프록시 ─────────────────────


def test_authority_sets_disjoint_and_koipa_demoted():
    # koipa 판례 인용 조작 확인 → 어떤 경로에서도 외부권위 아님
    assert "koipa_case_based" in SYNTHETIC_PROXY_SOURCES
    assert "koipa_case_based" not in EXTERNAL_AUTHORITY_SOURCES
    assert not (EXTERNAL_AUTHORITY_SOURCES & SYNTHETIC_PROXY_SOURCES)


def test_is_external_authority_record_level():
    # public 판례 = 외부권위 (record 조건 없음)
    assert is_external_authority({"label_source": "public_definitive"})
    # nkt는 지정근거(legal_reference)가 있어야 성립 — 위조 provenance 차단
    assert is_external_authority(
        {"label_source": "nkt_designated", "legal_reference": "산업기술보호법 §9 고시 제2023-209호"}
    )
    assert not is_external_authority({"label_source": "nkt_designated"})
    assert not is_external_authority({"label_source": "nkt_designated", "legal_reference": "  "})
    # 라벨 권위(고시 지정근거)가 정당해도 본문이 손작성 합성(source=public_scenario)이면 아님 —
    # real-text 실측 인용 불가(2026-07-04 텍스처 갭 감사). label floor 역할은 tier_of가 유지.
    assert not is_external_authority(
        {"label_source": "nkt_designated",
         "legal_reference": "산업기술보호법 §9 고시 제2023-209호",
         "source": "public_scenario"}
    )
    # 반대로 실텍스트(공개 판례/공보 등 non-scenario source)는 그대로 external
    assert is_external_authority(
        {"label_source": "nkt_designated",
         "legal_reference": "산업기술보호법 §9 고시 제2023-209호",
         "source": "patent_publication"}
    )
    # 큐레이트 프록시는 항상 아님
    assert not is_external_authority({"label_source": "koipa_case_based"})
    assert not is_external_authority({"label_source": "curated_scenario"})
    assert not is_external_authority({"label_source": "codex_review"})


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


# ── eval_readiness (죽음의 나선 #4 — 자동활성 가드 근거) ──────────────────────


def _locked(label):
    return {"label_source": "human_review", "reviewer_id": "admin_kim", "label": label}


def test_eval_readiness_empty_locked_not_ready():
    # locked 없음(무실데이터: legal_floor만 존재) → ready=False → 자동활성 차단 근거
    recs = [{"label_source": "nkt_designated", "label": "TS"}]
    r = eval_readiness(recs, min_per_grade=2)
    assert r["ready"] is False
    assert r["reason"] == "no_locked_records"


def test_eval_readiness_insufficient_per_grade():
    recs = [_locked("TS"), _locked("S1"), _locked("S2"), _locked("S3")]  # 등급별 1건
    r = eval_readiness(recs, min_per_grade=2)
    assert r["ready"] is False
    assert set(r["missing"]) == {"TS", "S1", "S2", "S3"}


def test_eval_readiness_ready_when_min_met():
    recs = [_locked(g) for g in ("TS", "S1", "S2", "S3") for _ in range(2)]  # 등급별 2건
    r = eval_readiness(recs, min_per_grade=2)
    assert r["ready"] is True
    assert r["missing"] == []
    assert r["per_grade"] == {"TS": 2, "S1": 2, "S2": 2, "S3": 2}
