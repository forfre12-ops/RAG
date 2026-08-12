"""합성 골든 빌드 다층방어 liveness 감사 — 어느 방어 축이 실제로 작동했나(가시성).

배경: 합의 게이트(evaluate_consensus)는 5개 방어 축을 쓴다 — ①disagree(rule==llm)
②ts_downgrade_suspect ③low_agreement(self-consistency) ④production_suspect(shadow 교차검증)
⑤no_evidence. 그러나 축이 *설정상 꺼져* 있으면(예: shadow_model 미지정 → shadow_grade 전건
None) 그 축은 아무것도 못 잡는데도 빌드는 조용히 gold를 낸다("게이트 ON=가시성 ON" 위반).

이 모듈은 run 레코드(gold+review)에서 각 축이 **실제로 발화(fire)했는지**를 세어, 무음으로
꺼진 방어를 폭로한다. 순수 함수 — LLM/DB/파일 불요. build_synthetic_golden(meta 기록)과
analyze_golden_run(리포트)이 호출한다.

verdict:
  - full_multi_axis        : shadow 작동 + 그 외 ≥2축 발화 (권장)
  - shadow_disabled        : shadow 꺼짐(coverage 0)이나 그 외 ≥2축 발화 (부분방어 — shadow 켜기 권장)
  - single_axis            : 실질 1축만 (합의만) — silent-miss 위험 큼, 사람검토 강력 권고
"""
from __future__ import annotations

from typing import Sequence

# self-consistency가 '정보(=애매도 신호)'로 인정되는 하한(evaluate_consensus min_self_consistency 기본).
_SELF_CONSISTENCY_INFORMATIVE = 0.999  # 1.0 미만이 하나라도 있으면 신호가 실제로 변동했다는 뜻


def _flag_count(records: Sequence[dict], flag: str) -> int:
    return sum(1 for r in records if flag in (r.get("flags") or []))


def assess_defense_liveness(records: Sequence[dict]) -> dict:
    """gold+review 전체 레코드에서 방어 5축의 실제 발화/커버리지를 집계하고 verdict를 낸다.

    records: build_*.jsonl + uncertain_*.jsonl 합친 것(각 dict에 flags·shadow_grade·
             self_consistency·agreement·llm_grade). 순수 함수.
    """
    recs = list(records)
    n = len(recs)
    if n == 0:
        return {"n": 0, "verdict": "empty", "axes": {}, "recommendation": "레코드 없음"}

    # ④ shadow: 커버리지(교차검증 실행 비율) + 발화(불일치로 production_suspect)
    shadow_covered = [r for r in recs if r.get("shadow_grade") is not None]
    shadow_cov = len(shadow_covered) / n
    shadow_divergent = sum(
        1 for r in shadow_covered if r.get("shadow_grade") != r.get("llm_grade")
    )
    shadow_fired = _flag_count(recs, "production_suspect")

    # ③ self-consistency: 실제로 변동했나(정보성) + 발화(low_agreement)
    sc_vals = [r.get("self_consistency") for r in recs if r.get("self_consistency") is not None]
    sc_below1 = sum(1 for x in sc_vals if x < _SELF_CONSISTENCY_INFORMATIVE)
    sc_informative = sc_below1 > 0
    sc_fired = _flag_count(recs, "low_agreement")

    # ① 합의: disagree 발화(agreement False). 주 게이트.
    disagree_fired = sum(1 for r in recs if r.get("agreement") is False)

    # ② ts_downgrade
    ts_fired = _flag_count(recs, "ts_downgrade_suspect")

    # ⑤ evidence: no_evidence 발화(단 require_evidence=False면 비차단 — 참고용)
    ev_fired = _flag_count(recs, "no_evidence")

    axes = {
        "agreement": {"fired": disagree_fired, "active": disagree_fired > 0},
        "ts_downgrade": {"fired": ts_fired, "active": ts_fired > 0},
        "self_consistency": {
            "fired": sc_fired, "informative": sc_informative,
            "n_below_1": sc_below1, "active": sc_fired > 0 or sc_informative,
        },
        "shadow": {
            "coverage": round(shadow_cov, 3), "divergent": shadow_divergent,
            "fired": shadow_fired, "active": shadow_cov > 0,
        },
        "evidence_flag": {"fired": ev_fired, "active": ev_fired > 0, "note": "require_evidence=False면 비차단(참고)"},
    }

    shadow_active = axes["shadow"]["active"]
    # 차단력 있는 축(evidence는 합성모드서 비차단이라 제외) 중 발화한 것
    blocking_active = sum(
        1 for k in ("agreement", "ts_downgrade", "self_consistency") if axes[k]["active"]
    )
    total_active = blocking_active + (1 if shadow_active else 0)

    if shadow_active and blocking_active >= 2:
        verdict = "full_multi_axis"
        rec = "다층방어 정상 — shadow 포함 다축 발화."
    elif not shadow_active and blocking_active >= 2:
        verdict = "shadow_disabled"
        rec = ("shadow 교차검증이 꺼져 있음(coverage 0) — build_synthetic_golden에 "
               "--shadow-model 지정(예: 배포 Qwen)해 production_suspect 축 점등 권장. "
               "그 외 방어는 발화 중.")
    elif total_active <= 1:
        verdict = "single_axis"
        rec = ("실질 1축(합의)만 작동 — silent-miss(고등급 동시 하향) 위험 큼. "
               "shadow·self-consistency 점등 + 사람검토 전까지 이 gold를 학습에 쓰지 말 것.")
    else:
        verdict = "partial_multi_axis"
        rec = "일부 축만 발화 — 꺼진 축 점등 권장."

    return {
        "n": n,
        "verdict": verdict,
        "shadow_active": shadow_active,
        "blocking_axes_active": blocking_active,
        "axes": axes,
        "recommendation": rec,
    }
