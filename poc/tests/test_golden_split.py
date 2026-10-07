"""golden_split — 누출 가드 + 등급층화 75/25 결정적 분할 + split_role 스탬프 테스트."""
from __future__ import annotations

from koipa.golden_split import (
    HOLDOUT,
    SILVER,
    split_for_training,
)


def _recs(grade, n, prefix=""):
    # 본문이 서로 다르게(중복 아님) — text_hash가 갈리도록.
    return [{"doc_id": f"{prefix}{grade}-{i}", "label": grade,
             "text": f"{prefix}{grade} 합성 문서 본문 고유 {i} " * 5,
             "label_source": "rule_llm_agreement", "review_status": "gold_candidate"}
            for i in range(n)]


def test_leak_drop_against_existing_corpus():
    recs = _recs("S2", 4)
    # 기존 코퍼스에 recs[0]과 동일 본문 → 누출 드롭
    existing = [recs[0]["text"]]
    res = split_for_training(recs, existing_texts=existing)
    assert res.dropped_leaked == 1
    assert res.stats["kept"] == 3


def test_within_run_dedup():
    recs = _recs("S2", 3)
    recs.append({**recs[0], "doc_id": "dup"})  # 본문 중복(doc_id만 다름)
    res = split_for_training(recs)
    assert res.dropped_duplicate == 1


def test_stratified_75_25_per_grade():
    recs = _recs("S2", 100) + _recs("S3", 40)
    res = split_for_training(recs, holdout_frac=0.25)
    pg = res.stats["per_grade"]
    assert pg["S2"]["holdout"] == 25 and pg["S2"]["silver"] == 75
    assert pg["S3"]["holdout"] == 10 and pg["S3"]["silver"] == 30


def test_no_text_overlap_between_silver_and_holdout():
    recs = _recs("S2", 40)
    res = split_for_training(recs)
    s_ids = {r["doc_id"] for r in res.silver}
    h_ids = {r["doc_id"] for r in res.holdout}
    assert s_ids.isdisjoint(h_ids)
    assert len(s_ids) + len(h_ids) == 40


def test_deterministic_split():
    recs = _recs("S1", 20)
    a = split_for_training(recs)
    b = split_for_training(list(reversed(recs)))  # 입력 순서 달라도
    assert {r["doc_id"] for r in a.holdout} == {r["doc_id"] for r in b.holdout}


def test_tiny_stratum_no_holdout():
    res = split_for_training(_recs("TS", 1))
    assert res.stats["per_grade"]["TS"]["holdout"] == 0  # 단일표본 분할 불가
    res2 = split_for_training(_recs("TS", 3))
    assert res2.stats["per_grade"]["TS"]["holdout"] == 1


def test_zero_holdout_fraction_keeps_everything_in_training():
    res = split_for_training(_recs("S2", 5), holdout_frac=0)
    assert len(res.silver) == 5 and not res.holdout


def test_split_role_stamps_and_holdout_note():
    res = split_for_training(_recs("S2", 8))
    assert all(r["split_role"] == HOLDOUT and "일반화 아님" in r["eval_note"] for r in res.holdout)
    assert all(r["split_role"] == SILVER for r in res.silver)


def test_label_source_untouched_for_golden_tiers_compat():
    # split_role은 golden_tiers truth-tier와 직교 — label_source/review_status를 안 덮음
    res = split_for_training(_recs("S2", 8))
    for r in res.silver + res.holdout:
        assert r["label_source"] == "rule_llm_agreement"
        assert r["review_status"] == "gold_candidate"


def test_high_grade_holdout_signal():
    recs = _recs("TS", 4) + _recs("S2", 40)
    res = split_for_training(recs)
    # 고등급 holdout 수치가 stats에 노출(앵커 의존 신호)
    assert "high_grade_holdout" in res.stats
    assert res.stats["high_grade_holdout"] == res.stats["per_grade"]["TS"]["holdout"]


def test_document_family_never_crosses_train_and_holdout():
    recs = _recs("S1", 8)
    for i, rec in enumerate(recs):
        rec["document_family_id"] = f"template-{i // 2}"

    res = split_for_training(recs, holdout_frac=0.25)
    train_families = {r["document_family_id"] for r in res.silver}
    holdout_families = {r["document_family_id"] for r in res.holdout}
    assert train_families.isdisjoint(holdout_families)
    assert res.stats["family_overlap"] == 0


def test_single_family_is_reported_not_split_for_holdout():
    recs = _recs("TS", 4)
    for rec in recs:
        rec["document_family_id"] = "one-scenario"

    res = split_for_training(recs)
    assert not res.holdout
    assert res.stats["unsplittable_families"] == {"TS": 4}


def test_existing_eval_family_is_dropped_from_training_candidates():
    recs = _recs("S1", 4)
    recs[0]["document_family_id"] = "eval-family"
    recs[1]["document_family_id"] = "train-family-a"
    recs[2]["document_family_id"] = "train-family-b"
    recs[3]["document_family_id"] = "train-family-c"

    res = split_for_training(recs, existing_family_ids=["eval-family"])
    kept_ids = {row["doc_id"] for row in res.silver + res.holdout}
    assert recs[0]["doc_id"] not in kept_ids
    assert res.stats["dropped_family_leaked"] == 1


def test_matched_family_spanning_grades_is_globally_atomic():
    recs = _recs("TS", 4, "ts-") + _recs("S1", 4, "s1-")
    for index, record in enumerate(recs):
        record["document_family_id"] = f"family-{index % 4}"
    result = split_for_training(recs, holdout_frac=0.25)
    train_families = {row["document_family_id"] for row in result.silver}
    holdout_families = {row["document_family_id"] for row in result.holdout}
    assert train_families.isdisjoint(holdout_families)
    assert result.stats["family_overlap"] == 0


def test_each_scenario_stratum_gets_holdout_coverage():
    recs = _recs("TS", 20)
    for index, record in enumerate(recs):
        scenario = f"scenario-{index // 10}"
        record["scenario_id"] = scenario
        record["document_family_id"] = f"{scenario}:family-{index % 10}"
    result = split_for_training(recs, holdout_frac=0.2)
    assert result.stats["per_stratum"]["TS|scenario-0"]["holdout"] == 2
    assert result.stats["per_stratum"]["TS|scenario-1"]["holdout"] == 2
