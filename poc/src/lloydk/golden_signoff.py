"""골든 후보 → locked_gold_eval 사람 이중서명 승격 (P3 골격).

gold_candidate(자동 합의+근거 통과)를 **평가 정답**(locked_gold_eval)으로 올리는 유일한 경로 = 사람 서명.
규칙:
  - 고등급(TS/S1): ≥2 독립 지재원 reviewer가 같은 등급에 서명(이중서명) — 운영 경로
    (confirm_service._apply_dual_review_gate / distinct_reviewers_for_level)와 동일 강도.
  - S2/S3: 단일 서명 허용(불일치 비용 제한적).
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

from lloydk.golden_tiers import TIER_LOCKED, is_human_reviewer

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


@dataclass
class SignoffResult:
    locked: list[dict]
    rejected: list[dict]   # {doc_id, reason, grade}
    stats: dict


def _evaluate(signoffs: list[Signoff], *, dual_for_upper: bool):
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
    need = 2 if (dual_for_upper and grade in UPPER_GRADES) else 1
    if len(reviewers) < need:
        return None, f"insufficient_reviewers(need {need}, got {len(reviewers)})", reviewers
    return grade, "ok", reviewers


def promote_to_locked(
    candidates: list[dict],
    signoffs: list[Signoff],
    *,
    dual_for_upper: bool = True,
) -> SignoffResult:
    """gold_candidate를 사람 서명으로 locked_gold_eval로 승격(순수 함수, 정본 미변경).

    고등급(TS/S1)=≥2 독립 reviewer 합의, S2/S3=≥1. 머신/불일치/미서명/등급외는 거부.
    승격 레코드는 label_source=human_review·tier=locked_gold_eval가 되어 평가 정답으로 인정된다.
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
        grade, reason, reviewers = _evaluate(sl, dual_for_upper=dual_for_upper)
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
            )
            locked.append(rec)
        else:
            rejected.append({"doc_id": doc_id, "reason": reason, "grade": c.get("label")})
            reason_counts[reason.split("(")[0]] += 1

    stats = {
        "candidates": len(candidates),
        "locked": len(locked),
        "rejected": len(rejected),
        "locked_by_grade": dict(Counter(r["label"] for r in locked)),
        "rejected_reasons": dict(reason_counts),
    }
    return SignoffResult(locked=locked, rejected=rejected, stats=stats)
