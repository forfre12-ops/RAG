"""키워드 매칭 기반 1차 라벨링 + 4대 평가요소 점수화.

알고리즘:
  1. 텍스트에서 각 키워드의 빈도(또는 정규식 매칭 수) 측정
     - pattern_type=exact (기본): 단순 부분 문자열
     - pattern_type=regex      : re.findall
     - pattern_type=semantic   : 임베딩 코사인 유사도 ≥ threshold 시 1회 매칭
  2. 등급별 점수 = Σ (매칭수 × keyword.weight)
  3. 등급 = argmax (점수)
  4. 4대 평가요소 점수 = Σ (factor별 매칭 가중치) → 0~5 스케일로 normalize
  5. FNR 최소화 전략: 동점일 때 더 높은 등급(낮은 order)을 선택
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from lloydk.modules.m3_labeling.seeds import (
    FACTOR_SEEDS,
    GRADE_ORDER,
    KEYWORD_SEEDS,
)

logger = logging.getLogger(__name__)

# semantic 매칭 기본 코사인 임계값. EMB_SEMANTIC_THRESHOLD 환경변수로 오버라이드 가능.
_DEFAULT_SEMANTIC_THRESHOLD = 0.75


@dataclass
class MatchedKeyword:
    keyword: str
    grade: str
    factor: str
    count: int
    weight: float
    score: float                # = count * weight


@dataclass
class RuleLabelResult:
    grade: str                  # TS|S1|S2|S3
    confidence: float
    grade_scores: dict[str, float]
    factor_scores: dict[str, float]      # 4대 요소 0.0~5.0
    matched_keywords: list[MatchedKeyword]
    total_score: float
    method: str = "rule_keyword"
    warnings: list[str] = field(default_factory=list)


_HIGH_RISK_PATTERNS: list[tuple[str, str, float, str]] = [
    ("TS", r"\b(?:DRAM|HBM|EUV|CVD|ALD|ICP-RIE|SiH4|N2O|sccm|Torr|Li6PS5Cl|Li2S|P2S5|LiCl|ZrO2|NMC|mAh/g)\b", 1.6, "ECONOMIC_VALUE"),
    ("TS", r"\b(?:HSM|FIPS|master\s*key|root\s*CA|SCADA|zero[- ]day|CFAR|MIMO|RLHF|LLM)\b", 1.6, "MANAGEMENT_LEVEL"),
    ("TS", r"\b(?:DCF|NDA|PMI|Post[- ]Merger|IPO|M&A|CFO|valuation|merger|acquisition)\b", 1.4, "NON_PUBLICITY"),
    ("S1", r"\b(?:GMP|DMF|PLC|TFT[- ]LCD|QKD|source\s*code|API|patent|license|trade\s*secret)\b", 1.2, "ECONOMIC_VALUE"),
    ("S1", r"\b(?:EBITDA|BUY|target\s*price|cost\s*structure|customer\s*(?:list|database)|pricing\s*model)\b", 1.0, "ECONOMIC_VALUE"),
    ("S2", r"\b(?:Weekly|Guide\s*Book|OEM|BEV|IRA|AMPC|CDMO|LNG|WTI|GHz|GWh|LTE|ETF|OECD)\b", 0.9, "NON_PUBLICITY"),
    ("S2", r"\b(?:internal\s*(?:review|plan|memo)|draft|negotiation|vendor|supplier|budget|forecast)\b", 1.0, "NON_PUBLICITY"),
]


def _apply_high_risk_overrides(
    text: str,
    grade_scores: dict[str, float],
    factor_raw: dict[str, float],
    matches: list[MatchedKeyword],
) -> None:
    """Add conservative pattern boosts for real-gold documents.

    The seeded Korean keywords cover curated synthetic text well, but public
    reports and technical snippets often contain English acronyms only. These
    boosts reduce the common S2/S1/TS -> S3 fall-through without changing the
    tie-breaking policy.
    """
    for grade, pattern, weight, factor in _HIGH_RISK_PATTERNS:
        found = re.findall(pattern, text, flags=re.IGNORECASE)
        if not found:
            continue
        count = len(found)
        score = count * weight
        grade_scores[grade] += score
        factor_raw[factor] += score
        matches.append(
            MatchedKeyword(
                keyword=pattern,
                grade=grade,
                factor=factor,
                count=count,
                weight=weight,
                score=score,
            )
        )


class LabelRuleEngine:
    def __init__(
        self,
        seeds: list[dict] | None = None,
        *,
        fnr_safe: bool = True,
        embedder: Optional[object] = None,
        semantic_threshold: Optional[float] = None,
    ) -> None:
        self.seeds = seeds or KEYWORD_SEEDS
        self.fnr_safe = fnr_safe
        self._factor_weights = {f["code"]: f["weight"] for f in FACTOR_SEEDS}
        # semantic 매칭 전용 자원 (lazy)
        self._embedder = embedder
        env_thr = os.environ.get("EMB_SEMANTIC_THRESHOLD")
        if semantic_threshold is not None:
            self.semantic_threshold = float(semantic_threshold)
        elif env_thr:
            try:
                self.semantic_threshold = float(env_thr)
            except ValueError:
                self.semantic_threshold = _DEFAULT_SEMANTIC_THRESHOLD
        else:
            self.semantic_threshold = _DEFAULT_SEMANTIC_THRESHOLD
        # seed.value 임베딩 캐시 — 동일 seed 반복 평가 시 재계산 방지
        self._seed_vec_cache: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # semantic helpers
    # ------------------------------------------------------------------
    def _get_embedder(self):
        """임베딩 어댑터 lazy 초기화.

        우선순위:
          1. 생성자에서 주입된 embedder (테스트·운영 모두 권장)
          2. EMB_PROVIDER=hash 환경변수면 HashEmbedding 강제
          3. 그 외에는 lloydk.adapters.embedding.build_embedder()로 폴백
        """
        if self._embedder is not None:
            return self._embedder
        from lloydk.adapters.embedding import build_embedder

        provider = (os.environ.get("EMB_PROVIDER") or "").strip().lower()
        force_hash = provider == "hash"
        self._embedder = build_embedder(force_hash=force_hash) if force_hash else build_embedder()
        return self._embedder

    def _embed_text(self, text: str) -> list[float]:
        emb = self._get_embedder()
        result = emb.embed([text])
        return result.vectors[0]

    def _embed_seed(self, seed_value: str) -> list[float]:
        cached = self._seed_vec_cache.get(seed_value)
        if cached is not None:
            return cached
        vec = self._embed_text(seed_value)
        self._seed_vec_cache[seed_value] = vec
        return vec

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        denom = math.sqrt(na) * math.sqrt(nb)
        if denom == 0.0:
            return 0.0
        return dot / denom

    def _semantic_match(self, text: str, seed_value: str) -> int:
        """semantic 평가 — 코사인 유사도 ≥ threshold면 1회 매칭으로 본다.

        주의: 빈도 개념이 없으므로 count는 0 또는 1.
        """
        if not text or not seed_value:
            return 0
        try:
            seed_vec = self._embed_seed(seed_value)
            query_vec = self._embed_text(text)
        except Exception:  # noqa: BLE001 — 임베딩 실패는 라벨링 전체를 막지 않음
            return 0
        sim = self._cosine(query_vec, seed_vec)
        return 1 if sim >= self.semantic_threshold else 0

    # ------------------------------------------------------------------
    # main label loop
    # ------------------------------------------------------------------
    def label(self, text: str) -> RuleLabelResult:
        if not text:
            return RuleLabelResult(
                grade="S3",
                confidence=0.0,
                grade_scores={g: 0.0 for g in GRADE_ORDER},
                factor_scores={f["code"]: 0.0 for f in FACTOR_SEEDS},
                matched_keywords=[],
                total_score=0.0,
                warnings=["empty text → default S3"],
            )

        matches: list[MatchedKeyword] = []
        grade_scores: dict[str, float] = {g: 0.0 for g in GRADE_ORDER}
        factor_raw: dict[str, float] = {f["code"]: 0.0 for f in FACTOR_SEEDS}

        for seed in self.seeds:
            kw = seed["keyword"]
            grade = seed["grade"]
            factor = seed["factor"]
            weight = float(seed["weight"])
            pattern_type = seed.get("pattern_type", "exact")
            count = self._count(text, kw, pattern_type)
            if count == 0:
                continue
            score = count * weight
            matches.append(
                MatchedKeyword(keyword=kw, grade=grade, factor=factor, count=count, weight=weight, score=score)
            )
            grade_scores[grade] += score
            factor_raw[factor] += score

        _apply_high_risk_overrides(text, grade_scores, factor_raw, matches)

        # 등급 결정
        total = sum(grade_scores.values())
        if total == 0:
            chosen = "S3"
            conf = 0.0
            warnings = ["no keyword matched → default S3"]
        else:
            # argmax. 동점이면 FNR-safe 옵션에 따라 더 높은 등급(낮은 order) 선택
            top_score = max(grade_scores.values())
            tops = [g for g, s in grade_scores.items() if s == top_score]
            chosen = min(tops, key=lambda g: GRADE_ORDER[g]) if self.fnr_safe else tops[0]
            conf = top_score / total
            warnings = []

        # 4대 평가요소 정규화 (0~5)
        max_factor = max(factor_raw.values()) if factor_raw and max(factor_raw.values()) > 0 else 1.0
        factor_scores = {k: round(min(5.0, (v / max_factor) * 5.0), 2) for k, v in factor_raw.items()}

        return RuleLabelResult(
            grade=chosen,
            confidence=round(conf, 4),
            grade_scores={g: round(s, 4) for g, s in grade_scores.items()},
            factor_scores=factor_scores,
            matched_keywords=matches,
            total_score=round(total, 4),
            warnings=warnings,
        )

    def _count(self, text: str, kw: str, pattern_type: str) -> int:
        if pattern_type == "regex":
            return len(re.findall(kw, text))
        if pattern_type == "semantic":
            return self._semantic_match(text, kw)
        # exact: 단순 부분 문자열 (한국어는 word boundary가 영문과 다름)
        if not kw:
            return 0
        return text.count(kw)


def build_rule_engine_from_db(**kwargs) -> LabelRuleEngine:
    """DB level_keywords에서 시드를 로드해 LabelRuleEngine 생성.

    다른 프로젝트에서 DB에 도메인 키워드를 등록하면 코드 변경 없이 룰 엔진이 갱신됩니다.
    DB 미가용 또는 키워드 없음 → KEYWORD_SEEDS로 자동 폴백.
    """
    from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS, load_seeds_from_db  # noqa: PLC0415
    seeds = load_seeds_from_db()
    if seeds is None:
        logger.debug("build_rule_engine_from_db: DB 미가용, KEYWORD_SEEDS 폴백")
        seeds = KEYWORD_SEEDS
    return LabelRuleEngine(seeds=seeds, **kwargs)
