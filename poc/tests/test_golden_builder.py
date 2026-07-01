"""lloydk.golden_builder.build_golden_set 코어 파이프라인 단위 테스트 (P1 v2 게이트).

라벨러를 주입(label_fn)해 DB/Celery/LLM 없이 위생(중복·누출)·합의·조립을 검증한다.
게이트: 합의(rule==llm) + 실제 근거(has_real_evidence) + self-consistency. gold_candidate만 자동 생성.
"""
from lloydk.golden_builder import LabelPair, build_golden_set

# 합의+근거 → gold_candidate / 불일치(근거 있음) → needs_review_disagree
_CONSENSUS = LabelPair("S1", 0.8, "S1", 0.9, has_real_evidence=True)
_DISAGREE = LabelPair("S2", 0.6, "S1", 0.9, has_real_evidence=True)


def test_gold_and_uncertain_bucketing():
    pairs = {"A 문서": _CONSENSUS, "B 문서": _DISAGREE}
    docs = [{"doc_id": "d1", "text": "A 문서"}, {"doc_id": "d2", "text": "B 문서"}]
    r = build_golden_set(docs, label_fn=lambda t: pairs[t])
    assert [x.doc_id for x in r.gold] == ["d1"]
    assert r.gold[0].label == "S1" and r.gold[0].label_source == "rule_llm_agreement"
    assert r.gold[0].status == "gold_candidate"
    assert [x.doc_id for x in r.uncertain] == ["d2"]
    assert r.uncertain[0].label is None and r.uncertain[0].status == "needs_review_disagree"


def test_dedup_drops_whitespace_variant():
    docs = [{"doc_id": "d1", "text": "같은 문서"}, {"doc_id": "d2", "text": "같은문서"}]
    r = build_golden_set(docs, label_fn=lambda t: _CONSENSUS)
    assert r.dropped_duplicate == 1
    assert len(r.gold) == 1 and r.gold[0].doc_id == "d1"


def test_leakage_drops_holdout_match():
    docs = [{"doc_id": "d1", "text": "누출 문서"}, {"doc_id": "d2", "text": "새 문서"}]
    # holdout "누출문서"는 d1("누출 문서")과 공백차이만 → 누출로 드롭
    r = build_golden_set(docs, label_fn=lambda t: _CONSENSUS, holdout_texts=["누출문서"])
    assert r.dropped_leaked == 1
    assert {x.doc_id for x in r.gold} == {"d2"}


def test_empty_text_skipped():
    docs = [{"doc_id": "d1", "text": "   "}, {"doc_id": "d2", "text": "실문서"}]
    r = build_golden_set(
        docs, label_fn=lambda t: LabelPair("S3", 0.0, "S3", 0.8, has_real_evidence=True),
    )
    assert r.stats["input"] == 2
    assert len(r.gold) + len(r.uncertain) == 1  # 빈 본문 1건 스킵
    assert r.gold[0].doc_id == "d2" and r.gold[0].status == "gold_candidate"


def test_no_evidence_routes_to_review():
    # 근거 없는 단일 LLM 동의(구 path B/C)는 더 이상 gold 아님 — needs_review_no_evidence.
    docs = [{"doc_id": "d1", "text": "근거없는 문서"}]
    r = build_golden_set(
        docs, label_fn=lambda t: LabelPair("S3", 0.0, "S3", 0.9, has_real_evidence=False),
    )
    assert len(r.gold) == 0 and len(r.uncertain) == 1
    assert r.uncertain[0].status == "needs_review_no_evidence"


def test_stats_and_to_dict():
    docs = [
        {"doc_id": "d1", "text": "판례 문서", "source": "판례", "domain": "legal"},
        {"doc_id": "d2", "text": "불일치 문서"},
    ]
    pairs = {"판례 문서": _CONSENSUS, "불일치 문서": _DISAGREE}
    r = build_golden_set(docs, label_fn=lambda t: pairs[t])
    assert r.stats["gold"] == 1 and r.stats["uncertain"] == 1
    assert r.stats["gold_by_grade"] == {"S1": 1}
    d = r.gold[0].to_dict()
    assert d["label"] == "S1" and d["source"] == "판례" and d["domain"] == "legal"
    assert d["label_source"] == "rule_llm_agreement" and d["review_status"] == "gold_candidate"
    assert d["gate_version"] == "v2_agreement_evidence"


def test_doc_id_falls_back_to_hash_prefix():
    docs = [{"text": "아이디 없는 문서"}]  # doc_id 누락
    r = build_golden_set(docs, label_fn=lambda t: _CONSENSUS)
    assert len(r.gold) == 1
    assert r.gold[0].doc_id and len(r.gold[0].doc_id) == 16  # 해시 16자 폴백
