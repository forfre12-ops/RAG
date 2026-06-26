"""골든셋 3-tier 논리 분할 — 정본 1파일 + **파생** tier(저장 컬럼 아님 → 드리프트 방지).

레드팀 권고: tier는 label_source/review_status/reviewer로부터 결정되는 파생값이다. 3물리 파일로
쪼개면 train_subset.jsonl식 doc_id/스키마 드리프트가 재발하므로, 정본은 하나로 두고 consumer가
이 함수로 필터한다.

tier 결정:
  - locked_gold_eval : 사람 서명(label_source=human_review, 실계정 reviewer) — **유일한 평가 정답**(P3)
  - legal_floor      : 법적근거/시나리오(public_definitive·koipa_case_based·nkt_designated·codex_review)
                       — 릴리스 차단 floor(고→S3 회귀 차단). 평가 정답 승격은 사람 서명 후. 합성/템플릿이라
                       그 자체로 일반화-진실 아님.
  - gold_candidate   : 새 게이트 자동 통과(label_source=rule_llm_agreement / review_status=gold_candidate)
                       — 사람 서명 대기. 학습엔 써도 됨.
  - silver_train     : 그 외(구 llm_judge_*, needs_review_*) — **학습 시드 전용, 평가 금지**.

consumer 계약:
  - 평가(eval) : locked_gold_eval만. 비어 있으면(9월 전) legal_floor를 interim floor로(호출부가 경고).
  - 학습(train): silver_train + gold_candidate (+ legal_floor). locked는 **절대 학습 금지**(train-on-test).
"""
from __future__ import annotations

from typing import Sequence

TIER_LOCKED = "locked_gold_eval"
TIER_LEGAL_FLOOR = "legal_floor"
TIER_CANDIDATE = "gold_candidate"
TIER_SILVER = "silver_train"

# 법적근거/시나리오 출처(고등급 backbone) — 릴리스 차단 floor.
_LEGAL_SOURCES = {"public_definitive", "koipa_case_based", "nkt_designated", "codex_review"}
# 새 게이트(P1) 자동 통과 출처.
_CANDIDATE_SOURCES = {"rule_llm_agreement"}
# 사람 아님(머신/플레이스홀더 reviewer) — import_review_corrections._is_machine_reviewer와 정합 유지.
_MACHINE_PREFIXES = ("llm_judge", "ai_assist", "codex", "model", "machine", "public_gold", "auto")


def is_human_reviewer(reviewer_id: object) -> bool:
    rid = str(reviewer_id or "").strip().lower()
    if not rid:
        return False
    return not any(rid.startswith(p) for p in _MACHINE_PREFIXES)


def tier_of(record: dict) -> str:
    """레코드의 논리 tier를 파생. label_source 우선, locked는 사람 서명까지 요구."""
    src = record.get("label_source")
    if src == "human_review" and is_human_reviewer(record.get("reviewer_id")):
        return TIER_LOCKED
    if src in _LEGAL_SOURCES:
        return TIER_LEGAL_FLOOR
    if src in _CANDIDATE_SOURCES or record.get("review_status") == "gold_candidate":
        return TIER_CANDIDATE
    return TIER_SILVER


def partition_by_tier(records: Sequence[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {
        TIER_LOCKED: [], TIER_LEGAL_FLOOR: [], TIER_CANDIDATE: [], TIER_SILVER: [],
    }
    for r in records:
        out[tier_of(r)].append(r)
    return out


def eval_records(
    records: Sequence[dict], *, allow_floor_fallback: bool = True,
) -> tuple[list[dict], str]:
    """평가 정답 = locked만. 없으면(9월 전) legal_floor를 interim으로 반환(2번째 값=실제 사용 tier).

    호출부는 반환된 tier가 legal_floor면 '템플릿 recall·일반화 아님'을 명시해 릴리스 게이트로만 쓴다.
    """
    locked = [r for r in records if tier_of(r) == TIER_LOCKED]
    if locked or not allow_floor_fallback:
        return locked, TIER_LOCKED
    floor = [r for r in records if tier_of(r) == TIER_LEGAL_FLOOR]
    return floor, TIER_LEGAL_FLOOR


def train_records(records: Sequence[dict]) -> list[dict]:
    """학습 = silver + candidate + legal_floor. locked(평가 정답)는 절대 제외(train-on-test 차단)."""
    return [r for r in records if tier_of(r) != TIER_LOCKED]
