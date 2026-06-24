"""통합 골든셋 빌더 코어 — 후보 문서를 〈위생→라벨→합의→조립〉으로 골든후보/검수대상 분류.

G1(consensus.evaluate_consensus) + G2(hygiene)를 부품으로 조립한 순수 코어.
라벨러를 주입(label_fn)받으므로 DB/Celery/LLM 없이 단위 테스트 가능하다.
서비스·Celery·route 계층(G3b)이 이 코어를 감싸 비동기/잡/검수큐로 노출한다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Sequence

from lloydk.hygiene import text_hash
from lloydk.modules.m3_labeling.consensus import evaluate_consensus


@dataclass
class LabelPair:
    """label_fn 반환형 — 룰/LLM 라벨 + 신뢰도."""

    rule_grade: str
    rule_conf: float
    llm_grade: str
    llm_conf: float


@dataclass
class GoldenRecord:
    """빌더 1건 결과 — gold(평가정본 후보) 또는 uncertain(검수대상)."""

    doc_id: str
    text: str
    label: str | None
    label_source: str
    review_status: str
    status: str
    rule_grade: str
    rule_confidence: float
    llm_grade: str
    llm_confidence: float
    agreement: bool
    source: str = ""
    domain: str = ""

    def to_dict(self) -> dict:
        """gold_real/classification_gold.jsonl 스키마와 정합되는 dict."""
        return {
            "doc_id": self.doc_id,
            "text": self.text,
            "label": self.label,
            "label_source": self.label_source,
            "source": self.source,
            "domain": self.domain,
            "review_status": self.review_status,
            "status": self.status,
            "rule_grade": self.rule_grade,
            "rule_confidence": self.rule_confidence,
            "llm_grade": self.llm_grade,
            "llm_confidence": self.llm_confidence,
            "agreement": self.agreement,
        }


@dataclass
class GoldenBuildResult:
    gold: list[GoldenRecord]
    uncertain: list[GoldenRecord]
    dropped_duplicate: int
    dropped_leaked: int
    stats: dict


def build_golden_set(
    docs: Sequence[dict],
    *,
    label_fn: Callable[[str], LabelPair],
    holdout_texts: Sequence[str] | None = None,
    min_rule_conf: float = 0.5,
    min_llm_conf: float = 0.7,
    text_key: str = "text",
    id_key: str = "doc_id",
) -> GoldenBuildResult:
    """후보 docs를 골든후보(gold)/검수대상(uncertain)으로 분류.

    단계:
      (1) 위생 — 정규화 text_hash로 본문 중복 제거 + holdout/train 누출 제거(G2)
      (2) 라벨 — label_fn(text)로 룰·LLM 라벨 획득(주입)
      (3) 합의 — evaluate_consensus로 gold/uncertain 판정(G1)
      (4) 조립 — GoldenRecord 버킷팅 + 통계

    빈 본문은 건너뛴다. 동일 본문(공백차이 무시)은 첫 건만 남기고 중복 카운트.
    holdout_texts와 본문해시가 겹치면 누출로 드롭(학습/평가 순환성 차단).
    """
    holdout_hashes = {text_hash(t) for t in (holdout_texts or [])}
    seen_hashes: set[str] = set()

    gold: list[GoldenRecord] = []
    uncertain: list[GoldenRecord] = []
    dropped_dup = dropped_leak = 0

    for doc in docs:
        text = (doc.get(text_key) or "").strip()
        if not text:
            continue
        h = text_hash(text)
        if h in seen_hashes:
            dropped_dup += 1
            continue
        if h in holdout_hashes:
            dropped_leak += 1
            continue
        seen_hashes.add(h)

        lp = label_fn(text)
        verdict = evaluate_consensus(
            lp.rule_grade, lp.rule_conf, lp.llm_grade, lp.llm_conf,
            min_rule_conf=min_rule_conf, min_llm_conf=min_llm_conf,
        )
        rec = GoldenRecord(
            doc_id=doc.get(id_key) or h[:16],
            text=text,
            label=lp.llm_grade if verdict.is_gold else None,
            label_source=verdict.label_source,
            review_status=verdict.review_status,
            status=verdict.status,
            rule_grade=lp.rule_grade,
            rule_confidence=round(lp.rule_conf, 4),
            llm_grade=lp.llm_grade,
            llm_confidence=round(lp.llm_conf, 4),
            agreement=verdict.agree,
            source=doc.get("source", ""),
            domain=doc.get("domain", ""),
        )
        (gold if verdict.is_gold else uncertain).append(rec)

    stats = {
        "input": len(docs),
        "gold": len(gold),
        "uncertain": len(uncertain),
        "dropped_duplicate": dropped_dup,
        "dropped_leaked": dropped_leak,
        "gold_by_grade": dict(Counter(r.label for r in gold)),
        "gold_by_status": dict(Counter(r.status for r in gold)),
    }
    return GoldenBuildResult(
        gold=gold,
        uncertain=uncertain,
        dropped_duplicate=dropped_dup,
        dropped_leaked=dropped_leak,
        stats=stats,
    )
