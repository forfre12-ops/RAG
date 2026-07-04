"""골든셋 3-tier 논리 분할 — 정본 1파일 + **파생** tier(저장 컬럼 아님 → 드리프트 방지).

레드팀 권고: tier는 label_source/review_status/reviewer로부터 결정되는 파생값이다. 3물리 파일로
쪼개면 train_subset.jsonl식 doc_id/스키마 드리프트가 재발하므로, 정본은 하나로 두고 consumer가
이 함수로 필터한다.

tier 결정:
  - locked_gold_eval : 사람 서명(label_source=human_review, 실계정 reviewer) — **유일한 평가 정답**(P3)
  - legal_floor      : 법적근거/시나리오(public_definitive·nkt_designated + koipa_case_based·
                       codex_review·curated_scenario) — 릴리스 차단 floor(고→S3 회귀 차단). 평가 정답
                       승격은 사람 서명 후. 합성/템플릿이라 그 자체로 일반화-진실 아님.
                       ⚠️ floor 안에서도 권위가 갈린다: 외부권위(EXTERNAL_AUTHORITY_SOURCES,
                       record 검증은 is_external_authority)만 실측 인용 가능하고, 큐레이트 프록시
                       (SYNTHETIC_PROXY_SOURCES — koipa 판례 인용 조작 확인, 2026-07-03 감사)는
                       실세계 정답·서빙 권위로 취급 금지.
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

# ── 권위 분류(2026-07-03 감사) — floor tier 안에서도 '실세계 정답'과 '큐레이트 프록시'를 구분한다 ──
# 외부권위: 라벨 근거가 텍스트 밖 실세계 사실(공개 판결/공시=public_definitive, §9 고시 지정=
# nkt_designated). 사람서명 없이 실측 인용 가능한 유일한 축. 단 nkt는 record가 지정근거
# (legal_reference)를 가질 때만 성립 — 근거 없는 손작성 시나리오가 이 출처를 위조 사용한 사례
# 6건 발견(augment_high_risk_gold, → curated_scenario로 교정). record 수준은 is_external_authority().
EXTERNAL_AUTHORITY_SOURCES = frozenset({"public_definitive", "nkt_designated"})
# 큐레이트/합성 프록시: 손작성 시나리오·LLM 리뷰. 감사에서 koipa_case_based의 판례 인용이 실사건과
# 무대응(사건번호 무관 재사용·자리표시자·시대착오)으로 확인 → 실세계 정답(legally grounded)·서빙
# 권위 출처로 취급 금지. 릴리스 차단 floor 회귀·학습 시드로만 쓴다.
SYNTHETIC_PROXY_SOURCES = frozenset({"koipa_case_based", "codex_review", "curated_scenario"})
# 손작성 합성 시나리오 텍스트의 self-tag(collect_public_gold.build_scenario_records가 record.source에
# 부여). 라벨 권위(고시 지정 등)가 정당해도 '본문'이 합성이면 real-text 실측 인용 불가 —
# is_external_authority의 2번째 축(2026-07-04 텍스처 갭 감사). label floor 역할은 tier_of가 유지.
SYNTHETIC_TEXT_SOURCE = "public_scenario"
# 법적근거/시나리오 출처(고등급 backbone) — 릴리스 차단 floor(tier 파생용 합집합).
_LEGAL_SOURCES = EXTERNAL_AUTHORITY_SOURCES | SYNTHETIC_PROXY_SOURCES
# 새 게이트(P1) 자동 통과 출처.
_CANDIDATE_SOURCES = {"rule_llm_agreement"}
# 사람 아님(머신/플레이스홀더 reviewer) — import_review_corrections._is_machine_reviewer와 정합 유지.
_MACHINE_PREFIXES = ("llm_judge", "ai_assist", "codex", "model", "machine", "public_gold", "auto")


def is_human_reviewer(reviewer_id: object) -> bool:
    rid = str(reviewer_id or "").strip().lower()
    if not rid:
        return False
    return not any(rid.startswith(p) for p in _MACHINE_PREFIXES)


def is_external_authority(record: dict) -> bool:
    """record가 '외부권위 정답'(사람서명 없이 실측 인용 가능)인가.

    두 축을 모두 요구한다 — 라벨 권위(external label)와 텍스트 실재(real text):
      1) source 수준(EXTERNAL_AUTHORITY_SOURCES) + nkt_designated는 지정근거(legal_reference)
         필수 — 근거 없는 손작성 시나리오가 nkt를 위조 사용해 버킷을 오염시킨 사례(2026-07-03
         감사) 재발 차단. koipa_case_based 등 SYNTHETIC_PROXY_SOURCES는 항상 False.
      2) 텍스트가 손작성 합성 시나리오(source=SYNTHETIC_TEXT_SOURCE)면 라벨 권위가 정당해도
         False — 지정고시가 TS '라벨'을 정당화해도 '본문'이 합성이면 real-text 실측 정확도로
         인용할 수 없다(텍스처 갭, capstone F1 0.26). 라벨 floor 역할은 tier_of가 legal_floor로
         별도 유지하되 '실측 인용 가능' 버킷에서는 제외(2026-07-04 감사).
    """
    src = record.get("label_source")
    if src not in EXTERNAL_AUTHORITY_SOURCES:
        return False
    if src == "nkt_designated" and not str(record.get("legal_reference") or "").strip():
        return False
    if str(record.get("source") or "") == SYNTHETIC_TEXT_SOURCE:
        return False
    return True


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


# locked_gold_eval이 '실(real) 평가'로 인정되는 등급별 최소 표본 (readiness 기준).
DEFAULT_MIN_LOCKED_PER_GRADE = 5
_DEFAULT_GRADES = ("TS", "S1", "S2", "S3")


def eval_readiness(
    records: Sequence[dict],
    *,
    min_per_grade: int = DEFAULT_MIN_LOCKED_PER_GRADE,
    grades: Sequence[str] = _DEFAULT_GRADES,
) -> dict:
    """locked_gold_eval이 '실(real) 평가'로 쓸 만큼 충분한가 — readiness 게이트(죽음의 나선 #4).

    locked tier만 인정한다(legal_floor·합성 holdout은 실평가 아님). 각 등급이 min_per_grade
    이상이어야 ready. 무실데이터 단계에선 locked가 비어 ready=False → deploy gate가 합성
    test.jsonl만 보는(실분포 오염 맹목) 상태에서 **자동 활성(오염 자동승격)을 막는 근거**가 된다.
    locked가 운영 사람서명으로 충분히 쌓이면 ready=True → 그때 자동 활성을 허용한다.

    반환: {ready, per_grade, missing, reason}. (순수 함수 — DB/파일 불요, 단위테스트 가능.)
    """
    locked, _ = eval_records(records, allow_floor_fallback=False)  # locked tier만
    per_grade = {g: 0 for g in grades}
    for r in locked:
        code = r.get("label") or r.get("expected_grade")
        if code in per_grade:
            per_grade[code] += 1
    missing = [g for g in grades if per_grade[g] < min_per_grade]
    ready = bool(locked) and not missing
    return {
        "ready": ready,
        "per_grade": per_grade,
        "missing": missing,
        "reason": (
            "ok" if ready
            else ("no_locked_records" if not locked else f"insufficient_per_grade:{missing}")
        ),
    }
