"""통합 골든셋 빌더 코어 — 후보 문서를 〈위생→라벨→합의→조립〉으로 골든후보/검수대상 분류.

P1 재설계(2026-06): 합의 게이트가 conf 대신 〈합의(rule==llm)+실제 근거 span+self-consistency〉로
gold_candidate를 판정한다(consensus.evaluate_consensus). label_fn은 ConsensusJudge(주 판정자 k샘플
self-consistency + Qwen 섀도)를 묶어 has_real_evidence/self_consistency/shadow_grade/airgap을 함께 낸다.
라벨러를 주입(label_fn)받으므로 DB/Celery/LLM 없이 단위 테스트 가능하다.
서비스·Celery·route 계층(G3b)이 이 코어를 감싸 비동기/잡/검수큐로 노출한다.

gold_candidate는 평가정답(locked_gold_eval)이 아니다 — 사람 서명이 진실을 만든다(P3).
needs_review = 사람 판정 대기(review_priority로 정렬, LLM은 보조).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Sequence

from lloydk.hygiene import text_hash
from lloydk.modules.m3_labeling.consensus import evaluate_consensus

# 게이트 버전 스탬프 — 각 산출 레코드에 박아 재현/감사 가능하게(구 conf 게이트=암묵 v1).
GATE_VERSION = "v2_agreement_evidence"


@dataclass
class LabelPair:
    """label_fn 반환형 — 룰/LLM 라벨 + 게이트 입력.

    rule_conf/llm_conf는 정렬(sort_conf)용일 뿐 admission엔 쓰이지 않는다(P1 재설계).
    """

    rule_grade: str
    rule_conf: float
    llm_grade: str
    llm_conf: float = 0.0              # sort-only(평균 자가보고 conf)
    has_real_evidence: bool = False    # 룰이 실제 한국어 시드 근거를 냈는가(약어 단독 제외)
    self_consistency: float = 1.0      # 주 판정자 k샘플 다수표 비율(1.0=만장일치)
    shadow_grade: str | None = None    # Qwen 섀도 등급(상용 주판정 시)
    airgap: bool = False               # 주 판정자가 온프렘 Qwen(상용 미연동/민감문서)


@dataclass
class GoldenRecord:
    """빌더 1건 결과 — gold_candidate(자동 후보) 또는 needs_review(검수대상)."""

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
    flags: tuple = ()
    review_priority: float = 0.0
    self_consistency: float = 1.0
    shadow_grade: str | None = None
    gate_version: str = GATE_VERSION
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
            "flags": list(self.flags),
            "review_priority": self.review_priority,
            "self_consistency": self.self_consistency,
            "shadow_grade": self.shadow_grade,
            "gate_version": self.gate_version,
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
    min_self_consistency: float = 0.67,
    require_evidence: bool = True,
    text_key: str = "text",
    id_key: str = "doc_id",
    **_legacy,  # 구 min_rule_conf/min_llm_conf 흡수(게이트 미사용; 서비스 호출 호환)
) -> GoldenBuildResult:
    """후보 docs를 골든후보(gold_candidate)/검수대상(needs_review)으로 분류.

    단계:
      (1) 위생 — 정규화 text_hash로 본문 중복 제거 + holdout/train 누출 제거(G2)
      (2) 라벨 — label_fn(text)로 룰·판정자 라벨 + 근거·self-consistency·섀도 획득(주입)
      (3) 합의 — evaluate_consensus로 gold_candidate/needs_review 판정(G1, conf 비사용)
      (4) 조립 — GoldenRecord 버킷팅 + review_priority 정렬 + 통계

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
            lp.rule_grade,
            lp.llm_grade,
            has_real_evidence=lp.has_real_evidence,
            self_consistency=lp.self_consistency,
            shadow_grade=lp.shadow_grade,
            airgap=lp.airgap,
            sort_conf=lp.llm_conf,
            min_self_consistency=min_self_consistency,
            require_evidence=require_evidence,
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
            flags=verdict.flags,
            review_priority=round(verdict.review_priority, 4),
            self_consistency=round(lp.self_consistency, 4),
            shadow_grade=lp.shadow_grade,
            source=doc.get("source", ""),
            domain=doc.get("domain", ""),
        )
        (gold if verdict.is_gold else uncertain).append(rec)

    # 검수대상은 review_priority 내림차순(ts_downgrade·고등급·애매도 먼저) — 사람 큐 정렬.
    uncertain.sort(key=lambda r: r.review_priority, reverse=True)

    stats = {
        "input": len(docs),
        "gold": len(gold),
        "uncertain": len(uncertain),
        "dropped_duplicate": dropped_dup,
        "dropped_leaked": dropped_leak,
        "gold_by_grade": dict(Counter(r.label for r in gold)),
        "gold_by_status": dict(Counter(r.status for r in gold)),
        "uncertain_by_status": dict(Counter(r.status for r in uncertain)),
    }
    return GoldenBuildResult(
        gold=gold,
        uncertain=uncertain,
        dropped_duplicate=dropped_dup,
        dropped_leaked=dropped_leak,
        stats=stats,
    )


_LABELS = ("TS", "S1", "S2", "S3")


def make_label_fn(
    provider: str = "noop",
    *,
    rule_pipeline=None,
    judge=None,
    sensitive: bool = False,
) -> Callable[[str], LabelPair]:
    """운영 label_fn — 룰 LabelingPipeline + ConsensusJudge(주 판정자 self-consistency + Qwen 섀도).

    provider = 주 판정자(상용 택일 anthropic|gemini|openai; 미지정/비-상용/민감 시 온프렘 Qwen).
    sensitive=True면 공개 클라우드로 보내지 않고 airgap(Qwen) 라우팅(데이터 거버넌스).
    무거운 m3/provider 의존은 lazy import라 build_golden_set만 쓰는 경로(테스트 포함)는 가볍게 유지된다.
    테스트는 fake label_fn을 직접 주입한다(이 팩토리를 거치지 않음).
    """
    from lloydk.modules.m3_labeling import LabelingPipeline  # lazy
    from lloydk.modules.m3_labeling.rule_engine import has_real_evidence as _has_evidence  # lazy

    rp = rule_pipeline or LabelingPipeline()
    if judge is None:
        from lloydk.modules.m3_labeling.judge import build_consensus_judge  # lazy

        judge = build_consensus_judge(primary_name=provider, sensitive=sensitive)
    jl = judge

    def label_fn(text: str) -> LabelPair:
        rr = rp.label(text)
        rule_grade = rr.grade.value if hasattr(rr.grade, "value") else str(rr.grade)
        evidence = bool(rr.rule_result) and _has_evidence(rr.rule_result)
        jres = jl.judge(text)
        llm_grade = jres.grade if jres.grade in _LABELS else "S3"
        return LabelPair(
            rule_grade=rule_grade,
            rule_conf=float(rr.confidence),
            llm_grade=llm_grade,
            llm_conf=float(jres.mean_conf),
            has_real_evidence=evidence,
            self_consistency=float(jres.self_consistency),
            shadow_grade=jres.shadow_grade,
            airgap=jres.airgap,
        )

    return label_fn


@dataclass
class PromoteResult:
    added: int
    skipped_duplicate: int
    skipped_leaked: int
    skipped_not_gold: int
    total_candidates: int
    gold_total: int  # 병합 후 gold 건수


def promote_candidates(
    candidates: Sequence[dict],
    existing_gold: Sequence[dict],
    *,
    holdout_texts: Sequence[str] | None = None,
    text_key: str = "text",
) -> tuple[list[dict], PromoteResult]:
    """빌더 후보(build_<id>.jsonl)를 기존 gold에 게이트 통과분만 병합 (G4).

    게이트:
      (1) label 없음/등급외(검수대상·빈건) → skip(not_gold)
      (2) 기존 gold와 본문 중복(정규화 text_hash) → skip(duplicate)
      (3) holdout 누출(text_hash) → skip(leaked) — 학습/평가 순환성 차단

    정본 파일을 직접 쓰지 않고 '병합된 리스트 + 통계'를 반환한다. 호출부(CLI/엔드포인트/
    운영자)가 check_data_quality 게이트 통과 후 기록을 결정한다(정본 보호).
    """
    merged = list(existing_gold)
    seen = {text_hash(r.get(text_key, "")) for r in merged if r.get(text_key)}
    holdout_hashes = {text_hash(t) for t in (holdout_texts or [])}

    added = skip_dup = skip_leak = skip_notgold = 0
    for c in candidates:
        text = (c.get(text_key) or "").strip()
        if not text or c.get("label") not in _LABELS:
            skip_notgold += 1
            continue
        h = text_hash(text)
        if h in seen:
            skip_dup += 1
            continue
        if h in holdout_hashes:
            skip_leak += 1
            continue
        merged.append(c)
        seen.add(h)
        added += 1

    result = PromoteResult(
        added=added,
        skipped_duplicate=skip_dup,
        skipped_leaked=skip_leak,
        skipped_not_gold=skip_notgold,
        total_candidates=len(candidates),
        gold_total=len(merged),
    )
    return merged, result
