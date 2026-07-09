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
    to_canonical_factor,
)

logger = logging.getLogger(__name__)

# semantic 매칭 기본 코사인 임계값. EMB_SEMANTIC_THRESHOLD 환경변수로 오버라이드 가능.
_DEFAULT_SEMANTIC_THRESHOLD = 0.75


def _settings_semantic_threshold() -> float:
    """[P1a] settings.rule_semantic_threshold (중앙 설정) → 없거나 오류면 하드 폴백 0.75."""
    try:
        from lloydk.config import settings  # noqa: PLC0415
        return float(getattr(settings, "rule_semantic_threshold", _DEFAULT_SEMANTIC_THRESHOLD))
    except Exception:  # noqa: BLE001
        return _DEFAULT_SEMANTIC_THRESHOLD


# ── 정본 가이드 B안 v2.2: 등급 = 비공지성(S) × 경제적유용성(V) × 비밀관리성(M) ──
# s==2·v==2 분기 선행: m=0이어도 s==2·v==2이면 S1(관리 미공식화 고가치 영업비밀).
# 이 분기가 없으면 s=2,v=2,m=0 → 곱=0 → S3 미탐 — v2.2 골든셋 FNR 분석에서 명문화.
# 곱 전용 매핑(아래)은 s=2·v=2·m=0 엣지를 처리하지 못하므로 grade_from_svm() 사용 권장.
# (doc/22 v2.2 §4.5~§4.6 / 영업비밀 등급분류 가이드 11~12p.)
SVM_GRADE_MAP: dict[int, str] = {8: "TS", 4: "TS", 2: "S2", 1: "S2", 0: "S3"}  # 근사값 — 직접 사용 금지


def grade_from_svm(s: int, v: int, m: int) -> str:
    """정본 곱셈식 등급 산정 v2.2 — 등급 = S×V×M + S1 운영 기준.

    v2.2 분기 (doc/22 §4.5~§4.6, 골든셋 100건 실증):
      s==2 AND v==2 → m==0: S1 / m≥1(곱≥4): TS
      그 외          → 곱≥1: S2 / 곱==0: S3

    s=2,v=2,m=0 → 곱=0이나 S1 확정(관리 미공식화 고가치 영업비밀).
    s<2 OR v<2이면 최고 등급은 S2(비공지성·경제가치 불완전 → S1/TS 미해당).
    """
    s, v, m = int(s), int(v), int(m)
    product = s * v * m
    if s == 2 and v == 2:
        return "TS" if product >= 4 else "S1"
    if product >= 1:
        return "S2"
    return "S3"


def grade_from_svm_floored(
    s: int,
    v: int,
    m: int,
    *,
    secrecy_proven_absent: bool = False,
    value_proven_absent: bool = False,
    mgmt_proven_absent: bool = False,
) -> str:
    """floor 원칙(doc/22 v2 §3.6)을 적용한 grade_from_svm 래퍼.

    §3.6 원칙: "요소값 0은 '근거 없음'이 아니라 공개/무가치/무관리가 *입증*된 경우에만".
    따라서 어떤 요소가 단지 '본문에 미언급'일 뿐이면 0이 아니라 1로 floor 해야
    곱셈 붕괴(S2→S3 과소분류, 이른바 'S2 dead-zone')를 막는다. grade_from_svm
    자체는 공식 가이드 워크드 예시(사무실배치도 1·1·0→S3)에 묶인 순수 함수라
    바꾸지 않고, '미입증 vs 입증' 구분을 이 래퍼에서 처리한다.

    rule_engine.label()은 S2-콘텐츠에 m_lv=1을 이미 floor하므로 배포 분류기는
    이 dead-zone에 빠지지 않는다. 이 래퍼는 *외부 라벨러/심판/평가 조립*이
    직접 S/V/M을 매길 때 같은 보호를 받도록 제공한다(예: 평가 라벨 경화).

    proven_absent=True인 요소만 0을 허용하고, 그 외에는 max(level, 1)로 floor.
    """
    se = 0 if (secrecy_proven_absent and int(s) == 0) else max(int(s), 1)
    ve = 0 if (value_proven_absent and int(v) == 0) else max(int(v), 1)
    me = 0 if (mgmt_proven_absent and int(m) == 0) else max(int(m), 1)
    return grade_from_svm(se, ve, me)


def svm_levels_for_grade(grade: str) -> tuple[int, int, int]:
    """등급을 재구성하는 표준 S/V/M 레벨 (표시 정합용).

    TS→(2,2,2) · S1→(2,2,0) · S2→(1,1,1) · S3→(0,0,0).
    v2.2: S1 표준형은 (2,2,1)=4가 아닌 (2,2,0)=0 — grade_from_svm(2,2,0)==S1 정합.
    FNR-safe 보정으로 svm_grade≠chosen일 때 표시 S/V/M을 최종 등급에 맞춰
    'S0·V0·M0인데 TS' 같은 모순 표기를 막는다. (doc/22 v2.2 §4.5)
    """
    return {"TS": (2, 2, 2), "S1": (2, 2, 0), "S2": (1, 1, 1), "S3": (0, 0, 0)}.get(grade, (1, 1, 1))


@dataclass
class MatchedKeyword:
    keyword: str
    grade: str
    factor: str
    count: int
    weight: float
    score: float                # = count * weight
    # [M-rule-evidence] 실제 매치 substring의 본문 내 위치(있으면 evidence span에 정확히 사용).
    # exact/regex/semantic 시드 매칭은 None(파이프라인이 text.find로 첫 위치 탐색).
    start: Optional[int] = None
    end: Optional[int] = None


@dataclass
class RuleLabelResult:
    grade: str                  # TS|S1|S2|S3
    confidence: float
    grade_scores: dict[str, float]
    factor_scores: dict[str, float]      # 4대 요소 0.0~5.0
    matched_keywords: list[MatchedKeyword]
    total_score: float
    method: str = "rule_keyword"
    svm: int = 0                         # 정본 B안: S×V×M 곱 (0/1/2/4/8). multiplicative 모드 산출.
    warnings: list[str] = field(default_factory=list)
    # [M축 가시화] 비밀관리성(M)이 독립 근거로 뒷받침되는가 — 형식적 관리표시(기밀/대외비/사외비
    # 등) 또는 MANAGEMENT 요소 시드가 실제 매치됐으면 True. False면 m_lv 가 콘텐츠등급에서 추정된
    # 것(독립 근거 없음)이라 검수 시 M 확인이 필요하다는 신호. **등급에는 영향 없음**(순수 메타데이터).
    management_evidenced: bool = False


def has_real_evidence(result: "RuleLabelResult") -> bool:
    """룰이 실제 한국어 시드 span을 냈는가 — 영어 약어 단독 부스트는 근거로 치지 않는다.

    규약(rule_engine 내부): 시드 매칭은 MatchedKeyword.start=None(기본값)으로 append되고,
    _apply_high_risk_overrides의 영어 약어 부스트는 start=mo.start()를 채운다. 따라서
    `start is None`이 시드 vs 약어 판별자다. 합의 게이트(consensus.evaluate_consensus)의
    has_real_evidence 인자로 전달된다.

    주의: grade=='S3'를 추가로 제외하지 말 것 — admission이 rule==llm을 이미 요구하므로 그러면
    S3 문서가 이 게이트로는 영영 gold가 못 된다. 근거 = "실제 시드가 떴다"는 등급 무관 사실.
    (start 규약이 바뀌면 test_rule_engine_evidence가 깨져 알려준다.)
    """
    return any(mk.start is None for mk in result.matched_keywords)


_HIGH_RISK_PATTERNS: list[tuple[str, str, float, str]] = [
    ("TS", r"\b(?:DRAM|HBM|EUV|CVD|ALD|ICP-RIE|SiH4|N2O|sccm|Torr|Li6PS5Cl|Li2S|P2S5|LiCl|ZrO2|NMC|mAh/g)\b", 1.6, "ECONOMIC_VALUE"),
    ("TS", r"\b(?:HSM|FIPS|master\s*key|root\s*CA|SCADA|zero[- ]day|CFAR|MIMO|RLHF|LLM)\b", 1.6, "MANAGEMENT_LEVEL"),
    ("TS", r"\b(?:DCF|NDA|PMI|Post[- ]Merger|IPO|M&A|CFO|valuation|merger|acquisition)\b", 1.4, "NON_PUBLICITY"),
    ("S1", r"\b(?:GMP|DMF|PLC|TFT[- ]LCD|QKD|source\s*code|API|patent|license|trade\s*secret)\b", 1.2, "ECONOMIC_VALUE"),
    ("S1", r"\b(?:EBITDA|BUY|target\s*price|cost\s*structure|customer\s*(?:list|database)|pricing\s*model)\b", 1.0, "ECONOMIC_VALUE"),
    ("S1", "(?:\uAE30\uC5C5\\s*\uC2E4\uC0AC|\uC778\uC218\\s*\uBB34\uC0B0|\uD569\uBCD1\\s*\uAC00\uACA9|\uC778\uC218\\s*\uAC00\uACA9|\uBE44\uACF5\uAC1C\\s*\uC774\uC0AC\uD68C|\uBBF8\uACF5\uC2DC)", 1.2, "NON_PUBLICITY"),
    ("S2", r"\b(?:Weekly|Guide\s*Book|OEM|BEV|IRA|AMPC|CDMO|LNG|WTI|GHz|GWh|LTE|ETF|OECD)\b", 0.9, "NON_PUBLICITY"),
    ("S2", r"\b(?:internal\s*(?:review|plan|memo)|draft|negotiation|vendor|supplier|budget|forecast)\b", 1.0, "NON_PUBLICITY"),
]


def _apply_high_risk_overrides(
    text: str,
    grade_scores: dict[str, float],
    factor_raw: dict[str, float],
    matches: list[MatchedKeyword],
    *,
    weight_multiplier: float = 1.0,
) -> None:
    """Add conservative pattern boosts for real-gold documents.

    The seeded Korean keywords cover curated synthetic text well, but public
    reports and technical snippets often contain English acronyms only. These
    boosts reduce the common S2/S1/TS -> S3 fall-through without changing the
    tie-breaking policy.

    [B1] weight_multiplier(settings.rule_high_risk_weight_multiplier)로 부스트 영향력을
    코드 변경 없이 조정한다. 1.0=기본(동작 보존). 골든셋 PR곡선에서 범용 약어의 단독 과분류가
    확인되면 이 값을 낮춰(예 0.6) 영향을 줄인다. <=0이면 부스트 비활성(조기 반환).

    [M-rule-evidence] 매치를 MatchedKeyword로 기록할 때 keyword에 정규식 원문이 아니라
    실제 매치된 substring(re.finditer의 group(0))과 span을 저장한다. 그래야 pipeline의
    evidence span(text.find(keyword))이 정규식이 아닌 실제 본문 토큰을 가리킨다.
    [M-rule-factor] grade_scores/factor_raw는 키가 없을 수 있으므로 KeyError 대신
    setdefault로 누산한다(DB LevelKeyword가 시드 외 factor_code를 가질 때 방어).
    """
    if weight_multiplier <= 0:
        return
    for grade, pattern, weight, factor in _HIGH_RISK_PATTERNS:
        eff_weight = weight * weight_multiplier
        for mo in re.finditer(pattern, text, flags=re.IGNORECASE):
            matched = mo.group(0)
            score = eff_weight  # finditer는 매치당 1회 → count=1
            grade_scores[grade] = grade_scores.get(grade, 0.0) + score
            factor_raw[factor] = factor_raw.get(factor, 0.0) + score
            matches.append(
                MatchedKeyword(
                    keyword=matched,
                    grade=grade,
                    factor=factor,
                    count=1,
                    weight=eff_weight,
                    score=score,
                    start=mo.start(),
                    end=mo.end(),
                )
            )


# [M축 가시화] 형식적 비밀관리 표시(분류 마킹) — 조직이 정보를 비밀로 '관리'한다는 직접 증거.
# 이 목록은 **탐지 전용**이다: 등급 산정(seed/argmax/m_lv)에 일절 개입하지 않고, 관리표시
# 존재 여부만 읽어 management_evidenced 플래그·검수 경고에 쓴다(등급 변경 없음·FNR/정밀도
# 무영향). ⚠️ 이 목록을 KEYWORD_SEEDS(MANAGEMENT_LEVEL)로 승격하면 안 된다 — S1 콘텐츠+관리
# 표시가 svm(2,2,2)=TS 로 과분류되는 피드백이 발생(2026-07-01 eval 실증, S3+사외비→TS).
_MANAGEMENT_MARKING_TERMS: tuple[str, ...] = (
    "특급기밀", "극비", "대외비", "사외비", "기밀", "1급 비밀", "1급비밀", "2급 비밀",
    "3급 비밀", "취급주의", "열람제한", "열람 제한", "접근제한", "접근 제한", "접근통제",
    "대외주의", "내부한정", "사내한정", "내부 전용", "사내 전용",
    "Confidential", "Restricted", "Top Secret", "Classified", "Internal Only",
    "Need-to-Know", "Eyes-Only",
)


def detect_management_marking(text: str) -> bool:
    """본문에 형식적 비밀관리 표시가 있는지 — 탐지 전용(등급 무영향).

    비밀관리성(M)의 독립 근거 유무를 판정하는 데만 쓴다. 대소문자 무시(영문 표시).
    """
    if not text:
        return False
    low = text.lower()
    return any(term.lower() in low for term in _MANAGEMENT_MARKING_TERMS)


class LabelRuleEngine:
    def __init__(
        self,
        seeds: list[dict] | None = None,
        *,
        fnr_safe: bool = True,
        embedder: Optional[object] = None,
        semantic_threshold: Optional[float] = None,
        method: Optional[str] = None,
        high_risk_weight_multiplier: Optional[float] = None,
    ) -> None:
        self.seeds = seeds or KEYWORD_SEEDS
        self.fnr_safe = fnr_safe
        # 정본 B안: 등급 = S×V×M(multiplicative). additive = 레거시 가중합.
        self.method = method or "multiplicative"
        # [B1] 고위험 패턴 가중치 배수 — 생성자 주입 > settings > 1.0(기본, 동작 보존).
        if high_risk_weight_multiplier is not None:
            self.high_risk_weight_multiplier = float(high_risk_weight_multiplier)
        else:
            try:
                from lloydk.config import settings  # noqa: PLC0415
                self.high_risk_weight_multiplier = float(
                    getattr(settings, "rule_high_risk_weight_multiplier", 1.0)
                )
            except Exception:  # noqa: BLE001
                self.high_risk_weight_multiplier = 1.0
        self._factor_weights = {f["code"]: f["weight"] for f in FACTOR_SEEDS}
        # semantic 매칭 전용 자원 (lazy)
        self._embedder = embedder
        # [P1a] 우선순위: 생성자 주입 > EMB_SEMANTIC_THRESHOLD env > settings > 0.75(하드 폴백).
        env_thr = os.environ.get("EMB_SEMANTIC_THRESHOLD")
        if semantic_threshold is not None:
            self.semantic_threshold = float(semantic_threshold)
        elif env_thr:
            try:
                self.semantic_threshold = float(env_thr)
            except ValueError:
                self.semantic_threshold = _settings_semantic_threshold()
        else:
            self.semantic_threshold = _settings_semantic_threshold()
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

    def _semantic_match(
        self, text: str, seed_value: str, *, query_vec: Optional[list[float]] = None
    ) -> int:
        """semantic 평가 — 코사인 유사도 ≥ threshold면 1회 매칭으로 본다.

        주의: 빈도 개념이 없으므로 count는 0 또는 1.

        #22: 동일 문서의 중복 임베딩 제거. label() 1회 호출에서 문서 벡터를
        한 번만 계산해 query_vec으로 주입하면 seed마다의 _embed_text(text)
        재계산을 피한다. query_vec 미주입 시(단독 호출·테스트)에는 기존처럼
        문서 벡터를 직접 계산해 동작·폴백 경로를 그대로 보존한다.
        """
        if not text or not seed_value:
            return 0
        try:
            seed_vec = self._embed_seed(seed_value)
            # #22: 호출자가 미리 계산한 문서 벡터가 있으면 재사용, 없으면 1회 계산.
            if query_vec is None:
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

        # #22: 문서(쿼리) 벡터를 label() 1회 호출에서 단 한 번만 계산해 모든 semantic
        # seed 비교에 재사용한다. 기존엔 seed마다 _semantic_match가 _embed_text(text)를
        # 호출해 동일 문서를 N회 재임베딩했다(seed 벡터만 캐시). semantic seed가
        # 있을 때만 1회 임베딩하며, 실패하면 None으로 둬 seed별 폴백(매칭 0) 경로를 보존한다.
        doc_query_vec: Optional[list[float]] = None
        has_semantic = any(s.get("pattern_type") == "semantic" for s in self.seeds)
        if has_semantic:
            try:
                doc_query_vec = self._embed_text(text)
            except Exception:  # noqa: BLE001 — 임베딩 실패는 라벨링 전체를 막지 않음(폴백 보존)
                doc_query_vec = None

        for seed in self.seeds:
            kw = seed["keyword"]
            grade = seed["grade"]
            factor = seed["factor"]
            weight = float(seed["weight"])
            pattern_type = seed.get("pattern_type", "exact")
            count = self._count(text, kw, pattern_type, query_vec=doc_query_vec)
            if count == 0:
                continue
            score = count * weight
            matches.append(
                MatchedKeyword(keyword=kw, grade=grade, factor=factor, count=count, weight=weight, score=score)
            )
            # [M-rule-factor] DB LevelKeyword가 시드 외 grade/factor_code를 가지면 KeyError가
            # 나므로 setdefault 누산(get+더하기)으로 방어. 미초기화 키는 0.0에서 시작.
            grade_scores[grade] = grade_scores.get(grade, 0.0) + score
            factor_raw[factor] = factor_raw.get(factor, 0.0) + score

        _apply_high_risk_overrides(
            text, grade_scores, factor_raw, matches,
            weight_multiplier=self.high_risk_weight_multiplier,
        )

        # 등급 결정
        total = sum(grade_scores.values())
        if total == 0:
            chosen = "S3"
            conf = 0.0
            warnings = ["no keyword matched → default S3"]
        else:
            # argmax. 동점이면 FNR-safe 옵션에 따라 더 높은 등급(낮은 order) 선택.
            # [M-rule-factor] DB 시드가 GRADE_ORDER 외 등급을 더했을 수 있어 order 조회는
            # KeyError 대신 큰 값(낮은 우선순위)으로 폴백 — 미지 등급은 동점 시 후순위.
            top_score = max(grade_scores.values())
            tops = [g for g, s in grade_scores.items() if s == top_score]
            chosen = (
                min(tops, key=lambda g: GRADE_ORDER.get(g, 999))
                if self.fnr_safe
                else tops[0]
            )
            conf = top_score / total
            warnings = []

        # 레거시 4요소 factor → 정본 3요건(S/V/M)으로 폴딩 (B안 정합, doc/22 v2 §3).
        # 유출영향도는 VALUE로 흡수(R4). 키워드 재태깅 없이 to_canonical_factor로 정규화.
        folded: dict[str, float] = {}
        for code, val in factor_raw.items():
            cf = to_canonical_factor(code)
            folded[cf] = folded.get(cf, 0.0) + val
        factor_raw = folded

        # 3요건(S/V/M) 평가요소 정규화 (0~5) — additive/표시용
        max_factor = max(factor_raw.values()) if factor_raw and max(factor_raw.values()) > 0 else 1.0
        factor_scores = {k: round(min(5.0, (v / max_factor) * 5.0), 2) for k, v in factor_raw.items()}

        # ── 정본 B안: 등급 = S×V×M (multiplicative) ──
        # 원칙(doc/22 v2 §3.6): 요소값 0은 "근거 없음"이 아니라 "공개/가치없음/관리안됨이 *입증*된
        # 경우"에만. 기본 floor=1 → 곱셈 붕괴(과소분류) 방지. 곱 결과가 콘텐츠 등급보다 낮으면
        # FNR-safe 콘텐츠 가드(R2)로 끌어올림(silent 하향 차단).
        svm_val = 0
        # B안 곱셈은 영업비밀 4등급 스킴에만 적용 — 커스텀 등급(타 프로젝트 DB 스킴)은 우회(genericity 보존).
        if self.method == "multiplicative" and total > 0 and chosen in ("TS", "S1", "S2", "S3"):
            content_grade = chosen
            public = any(
                mm.grade == "S3" and to_canonical_factor(mm.factor) == "SECRECY" for mm in matches
            )
            strong = content_grade in ("TS", "S1")
            has_mgmt = any(
                to_canonical_factor(mm.factor) == "MANAGEMENT" and mm.grade in ("TS", "S1")
                for mm in matches
            )
            s_lv = 0 if (public or content_grade == "S3") else (2 if strong else 1)
            v_lv = 2 if strong else (0 if content_grade == "S3" else 1)
            # v2.2: S1 전형은 s=2·v=2·m=0 — 관리 키워드 없는 S1 콘텐츠에 m=1을 주면
            # grade_from_svm(2,2,1)=TS가 되어 FNR-safe가 TS로 과상향시킴.
            m_lv = 2 if has_mgmt else (0 if content_grade in ("S3", "S1") else 1)
            svm_val = s_lv * v_lv * m_lv
            svm_grade = grade_from_svm(s_lv, v_lv, m_lv)
            final = (
                min([svm_grade, content_grade], key=lambda g: GRADE_ORDER.get(g, 999))
                if self.fnr_safe
                else svm_grade
            )
            if svm_grade != content_grade:
                warnings = warnings + [
                    f"svm={svm_val}({svm_grade})↔content({content_grade}) → FNR-safe {final}"
                ]
            chosen = final
            # [표시 정합 B2/A3] FNR-safe 보정으로 svm_grade≠chosen이면 표시 S/V/M을 최종 등급에
            # 정합화. 예: public 게이트로 svm=S3지만 content=TS → chosen=TS → S/V/M도 TS 기준 표시.
            # (그렇지 않으면 'S0·V0·M0인데 TS' 모순 표기가 검수자에게 노출됨)
            if grade_from_svm(s_lv, v_lv, m_lv) != chosen:
                s_lv, v_lv, m_lv = svm_levels_for_grade(chosen)
                svm_val = s_lv * v_lv * m_lv
            # 정본 3요건은 레벨(0/1/2)로 덮되, 커스텀 factor 키는 보존(merge — genericity 계약).
            factor_scores = {**factor_scores, "SECRECY": float(s_lv), "VALUE": float(v_lv), "MANAGEMENT": float(m_lv)}

        # [M축 가시화] 비밀관리성(M) 독립 근거 유무 — 등급 결정 후 순수 메타데이터로만 산출
        # (grade/factor/svm 에 일절 개입 안 함). 관리표시(기밀/대외비 등) 또는 MANAGEMENT 요소
        # 시드가 실제 매치됐으면 evidenced. 아니면 m_lv 가 콘텐츠등급에서 추정된 것 → 검수 경고.
        mgmt_factor_matched = any(
            to_canonical_factor(mm.factor) == "MANAGEMENT" for mm in matches
        )
        management_evidenced = mgmt_factor_matched or detect_management_marking(text)
        if chosen != "S3" and not management_evidenced:
            warnings = warnings + [
                "비밀관리성(M) 독립 근거 없음 — 관리표시/관리요소 미검출, 콘텐츠등급 기반 추정"
                " (검수 시 M 확인 권장)"
            ]

        return RuleLabelResult(
            grade=chosen,
            confidence=round(conf, 4),
            grade_scores={g: round(s, 4) for g, s in grade_scores.items()},
            factor_scores=factor_scores,
            matched_keywords=matches,
            total_score=round(total, 4),
            svm=svm_val,
            warnings=warnings,
            management_evidenced=management_evidenced,
        )

    def _count(
        self, text: str, kw: str, pattern_type: str, *, query_vec: Optional[list[float]] = None
    ) -> int:
        if pattern_type == "regex":
            return len(re.findall(kw, text))
        if pattern_type == "semantic":
            # #22: label()이 1회 계산한 문서 벡터를 그대로 전달(중복 임베딩 제거).
            return self._semantic_match(text, kw, query_vec=query_vec)
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
