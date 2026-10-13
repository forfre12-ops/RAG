"""규정 조회 엔진 시험 — 결정형·순수 numpy. DB·임베더 없이 도는 시험."""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np
import pytest

from koipa.regulation import vectors
from koipa.regulation.index import (
    REASON_BELOW_FLOOR,
    REASON_NO_CLAUSES,
    REASON_NO_SENTENCE,
    ClauseRec,
    EvidenceItem,
    RegulationIndex,
    SentenceRec,
    evidence_item,
)
from koipa.regulation.lexical import LexicalIndex

DIM = 8


def unit(*hot: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    for h in hot:
        v[h] = 1.0
    return vectors.normalize(v)


def sent(seq, text, vec=None, lead=False, group=None):
    return SentenceRec(f"s{seq}-{text[:4]}", seq, text, lead, group, vec)


def clause(seq, no, vec, text, sentences, reg="규정A", ver="v1", rid="r1"):
    return ClauseRec(f"c{rid}{seq}", rid, reg, ver, seq, no, f"제목{seq}", text, vec, tuple(sentences))


# ── 벡터 직렬화 ────────────────────────────────────────────────────────────

def test_vector_roundtrip_and_errors():
    v = vectors.normalize(np.arange(1, 9, dtype=np.float32))
    assert np.allclose(vectors.decode(vectors.encode(v), 8), v)
    with pytest.raises(ValueError):
        vectors.decode(b"\x00\x01\x02")             # 4의 배수가 아님
    with pytest.raises(ValueError):
        vectors.decode(vectors.encode(v), 1024)     # 차원 불일치
    with pytest.raises(ValueError):
        vectors.decode(b"")
    assert float(np.linalg.norm(vectors.normalize(np.zeros(8)))) == 0.0    # 영벡터는 0 나눗셈 없이 그대로


# ── 낱말 채널: 역색인이 시험의 원래 계산과 같은 값을 준다 ─────────────────────────

def _reference_scores(docs, query):
    def bi(t):
        s = re.sub(r"\s+", "", t)
        return Counter(s[i:i + 2] for i in range(len(s) - 1))
    tfs = [bi(d) for d in docs]
    df = Counter()
    for tf in tfs:
        df.update(tf.keys())
    n = len(docs)
    idf = {g: math.log((n + 1) / (c + 1)) + 1 for g, c in df.items()}

    def w(tf):
        x = {g: (1 + math.log(c)) * idf[g] for g, c in tf.items() if g in idf}
        nm = math.sqrt(sum(v * v for v in x.values())) or 1.0
        return {g: v / nm for g, v in x.items()}
    vecs = [w(tf) for tf in tfs]
    q = w(bi(query))
    return np.array([sum(v * d.get(g, 0.0) for g, v in q.items()) for d in vecs], dtype=np.float32)


def test_lexical_index_matches_the_reference_implementation():
    docs = ["외부에 문서를 제공할 때는 등급에 따라 승인을 받는다", "시험 결과는 기밀로 취급한다", "열람 범위는 부서 한정이다",
            "소스코드 저장소는 프로젝트 단위로 권한을 부여한다"]
    for q in ["외부 제공 승인", "시험 성적서 기밀", "완전히 다른 낱말들 abc", ""]:
        assert np.allclose(LexicalIndex(docs).scores(q), _reference_scores(docs, q), atol=1e-6)


def test_lexical_empty_index_and_unknown_query():
    assert LexicalIndex([]).scores("아무거나").shape == (0,)
    assert float(LexicalIndex(["가나다라"]).scores("zzzz").sum()) == 0.0


# ── 조항 순위·문장 선택 ─────────────────────────────────────────────────────

def test_dense_similarity_picks_the_closest_clause_and_best_sentence():
    a = clause(0, "제1조", unit(0), "aa 조항", [sent(0, "가 문장은 후보가 아니다 충분히 길게", unit(5))])
    b = clause(1, "제2조", unit(1), "bb 조항", [sent(0, "첫째 문장이다 충분히 길게 쓴다", unit(2)),
                                                 sent(1, "둘째 문장이다 충분히 길게 쓴다", unit(1))])
    idx = RegulationIndex([a, b], "m")
    res = idx.find(unit(1), "zzz")
    assert [i.article_no for i in res.items] == ["제2조"]
    assert res.items[0].sentences == ("둘째 문장이다 충분히 길게 쓴다",)      # 코사인이 가장 큰 문장
    assert res.reason is None


def test_lexical_channel_can_change_the_winner():
    a = clause(0, "제1조", unit(0), "외부 제공 승인 절차 협력사", [sent(0, "외부 제공은 승인을 받는다 충분히", unit(0))])
    b = clause(1, "제2조", unit(0), "소스코드 저장소 권한 점검", [sent(0, "저장소 권한은 분기마다 점검한다", unit(0))])
    idx = RegulationIndex([a, b], "m")
    assert idx.find(unit(0), "협력사 외부 제공 승인").items[0].article_no == "제1조"
    assert idx.find(unit(0), "소스코드 저장소 권한").items[0].article_no == "제2조"


def test_ties_are_broken_by_clause_order_and_repeatable():
    def mk(seq):
        return clause(seq, f"제{seq}조", unit(0), "같은 조항", [sent(0, "똑같은 문장이다 길이도 충분히", unit(0))])
    idx = RegulationIndex([mk(3), mk(1), mk(2)], "m")
    first = idx.find(unit(0), "같은 조항", max_items=3)
    assert [i.article_no for i in first.items] == ["제1조", "제2조", "제3조"]
    for _ in range(5):
        assert idx.find(unit(0), "같은 조항", max_items=3) == first


def test_lead_sentences_are_never_selected():
    c = clause(0, "제1조", unit(0), "조항", [sent(0, "다음 각 호의 문서는 극비로 분류한다.", unit(0), lead=True),
                                            sent(1, "실제 내용을 담은 문장이다 길이 충분", unit(3))])
    res = RegulationIndex([c], "m").find(unit(0), "조항")
    assert res.items[0].sentences == ("실제 내용을 담은 문장이다 길이 충분",)


def test_grade_list_is_shown_whole_in_order():
    items = [sent(i, f"{i + 1}. 등급{i}: 규칙 {i} 을 적용한다 충분히", unit(i % 3), group=1) for i in range(4)]
    c = clause(0, "제34조", unit(0), "외부 제공", items + [sent(4, "② 별도 문장이다 충분히 길다", unit(4))])
    res = RegulationIndex([c], "m").find(unit(2), "외부")
    it = res.items[0]
    assert it.is_grade_list and len(it.sentences) == 4
    assert it.sentences == tuple(s.text for s in items)               # 한 등급 줄이 아니라 목록 전체·순서대로


def test_clause_without_candidate_sentences_is_skipped():
    empty = clause(0, "제1조", unit(0), "조항 하나", [sent(0, "다음 각 호와 같다 충분히 길다", unit(0), lead=True)])
    ok = clause(1, "제2조", unit(1), "조항 둘", [sent(0, "쓸 만한 문장이다 충분히 길다", unit(1))])
    res = RegulationIndex([empty, ok], "m").find(unit(0), "조항")
    assert [i.article_no for i in res.items] == ["제2조"]


def test_sentences_without_embeddings_are_not_candidates():
    c = clause(0, "제1조", unit(0), "조항", [sent(0, "아직 임베딩이 없는 문장이다 충분", None)])
    res = RegulationIndex([c], "m").find(unit(0), "조항")
    assert res.items == [] and res.reason == REASON_NO_SENTENCE


def test_floor_and_reasons():
    c = clause(0, "제1조", unit(0), "조항", [sent(0, "문장이다 충분히 길게 쓴다 여기", unit(0))])
    idx = RegulationIndex([c], "m")
    assert idx.find(unit(0), "조항", floor=0.5).reason is None
    below = idx.find(unit(7), "조항", floor=0.5)
    assert below.items == [] and below.reason == REASON_BELOW_FLOOR
    assert RegulationIndex([], "m").find(unit(0), "x").reason == REASON_NO_CLAUSES


def test_max_items_returns_distinct_clauses():
    cs = [clause(i, f"제{i}조", unit(i), f"조항{i}", [sent(0, f"문장 {i} 입니다 충분히 길게 씁니다", unit(i))]) for i in range(4)]
    res = RegulationIndex(cs, "m").find(unit(1, 2), "조항", max_items=2)
    assert len(res.items) == 2 and len({i.clause_id for i in res.items}) == 2


def test_union_of_two_regulations_keeps_the_regulation_name():
    a = clause(0, "제1조", unit(0), "외부 제공", [sent(0, "규정 A 의 문장이다 충분히 길게", unit(0))], reg="규정A", rid="a")
    b = clause(0, "제1조", unit(1), "개인정보", [sent(0, "규정 B 의 문장이다 충분히 길게", unit(1))], reg="규정B", rid="b")
    idx = RegulationIndex([b, a], "m")
    assert idx.find(unit(1), "개인정보").items[0].rgltn_nm == "규정B"
    assert idx.find(unit(0), "외부 제공").items[0].rgltn_nm == "규정A"


def test_evidence_item_carries_no_score():
    """점수를 응답에 실으면 신뢰도로 오용된다 — 값 객체에 그런 필드가 없어야 한다."""
    assert not any("score" in f or "similar" in f or "confidence" in f for f in EvidenceItem.__dataclass_fields__)


# ── 후보 조항만 세우기(로컬 LLM 판정 옵션의 입력) ────────────────────────────────

def test_rank_clauses_returns_candidates_in_the_same_order_find_would_pick():
    cs = [clause(i, f"제{i}조", unit(i), f"조항{i}", [sent(0, f"문장 {i} 입니다 충분히 길게 씁니다", unit(i))]) for i in range(5)]
    idx = RegulationIndex(cs, "m")
    ranked = idx.rank_clauses(unit(3), "조항", 3)
    found = idx.find(unit(3), "조항", max_items=3)
    assert [c.article_no for c in ranked] == [i.article_no for i in found.items]
    assert ranked[0].article_no == "제3조" and len(ranked) == 3


def test_rank_clauses_skips_clauses_without_selectable_sentences_and_honors_floor_and_k():
    empty = clause(0, "제1조", unit(0), "조항 하나", [sent(0, "다음 각 호와 같다 충분히 길다", unit(0), lead=True)])
    ok = clause(1, "제2조", unit(1), "조항 둘", [sent(0, "쓸 만한 문장이다 충분히 길다", None)])       # 문장 임베딩이 없어도 고를 문장이 있다(고르는 것은 LLM)
    other = clause(2, "제3조", unit(5), "조항 셋", [sent(0, "다른 문장이다 충분히 길다", unit(5))])
    idx = RegulationIndex([empty, ok, other], "m")
    assert [c.article_no for c in idx.rank_clauses(unit(1), "조항", 5)] == ["제2조", "제3조"]
    assert [c.article_no for c in idx.rank_clauses(unit(1), "조항", 1)] == ["제2조"]
    assert [c.article_no for c in idx.rank_clauses(unit(1), "조항", 5, floor=0.9)] == ["제2조"]        # 밀집 점수가 문턱 미만이면 뺀다
    assert RegulationIndex([], "m").rank_clauses(unit(0), "x", 3) == []


def test_evidence_item_shows_the_whole_list_for_a_grade_list_line_and_one_sentence_otherwise():
    items = [sent(i, f"{i + 1}. 등급{i}: 규칙 {i} 을 적용한다 충분히", unit(0), group=1) for i in range(3)]
    plain = sent(3, "② 별도 문장이다 충분히 길다", unit(0))
    c = clause(0, "제34조", unit(0), "외부 제공", items + [plain])
    one = evidence_item(c, plain)
    assert one.sentences == (plain.text,) and one.is_grade_list is False
    lst = evidence_item(c, items[1])
    assert lst.sentences == tuple(s.text for s in items) and lst.is_grade_list is True
