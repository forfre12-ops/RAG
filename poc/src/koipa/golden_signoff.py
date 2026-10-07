"""골든 후보 → locked_gold_eval 사람 서명 승격 (P3 골격).

gold_candidate(자동 합의+근거 통과)를 **평가 정답**(locked_gold_eval)으로 올리는 유일한 경로 = 사람 서명.
규칙(2026-06-29 결정: 골든 평가정답도 단일 서명):
  - 단일 서명: 모든 등급(TS/S1 포함) 독립 지재원 reviewer 1인 서명이면 승격.
    [2026-08-06] 고등급 이중서명 옵션(dual_for_upper) 제거 — RFP 기능요구사항·RTM 어디에도
    2인 서명 요구가 없다(검색 0건). 요건 없는 선택지가 화면·문서에 남아 감리 스크루티니와
    문서↔코드 불일치(백서는 '이중서명'을 표준처럼 서술)를 만들고 있었다.
  - 머신/플레이스홀더 reviewer 거부(golden_tiers.is_human_reviewer 재사용).
  - 서명자 등급 불일치 → 거부(조정 필요).

골든 검수(지재원 관리자 권위)는 운영 교정 루프(회원사 confirm_service)와 **별개**다.
순수 함수 — DB/네트워크/시계 없음(signed_at은 서명 레코드가 들고 옴). 정본 미변경: 호출부가
locked 산출(promote_golden_candidates 등 별도 게이트)을 기록한다.

9월 실문서+실서명 도착 전 미리 세우는 골격: 실 Signoff 레코드가 들어오면 이 함수가 받는다.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from koipa.golden_tiers import TIER_LOCKED, is_human_reviewer, tier_of

UPPER_GRADES = ("TS", "S1")
_LABELS = ("TS", "S1", "S2", "S3")
LOCKED_LABEL_SOURCE = "human_review"
SIGNOFF_GATE_VERSION = "human_signoff_v1"


@dataclass
class Signoff:
    """지재원 관리자 1인의 골든 후보 서명. reviewer_id=실계정(머신/ai_assist/플레이스홀더 거부)."""

    doc_id: str
    reviewer_id: str
    grade: str
    signed_at: str = ""
    note: str = ""
    # [2026-08-22 최소구현] 검수자가 이 문서를 평가정답(locked_eval)으로 쓸지, 학습후보(train)로
    # 쓸지 표시. 기본값은 기존 동작과 동일(locked_eval) — 값 저장만 하고 tier_of()/train_records()
    # 로직 변경은 하지 않는다(그건 별도 작업). 값 자체가 사람 검수 시점의 의도를 기록해 두면,
    # 나중에 학습 편입 경로를 설계할 때 이 표시부터 다시 만들 필요가 없다.
    intended_use: str = "locked_eval"


@dataclass
class SignoffResult:
    locked: list[dict]
    rejected: list[dict]   # {doc_id, reason, grade}
    stats: dict


# 실문서 인테이크 표식(ICD/콘솔 업로드가 붙인다). 이 표식이 **없는** 레코드는 게이트
# 대상이 아니다 — 합성·공개코퍼스 파생 후보에는 반출 근거라는 개념이 없다.
_INTAKE_ORIGINS = frozenset({"public_real", "organization_real"})


def _provenance_ok(candidate: dict) -> bool:
    """실문서라면 원천 위치·사용 권한 근거가 둘 다 기록됐는가.

    ⚠ **[2026-08-31] 이것은 더 이상 게이트가 아니다.** 발주처(지재원) 지시로 승격 차단을
      걷었다 — 등급의 근거는 검수자의 판단이지 출처 칸이 아니다. 지금 이 술어의 쓰임은
      승격 레코드에 실을 `provenance_state` 하나뿐이고, 평가셋 구성 보고에서 "실문서 중
      출처를 못 적은 것이 몇 건인가"를 세는 데 쓴다.

    ⚠ **표식이 없으면 True 다.** 실측 2026-08-23: 기존 검수 전달본 5종
      (777·200·120·106·120건)에는 document_origin 도 provenance 도 없다. 표식이 없는 것을
      "출처 없음"으로 세면 합성 후보까지 미기록으로 잡혀 집계가 부풀려진다.
    """
    origin = str(candidate.get("document_origin") or "")
    if origin not in _INTAKE_ORIGINS:
        return True
    prov = candidate.get("provenance")
    if not isinstance(prov, dict):
        return False
    return bool(
        str(prov.get("source_reference") or "").strip()
        and str(prov.get("authorization_basis") or "").strip()
    )


def _evaluate(signoffs: list[Signoff]):
    """서명 묶음을 평가 → (확정등급|None, 사유, 독립reviewer목록)."""
    if not signoffs:
        return None, "no_signoff", []
    human = [s for s in signoffs if is_human_reviewer(s.reviewer_id)]
    if not human:
        return None, "machine_reviewer", []
    grades = {s.grade for s in human}
    if len(grades) != 1:
        return None, "grade_disagree", []
    grade = next(iter(grades))
    if grade not in _LABELS:
        return None, "invalid_grade", []
    reviewers = sorted({s.reviewer_id for s in human})
    if not reviewers:
        return None, "insufficient_reviewers(need 1, got 0)", reviewers
    return grade, "ok", reviewers


def promote_to_locked(
    candidates: list[dict],
    signoffs: list[Signoff],
) -> SignoffResult:
    """gold_candidate를 사람 서명으로 locked_gold_eval로 승격(순수 함수, 정본 미변경).

    단일 서명: 모든 등급이 독립 reviewer 1인이면 승격.
    머신/불일치/미서명/등급외는 거부. 승격 레코드는 label_source=human_review·tier=locked_gold_eval.
    """
    by_doc: dict[str, list[Signoff]] = defaultdict(list)
    for s in signoffs:
        by_doc[s.doc_id].append(s)

    locked: list[dict] = []
    rejected: list[dict] = []
    reason_counts: Counter = Counter()

    for c in candidates:
        doc_id = c.get("doc_id")
        sl = by_doc.get(doc_id, [])
        grade, reason, reviewers = _evaluate(sl)
        # [2026-08-31] 출처 근거는 승격을 **막지 않는다** — 발주처(지재원) 지시.
        # 등급 확정 게이트(decide)를 걷으면서 여기만 남기면 검수는 통과하고 승격에서
        # 한꺼번에 막힌다. 대신 승격 레코드에 출처 상태를 실어 평가셋 구성 보고에서
        # "출처 미기록 몇 건"을 그대로 셀 수 있게 한다.
        provenance_state = "recorded" if _provenance_ok(c) else "missing"
        if grade is not None:
            rec = dict(c)
            rec.update(
                label=grade,
                label_source=LOCKED_LABEL_SOURCE,
                review_status="accepted",
                reviewer_id=reviewers[0],
                reviewer_ids=reviewers,
                signed_at=max((s.signed_at for s in sl if s.signed_at), default=""),
                gate_version=SIGNOFF_GATE_VERSION,
                tier=TIER_LOCKED,
                note=next((s.note for s in sl if s.note), ""),  # [#3] 검수자 메모 영속(사후 감사·이의제기 근거)
                # [2026-08-22 최소구현] 표시만 영속화 — tier는 여전히 TIER_LOCKED 그대로다.
                intended_use=next((s.intended_use for s in sl if s.intended_use), "locked_eval"),
                # [2026-08-31] 승격을 막지는 않되 출처 상태는 레코드에 남긴다 —
                # 평가셋 구성 보고에서 실문서 출처 미기록 건수를 셀 수 있어야 한다.
                provenance_state=provenance_state,
            )
            locked.append(rec)
        else:
            rejected.append({
                "doc_id": doc_id, "reason": reason, "grade": c.get("label"),
                "note": next((s.note for s in sl if s.note), ""),  # [#3] 거부 메모 영속
            })
            reason_counts[reason.split("(")[0]] += 1

    stats = {
        "candidates": len(candidates),
        "locked": len(locked),
        "rejected": len(rejected),
        "locked_by_grade": dict(Counter(r["label"] for r in locked)),
        "rejected_reasons": dict(reason_counts),
    }
    return SignoffResult(locked=locked, rejected=rejected, stats=stats)


def merge_locked_records(
    existing: list[dict], new_locked: list[dict]
) -> list[dict]:
    """published locked_gold_eval 셋에 새 서명 승격분을 doc_id 기준 dedup 병합(순수 함수).

    사람 서명 승격은 여러 검수 세션에 걸쳐 **누적**된다(config locked_eval_jsonl 주석:
    "파일이 쌓이면 readiness가 자동으로 켜진다"). 같은 doc_id 재승격은 최신(new)으로 대체하고,
    삽입 위치는 안정적으로 유지(결정적). locked tier(label_source=human_review·실계정 reviewer)가
    아닌 레코드는 방어적으로 배제 — 읽기 경로(eval_readiness)가 locked만 인정하므로, 병합 단계에서
    비-locked 오염이 파일에 섞여 들어가는 것을 원천 차단한다.

    이 함수가 last-mile의 핵심: promote_to_locked가 산출한 locked 레코드를 실제 읽기 경로
    파일에 누적시켜, locked_eval_readiness / deploy gate가 사람 서명을 실제로 '보게' 만든다.
    """
    merged: dict[str, dict] = {}
    for r in list(existing) + list(new_locked):
        if tier_of(r) != TIER_LOCKED:
            continue
        doc_id = r.get("doc_id")
        if doc_id is None:
            continue
        merged[doc_id] = r
    return list(merged.values())
