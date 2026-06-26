"""룰 라벨러 + LLM 판정자 합의 게이트 (gold_candidate 편입 판정).

P1 재설계(2026-06): conf 임계값을 admission에서 **완전 제거**.
게이트 무게중심 = 합의(rule==llm) + 실제 근거 span + 판정자 자기일관(self-consistency).
conf는 사람 검토 큐 정렬(review_priority)에만 쓰이고, 절대 자동확정에 쓰이지 않는다
(실측 llm_conf AUROC 0.535 = 동전 — 단독 신뢰 불가).

이 게이트는 gold_candidate(자동 후보)만 만든다 — 평가정답(locked_gold_eval)이 아니다.
평가정답 승격은 별개 경로(지재원 관리자 사람 서명, TS/S1 이중서명; P3).
needs_review = 사람 판정 대기(LLM은 보조: 제안등급·근거·플래그·우선순위만 차려놓는다).
(torch 등 무거운 의존 없음 — 순수 결정 함수)

구 path A/B/C(룰무의견 LLM 단독 + conf 게이트)는 삭제됨: 룰 근거 없는 단일 LLM 라벨이
gold가 되던 67% 경로가 이 재설계의 제거 대상이다.
"""
from __future__ import annotations

from dataclasses import dataclass

# 등급 위험순위(낮을수록 고위험). FNR-safe 비교·다운그레이드 탐지에 사용.
GRADE_RANK = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
# 고등급(미탐 시 가장 치명적). airgap 자동확정 금지·다운그레이드 탐지 기준.
UPPER = ("TS", "S1")

# --- 레거시 상수(v1 호환·외부 참조 보존용; 새 게이트는 admission에 쓰지 않음) ---
RULE_NO_OPINION_THRESHOLD = 0.05
DEFAULT_UPPER_GRADES = ("TS", "S1", "S2")


@dataclass(frozen=True)
class ConsensusResult:
    """합의 게이트 판정 결과 (P1 v2).

    is_gold == gold_candidate(자동 후보). 평가정답 아님 — 사람 서명이 진실을 만든다(P3).
    """

    is_gold: bool
    status: str          # gold_candidate | needs_review_ts_downgrade | needs_review_no_evidence
                         #   | needs_review_disagree | needs_review_low_agreement
                         #   | needs_review_airgap_upper | needs_review_production_suspect
    label_source: str    # rule_llm_agreement | needs_review
    review_status: str   # gold_candidate | needs_review   (구 'accepted' 제거)
    agree: bool
    has_real_evidence: bool
    flags: tuple[str, ...]
    review_priority: float  # 사람 큐 정렬 키 ONLY — 절대 admission에 안 쓰임


def evaluate_consensus(
    rule_grade: str,
    llm_grade: str,
    *,
    has_real_evidence: bool,
    self_consistency: float = 1.0,
    shadow_grade: str | None = None,
    airgap: bool = False,
    min_self_consistency: float = 0.67,
    sort_conf: float = 0.0,
    **_legacy,  # 구 keyword 인자(min_rule_conf 등) 흡수 — 1 릴리스 한시. (위치인자는 못 막음!)
) -> ConsensusResult:
    """룰·LLM 판정을 합의+근거 게이트로 평가해 gold_candidate 편입 여부 판정.

    admission(자동 후보) 조건 — conf는 전혀 보지 않는다:
        has_real_evidence AND (rule_grade == llm_grade)
        AND not low_agreement AND not ts_downgrade_suspect
        AND not production_suspect AND not airgap_blocked_upper
      (airgap = 상용 교차검증 없음 → 상위등급 TS/S1 자동확정 금지)

    그 외/플래그된 것은 needs_review(사람 판정 대기). sort_conf는 review_priority 정렬에만 사용.

    인자:
      has_real_evidence: 룰이 실제 한국어 시드 span을 냈는가(영어 약어 단독 부스트는 제외).
      self_consistency : 주 판정자 k회 샘플의 다수표 비율(1.0=만장일치). 애매도 신호.
      shadow_grade     : 상용이 주판정일 때 Qwen 섀도 등급(배포 모델 실패 예측). None=섀도 없음/airgap.
      airgap           : 주 판정자가 온프렘 Qwen(상용 미연동/민감문서). 상위등급 자동확정 차단.
    """
    agree = rule_grade == llm_grade
    chosen = llm_grade
    low_agreement = self_consistency < min_self_consistency

    # 고등급 안전: 룰/주판정/섀도 중 누가 TS/S1인데 최종 등급이 그보다 낮으면(미탐 의심) 차단.
    voices = [g for g in (rule_grade, llm_grade, shadow_grade) if g in GRADE_RANK]
    chosen_rank = GRADE_RANK.get(chosen, 99)
    ts_downgrade_suspect = any((g in UPPER) and GRADE_RANK[g] < chosen_rank for g in voices)
    # 섀도(배포급 모델)가 주판정과 갈리면 = 출하 모델이 여기서 틀릴 가능성 → 사람에게 surfacing.
    production_suspect = shadow_grade is not None and shadow_grade != llm_grade
    airgap_blocked_upper = airgap and (chosen in UPPER)

    flags: list[str] = []
    if ts_downgrade_suspect:
        flags.append("ts_downgrade_suspect")
    if production_suspect:
        flags.append("production_suspect")
    if low_agreement:
        flags.append("low_agreement")
    if not has_real_evidence:
        flags.append("no_evidence")
    if airgap_blocked_upper:
        flags.append("airgap_upper")

    # 차단 사유(우선순위 순서로 첫 매치가 status). ts_downgrade가 generic disagree보다 우선.
    if ts_downgrade_suspect:
        reason = "ts_downgrade"
    elif not has_real_evidence:
        reason = "no_evidence"
    elif not agree:
        reason = "disagree"
    elif low_agreement:
        reason = "low_agreement"
    elif airgap_blocked_upper:
        reason = "airgap_upper"
    elif production_suspect:
        reason = "production_suspect"
    else:
        reason = None  # 통과 → gold_candidate

    if reason is None:
        is_gold = True
        status = "gold_candidate"
        label_source = "rule_llm_agreement"
        review_status = "gold_candidate"
    else:
        is_gold = False
        status = f"needs_review_{reason}"
        label_source = "needs_review"
        review_status = "needs_review"

    # review_priority: 정렬 ONLY. ts_downgrade 최우선 → 고등급 → production_suspect → 애매도 →
    # conf는 마지막 저가중 tiebreaker(낮은 conf 먼저 검토).
    review_priority = (
        100.0 * float(ts_downgrade_suspect)
        + 10.0 * float(chosen in UPPER)
        + 5.0 * float(production_suspect)
        + 2.0 * (1.0 - self_consistency)
        + (1.0 - sort_conf)
    )

    return ConsensusResult(
        is_gold=is_gold,
        status=status,
        label_source=label_source,
        review_status=review_status,
        agree=agree,
        has_real_evidence=has_real_evidence,
        flags=tuple(flags),
        review_priority=review_priority,
    )
