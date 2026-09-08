"""콘솔 검수 결정 → 사람 서명(locked_gold_eval) 승격 — **끊겨 있던 두 경로를 잇는 다리.**

왜 이 모듈이 있는가(2026-09-09 실측). 서명 경로가 두 벌인데 서로 이어져 있지 않았다.

    경로 A  POST /golden/jobs/{id}/signoff → apply_signoff → promote_to_locked
            → label_source=human_review → tier_of → locked_gold_eval
            대상 = datasets/golden_runs/*/build_*.jsonl

    경로 B  콘솔 후보 화면 → ProxyGoldCandidateService.decide()
            → candidate_decisions.jsonl 에 이벤트 append
            **label_source·review_status 를 쓰지 않는다.** 원장을 읽는 코드는 콘솔
            자기 화면 표시뿐이었다(전수 grep: tier 로 넘기는 코드 0건).

그래서 콘솔에서 등급을 확정해도 평가정답은 한 건도 생기지 않았다. 대상도 겹치지 않는다 —
콘솔 후보 1,067건 중 tier 코퍼스에 존재하는 것은 67건(6.3%)뿐이고 나머지 1,000건은
tier 레코드 자체가 없어 서명해도 tier_of 가 볼 행이 없었다.

이 모듈은 **원장을 투영**한다. decide() 의 쓰기 경로는 건드리지 않는다:

  · 이미 쌓인 결정도 소급 승격된다(223 에 남아 있는 검수 이력이 그대로 살아난다).
  · 여러 번 돌려도 결과가 같다(doc_id dedup 병합 — merge_locked_records 와 같은 규율).
  · 결정 시점의 신원·시각이 그대로 서명이 된다. 승격을 실행한 관리자가 아니라
    **결정을 내린 검수자**가 reviewer_id 다 — 머신·플레이스홀더는 promote_to_locked
    내부 is_human_reviewer 가 거부한다.

콘솔 화면이 스스로 적어 둔 계약과 같다 — "여기서 확정해도 평가 정답지로 승격되지는
않습니다. 승격은 사람 서명을 거치는 별도 절차입니다."(api/golden.py 관리 화면). 그 별도
절차가 없었을 뿐이다.
"""
from __future__ import annotations

from typing import Any, Iterable

from koipa.golden_signoff import Signoff

# 등급을 확정한 결정만 서명이 된다. 보류·폐기·재검토·범위밖은 등급을 확정하지 않으므로
# (decide() 가 그 전이에서 final_grade 를 비운다) 승격 대상이 아니다.
GRADE_CONFIRMING_STATUSES = frozenset({"approved_proxy", "grade_fixed_unlocked"})

# locked 레코드로 실어 보낼 후보 필드.
#
# 콘솔 행을 통째로 넘기지 않는 이유: promote_to_locked 가 `rec = dict(c)` 로 후보를 그대로
# 복사한 뒤 서명 필드만 덮는다. latest_decision·grade_fixed 같은 원장 부산물이 평가정답
# 레코드에 섞여 들어가면, 나중에 그 파일을 읽는 쪽이 그것을 정답의 근거로 읽는다.
#
# text 는 반드시 싣는다 — locked jsonl 이 평가면 그 자체다(기존 locked 레코드도 text 를
# 들고 있다). 본문 없는 평가정답은 평가에 쓸 수 없다.
#
# document_origin 은 **콘솔 어휘 그대로** 넘긴다(organization_real 포함). golden_tiers 의
# _ORIGIN_ALIASES 가 정본 어휘로 옮기고, golden_signoff._provenance_ok 는 콘솔 어휘를
# 그대로 보므로 양쪽이 다 성립한다. 여기서 미리 바꾸면 _provenance_ok 가 조직 실문서를
# 출처 기록 대상에서 놓친다.
_CARRIED_FIELDS = (
    "doc_id",
    "text",
    "title",
    "document_origin",
    "claim_scope",
    "document_sha256",
    "document_path",
    "content_revision",
    "review_batch",
    "provenance",
    "requires_manual_audit",
    "management",
)


def _decision_of(candidate: dict[str, Any]) -> dict[str, Any]:
    return candidate.get("latest_decision") or {}


def is_promotable(candidate: dict[str, Any]) -> bool:
    """이 후보의 최신 결정이 사람 서명이 될 수 있는가.

    등급 확정 + 확정 등급 + 결정자 신원 + 결정 시각이 모두 있어야 한다. 넷 중 하나라도
    없으면 승격하지 않는다 — 서명 envelope 는 promote_to_locked 가 스탬프하지만, 그 재료가
    비어 있으면 is_valid_signoff 를 못 넘어 held_review 로 떨어진다(조용한 실패).
    """
    decision = _decision_of(candidate)
    return bool(
        candidate.get("status") in GRADE_CONFIRMING_STATUSES
        and candidate.get("final_grade")
        and str(decision.get("actor_id") or "").strip()
        and str(decision.get("decided_at") or "").strip()
    )


def build_promotion_inputs(
    candidates: Iterable[dict[str, Any]],
) -> "tuple[list[dict], list[Signoff]]":
    """콘솔 후보 행 → (promote_to_locked 후보 레코드, 서명 목록). 순수 함수.

    승격 가능한 것만 골라 짝을 맞춘 채 돌려준다 — promote_to_locked 는 서명이 없는 후보를
    rejected(no_signoff) 로 세므로, 미결정 후보를 함께 넘기면 "거부 1,000건" 이라는 잡음이
    난다(apply_signoff 가 decided_ids 로 좁히는 것과 같은 이유).
    """
    records: list[dict] = []
    signoffs: list[Signoff] = []
    for candidate in candidates:
        if not is_promotable(candidate):
            continue
        decision = _decision_of(candidate)
        grade = str(candidate["final_grade"])
        record = {k: candidate[k] for k in _CARRIED_FIELDS if k in candidate}
        record["label"] = grade
        # 등급이 있는 곳이 두 자리가 되지 않게 한다 — promote_to_locked 가 label 을 다시
        # 덮고, 읽는 쪽은 label 만 본다.
        record["source"] = str(candidate.get("document_origin") or "unknown")
        # 이 평가정답이 **어느 결정에서 왔는지** 되짚을 수 있어야 한다. 원장은 append-only
        # 라 event_id 하나로 사유·이전 M 값까지 전부 찾아갈 수 있다.
        record["console_decision_event_id"] = decision.get("event_id")
        record["console_decision_action"] = decision.get("action")
        records.append(record)
        signoffs.append(
            Signoff(
                doc_id=str(candidate["doc_id"]),
                reviewer_id=str(decision["actor_id"]),
                grade=grade,
                signed_at=str(decision["decided_at"]),
                note=str(decision.get("reason") or ""),
            )
        )
    return records, signoffs
