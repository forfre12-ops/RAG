"""koipa.golden_builder.promote_candidates 게이트 승격 단위 테스트 (G4).

빌더 후보를 기존 gold에 병합할 때 중복·누출·비-gold를 게이트로 차단하는지 검증.
정본 파일은 건드리지 않고 (병합 리스트, 통계)만 반환한다.
"""
from koipa.golden_builder import promote_candidates


def test_promote_adds_new_and_keeps_existing():
    existing = [{"doc_id": "g1", "text": "기존 골드", "label": "S1"}]
    cands = [{"doc_id": "c1", "text": "새 후보", "label": "S2"}]
    merged, r = promote_candidates(cands, existing)
    assert r.added == 1 and r.gold_total == 2
    assert {x["doc_id"] for x in merged} == {"g1", "c1"}


def test_promote_skips_duplicate_whitespace_variant():
    existing = [{"doc_id": "g1", "text": "중복 문서", "label": "S1"}]
    cands = [{"doc_id": "c1", "text": "중복문서", "label": "S1"}]  # 공백차이 → 중복
    merged, r = promote_candidates(cands, existing)
    assert r.added == 0 and r.skipped_duplicate == 1 and r.gold_total == 1


def test_promote_skips_holdout_leak():
    cands = [{"doc_id": "c1", "text": "누출 문서", "label": "S1"}]
    merged, r = promote_candidates(cands, [], holdout_texts=["누출문서"])
    assert r.added == 0 and r.skipped_leaked == 1


def test_promote_skips_not_gold():
    cands = [
        {"doc_id": "c1", "text": "무라벨", "label": None},
        {"doc_id": "c2", "text": "   ", "label": "S1"},
        {"doc_id": "c3", "text": "잘못된등급", "label": "X9"},
        {"doc_id": "c4", "text": "정상", "label": "S2"},
    ]
    merged, r = promote_candidates(cands, [])
    assert r.added == 1 and r.skipped_not_gold == 3
    assert merged[0]["doc_id"] == "c4"


def test_promote_dedup_within_candidates():
    cands = [
        {"doc_id": "c1", "text": "같은 후보", "label": "S1"},
        {"doc_id": "c2", "text": "같은후보", "label": "S1"},  # 공백차이 → 중복
    ]
    merged, r = promote_candidates(cands, [])
    assert r.added == 1 and r.skipped_duplicate == 1 and r.gold_total == 1
