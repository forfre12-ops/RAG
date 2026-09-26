"""프로세스 내 정확 검색 + 문장 선택 + 목록 확장 (결정형, DB·임베더 없음).

■ 절차 (설계서 §2.5)
    1. 조항 점수  = 밀집(조항 벡터 · 문서 대표 벡터)과 낱말(글자 2-gram TF-IDF) 순위를 합산(RRF, k=60)
    2. 1위 조항에서 문장 1개 = 서두가 아닌 문장 중 문서 대표 벡터와 코사인이 가장 큰 것
    3. 그 문장이 등급별 목록의 한 줄이면 **목록 전체**를 보인다(한 등급 규칙만 보이면 그 등급으로 끈다)
    4. 후보 문장이 없는 조항은 건너뛰고 다음 순위로 간다

■ 결정형
    같은 입력이면 같은 출력이다. 동점은 (규정 순서, 조항 순번)이 앞선 쪽이 이긴다(안정 정렬).
    랜덤·시간·병렬 순서가 결과에 들어가지 않는다.

■ 이 모듈이 하지 않는 것
    점수를 돌려주지 않는다(신뢰도로 오용되는 것을 막는다). DB 접근·임베딩 호출을 하지 않는다.
    등급을 판정하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from koipa.regulation.lexical import LexicalIndex

RRF_K = 60

REASON_NO_CLAUSES = "no_active_regulation"
REASON_BELOW_FLOOR = "below_floor"
REASON_NO_SENTENCE = "no_sentence"


@dataclass(frozen=True)
class SentenceRec:
    sentence_id: str
    seq: int
    text: str
    is_lead: bool
    list_group: int | None
    vec: np.ndarray | None          # 정규화된 임베딩. None 이면 후보에서 뺀다(아직 임베딩 전)


@dataclass(frozen=True)
class ClauseRec:
    clause_id: str
    rgltn_id: str
    rgltn_nm: str
    ver_lbl_nm: str
    seq: int
    article_no: str
    title: str
    text: str                       # 낱말 색인에 넣는 글자(머리글 정보 포함)
    vec: np.ndarray                 # 정규화된 임베딩
    sentences: tuple[SentenceRec, ...] = ()


@dataclass(frozen=True)
class EvidenceItem:
    rgltn_id: str
    rgltn_nm: str
    ver_lbl_nm: str
    clause_id: str
    article_no: str
    title: str
    sentences: tuple[str, ...]
    is_grade_list: bool


@dataclass
class FindResult:
    items: list[EvidenceItem] = field(default_factory=list)
    reason: str | None = None


def _rank_positions(scores: np.ndarray) -> np.ndarray:
    """점수 내림차순 순위(0 부터). **동점은 같은 순위**를 준다.

    동점에 인위적인 순위 차이를 두면(예: 낱말 점수가 전부 0인데 첫 조항만 1위) 순위 합산에서 다른 채널의 실제
    차이를 상쇄해 버린다. 동점을 가르는 일은 최종 정렬(안정 정렬 = 앞 번호 우선)이 맡는다.
    """
    n = len(scores)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    order = np.argsort(-scores, kind="stable")
    sorted_scores = scores[order]
    change = np.concatenate(([True], sorted_scores[1:] != sorted_scores[:-1]))
    first = np.maximum.accumulate(np.where(change, np.arange(n), 0))
    ranks = np.empty(n, dtype=np.int64)
    ranks[order] = first
    return ranks


class RegulationIndex:
    """활성 규정의 조항 묶음 하나(임베딩 모델이 같은 것끼리)."""

    def __init__(self, clauses: list[ClauseRec], embed_model: str) -> None:
        self.embed_model = embed_model
        # 규정 → 조항 순번 순으로 고정한다(동점 정렬의 기준)
        self.clauses = sorted(clauses, key=lambda c: (c.rgltn_nm, c.ver_lbl_nm, c.rgltn_id, c.seq))
        self._matrix = (np.vstack([c.vec for c in self.clauses]).astype(np.float32)
                        if self.clauses else np.zeros((0, 0), dtype=np.float32))
        self._lexical = LexicalIndex([c.text for c in self.clauses])

    def __len__(self) -> int:
        return len(self.clauses)

    def _order(self, doc_vec: np.ndarray, doc_text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """조항을 순위합산(RRF) 순으로 세운다 → (순서, 조항별 밀집 점수, 문서 벡터)."""
        dv = np.asarray(doc_vec, dtype=np.float32)
        dense = self._matrix @ dv
        lex = self._lexical.scores(doc_text)
        rr_d, rr_l = _rank_positions(dense), _rank_positions(lex)
        total = 1.0 / (RRF_K + rr_d + 1) + 1.0 / (RRF_K + rr_l + 1)
        return np.argsort(-total, kind="stable"), dense, dv

    def rank_clauses(self, doc_vec: np.ndarray, doc_text: str, k: int, floor: float = 0.0) -> list[ClauseRec]:
        """조회 순위 순으로 후보 **조항** k 개 — 고를 문장이 있는 조항만. 문장은 고르지 않는다(후단이 고른다)."""
        if not self.clauses:
            return []
        order, dense, _ = self._order(doc_vec, doc_text)
        out: list[ClauseRec] = []
        for i in order:
            clause = self.clauses[int(i)]
            if floor > 0.0 and float(dense[int(i)]) < floor:
                continue
            if not any(not s.is_lead for s in clause.sentences):
                continue
            out.append(clause)
            if len(out) >= max(1, k):
                break
        return out

    def find(self, doc_vec: np.ndarray, doc_text: str, max_items: int = 1, floor: float = 0.0) -> FindResult:
        if not self.clauses:
            return FindResult([], REASON_NO_CLAUSES)
        order, dense, dv = self._order(doc_vec, doc_text)

        items: list[EvidenceItem] = []
        saw_below_floor = False
        for i in order:
            clause = self.clauses[int(i)]
            if floor > 0.0 and float(dense[int(i)]) < floor:
                saw_below_floor = True
                continue
            picked = self._pick(clause, dv)
            if picked is None:
                continue
            items.append(picked)
            if len(items) >= max(1, max_items):
                break
        if items:
            return FindResult(items, None)
        return FindResult([], REASON_BELOW_FLOOR if saw_below_floor else REASON_NO_SENTENCE)

    @staticmethod
    def _pick(clause: ClauseRec, dv: np.ndarray) -> EvidenceItem | None:
        cand = [s for s in clause.sentences if not s.is_lead and s.vec is not None]
        if not cand:
            return None
        sims = np.asarray([float(s.vec @ dv) for s in cand], dtype=np.float32)
        best = cand[int(np.argmax(sims))]        # 동점이면 앞 문장(argmax 는 첫 최대)
        return evidence_item(clause, best)


def evidence_item(clause: ClauseRec, best: SentenceRec) -> EvidenceItem:
    """고른 문장 → 화면 항목. 등급별 목록의 한 줄이면 **목록 전체**를 보인다(한 등급 규칙만 보이면 그 등급으로 끈다)."""
    if best.list_group is not None:
        group = [s for s in clause.sentences if s.list_group == best.list_group]
        shown, is_list = tuple(s.text for s in sorted(group, key=lambda s: s.seq)), True
    else:
        shown, is_list = (best.text,), False
    return EvidenceItem(clause.rgltn_id, clause.rgltn_nm, clause.ver_lbl_nm, clause.clause_id,
                        clause.article_no, clause.title, shown, is_list)
