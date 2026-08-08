"""golden_tiers.tier_of 파생 + consumer 필터(eval=real_locked_eval, train=allowlist) 계약 테스트.

2026-07-22 하드닝: locked는 유효 서명 envelope(gate_version·signed_at·reviewer_ids)까지 요구하고,
real 평가정답 집계는 실문서 출처(document_origin)까지 요구한다. 무효 human_review는 SILVER(학습연료)가
아니라 TIER_HELD로 격리(역-누수 차단). train은 denylist(!=LOCKED)가 아니라 allowlist(TRAIN_TIERS).
"""
from lloydk.golden_tiers import (
    EXTERNAL_AUTHORITY_SOURCES,
    ORIGIN_CUSTOMER_REAL,
    ORIGIN_PUBLIC_REAL,
    ORIGIN_SYNTHETIC,
    ORIGIN_UNKNOWN,
    SYNTHETIC_PROXY_SOURCES,
    TIER_CANDIDATE,
    TIER_HELD,
    TIER_LEGAL_FLOOR,
    TIER_LOCKED,
    TIER_SILVER,
    document_origin,
    eval_readiness,
    eval_records,
    is_external_authority,
    is_human_reviewer,
    is_real_locked_eval,
    is_real_text_origin,
    is_valid_signoff,
    partition_by_tier,
    tier_of,
    train_records,
)


def _signed(label, reviewer="admin_kim", source="판례", **extra):
    """유효 서명 envelope + (기본) 실문서 출처를 갖춘 locked 레코드."""
    rec = {
        "label_source": "human_review",
        "reviewer_id": reviewer,
        "reviewer_ids": [reviewer],
        "gate_version": "human_signoff_v1",
        "signed_at": "2026-09-10T00:00:00+00:00",
        "source": source,
        "label": label,
    }
    rec.update(extra)
    return rec


# ── locked = 유효 서명 envelope (손편집·민팅·placeholder 차단) ─────────────────────


def test_locked_requires_valid_signoff_envelope():
    assert tier_of(_signed("S2")) == TIER_LOCKED


def test_envelope_less_human_review_is_held_not_silver():
    # gate_version/signed_at/reviewer_ids 없는 human_review(손편집·correction-import 민팅) → HELD
    assert tier_of({"label_source": "human_review", "reviewer_id": "admin_kim"}) == TIER_HELD
    # 부분 envelope도 무효 → HELD (SILVER 로 흘러들지 않음)
    assert tier_of(
        {"label_source": "human_review", "reviewer_id": "admin_kim", "gate_version": "human_signoff_v1"}
    ) == TIER_HELD


def test_valid_signoff_requires_full_bundle():
    base = _signed("S2")
    assert is_valid_signoff(base)
    assert not is_valid_signoff({**base, "gate_version": "bogus"})
    assert not is_valid_signoff({**base, "gate_version": None})
    assert not is_valid_signoff({**base, "signed_at": ""})
    assert not is_valid_signoff({**base, "reviewer_ids": []})
    assert not is_valid_signoff({**base, "reviewer_id": "ai_assist"})     # 머신
    assert not is_valid_signoff({**base, "label_source": "customer_review"})


def test_human_review_with_machine_reviewer_is_held():
    # 머신/플레이스홀더 reviewer → 서명 무효 → HELD(이전엔 SILVER로 새던 구멍).
    assert tier_of(_signed("S2", reviewer="ai_assist")) == TIER_HELD
    assert tier_of(_signed("S2", reviewer="")) == TIER_HELD


def test_common_model_reviewer_ids_are_not_human():
    for rid in ("claude-review", "gpt-4o", "qwen_judge", "gemini-pro", "bot1", "judge_local"):
        assert tier_of(_signed("S2", reviewer=rid)) == TIER_HELD


def test_placeholder_reviewers_blocked_real_names_pass():
    for rid in ("human", "reviewer", "tbd", "system", "unknown", "none", "ai_assist"):
        assert not is_human_reviewer(rid)
    for rid in ("admin_kim", "reviewer-1", "aiden_kim", "kim@corp.com", "홍길동"):
        assert is_human_reviewer(rid)


def test_legal_floor_sources():
    for s in ("public_definitive", "koipa_case_based", "nkt_designated", "codex_review",
              "curated_scenario"):
        assert tier_of({"label_source": s}) == TIER_LEGAL_FLOOR


# ── document_origin (본문 실재성 축, 라벨 권위와 직교) ───────────────────────────────


def test_document_origin_mapping():
    assert document_origin({"source": "판례"}) == ORIGIN_PUBLIC_REAL
    assert document_origin({"source": "판례(3000+)"}) == ORIGIN_PUBLIC_REAL
    assert document_origin({"source": "판례_공개문서"}) == ORIGIN_PUBLIC_REAL
    assert document_origin({"source": "금융보고서"}) == ORIGIN_PUBLIC_REAL
    assert document_origin({"source": "public_scenario"}) == ORIGIN_SYNTHETIC
    assert document_origin({"source": "synthetic_grounded"}) == ORIGIN_SYNTHETIC
    assert document_origin({"source": "synthetic"}) == ORIGIN_SYNTHETIC
    assert document_origin({"source": "real_deidentified"}) == ORIGIN_CUSTOMER_REAL
    assert document_origin({"source": "wat"}) == ORIGIN_UNKNOWN
    assert document_origin({}) == ORIGIN_UNKNOWN  # fail-closed
    # 명시 document_origin 필드가 source 유도보다 우선.
    assert document_origin({"source": "public_scenario", "document_origin": "customer_real"}) == ORIGIN_CUSTOMER_REAL


def test_synthetic_signoff_is_locked_tier_but_not_real_eval():
    # 서명은 유효하나 본문이 합성 → is_real_locked_eval(실문서 축)은 False.
    # 단 평가정답 편입은 '사람 서명' 기준이므로 eval pool 에는 들어간다(2026-08-06 결정:
    # 발주처는 검수만 하고 실문서를 올리지 않는다 → 실문서 요구 시 TS 가 영원히 0).
    rec = _signed("TS", source="public_scenario")
    assert is_valid_signoff(rec) and tier_of(rec) == TIER_LOCKED
    assert not is_real_text_origin(rec) and not is_real_locked_eval(rec)
    ev, _ = eval_records([rec], allow_floor_fallback=False)
    assert ev == [rec]                    # 사람 서명 → eval pool 편입
    assert train_records([rec]) == []     # 평가정답이므로 학습에서는 제외(train-on-test 차단)


# ── 권위 분류(2026-07-03 감사) — 외부권위 vs 큐레이트 프록시 ─────────────────────


def test_authority_sets_disjoint_and_koipa_demoted():
    assert "koipa_case_based" in SYNTHETIC_PROXY_SOURCES
    assert "koipa_case_based" not in EXTERNAL_AUTHORITY_SOURCES
    assert not (EXTERNAL_AUTHORITY_SOURCES & SYNTHETIC_PROXY_SOURCES)


def test_is_external_authority_record_level():
    assert is_external_authority({"label_source": "public_definitive"})
    assert is_external_authority(
        {"label_source": "nkt_designated", "legal_reference": "산업기술보호법 §9 고시 제2023-209호"}
    )
    assert not is_external_authority({"label_source": "nkt_designated"})
    assert not is_external_authority({"label_source": "nkt_designated", "legal_reference": "  "})
    assert not is_external_authority(
        {"label_source": "nkt_designated",
         "legal_reference": "산업기술보호법 §9 고시 제2023-209호",
         "source": "public_scenario"}
    )
    assert is_external_authority(
        {"label_source": "nkt_designated",
         "legal_reference": "산업기술보호법 §9 고시 제2023-209호",
         "source": "patent_publication"}
    )
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
        _signed("S2"),                                                             # LOCKED(real)
        {"label_source": "nkt_designated"},                                        # LEGAL_FLOOR
        {"label_source": "rule_llm_agreement", "review_status": "gold_candidate"},  # CANDIDATE
        {"label_source": "llm_judge_primary"},                                     # SILVER
        {"label_source": "human_review", "reviewer_id": "r1"},                     # HELD(envelope無)
    ]
    p = partition_by_tier(recs)
    assert len(p[TIER_LOCKED]) == 1
    assert len(p[TIER_LEGAL_FLOOR]) == 1
    assert len(p[TIER_CANDIDATE]) == 1
    assert len(p[TIER_SILVER]) == 1
    assert len(p[TIER_HELD]) == 1
    # train = silver+candidate+legal_floor = 3 (실문서 서명분·held 제외)
    assert len(train_records(recs)) == 3
    # eval = real_locked_eval(서명유효+실문서)만 = 1
    ev, used = eval_records(recs)
    assert used == TIER_LOCKED and len(ev) == 1


def test_held_excluded_from_train_and_eval():
    held = {"label_source": "human_review", "reviewer_id": "admin_kim"}  # envelope 無 → HELD
    assert tier_of(held) == TIER_HELD
    assert train_records([held]) == []
    ev, _ = eval_records([held], allow_floor_fallback=False)
    assert ev == []


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
    return _signed(label)


def test_eval_readiness_empty_locked_not_ready():
    recs = [{"label_source": "nkt_designated", "label": "TS"}]
    r = eval_readiness(recs, min_per_grade=2)
    assert r["ready"] is False
    assert r["reason"] == "no_locked_records"


def test_eval_readiness_counts_signed_synthetic_and_reports_composition():
    # [2026-08-06] 사람 서명이 곧 평가정답 편입 조건. 합성 본문도 서명되면 집계된다
    # (발주처는 검수만 하고 실문서를 올리지 않는 운영 전제 — 실문서 요구 시 TS 가 영원히 0).
    # 대신 구성(실문서/합성)을 함께 보고해 수치 인용 시 한정할 수 있게 한다.
    recs = [_signed(g, source="public_scenario") for g in ("TS", "S1", "S2", "S3")]
    r = eval_readiness(recs, min_per_grade=1)
    assert r["ready"] is True
    assert r["per_grade"] == {"TS": 1, "S1": 1, "S2": 1, "S3": 1}
    assert r["synthetic_per_grade"] == {"TS": 1, "S1": 1, "S2": 1, "S3": 1}
    assert r["real_total"] == 0 and r["synthetic_total"] == 4


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
