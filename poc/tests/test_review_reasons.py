"""검수 사유 집계가 **상태를 정한 게이트**만 세는지 고정한다.

배경(실측 2026-08-22). 서빙 측정이 사유를 `warnings` 를 ':' 로 잘라 세는 방식이라
hardened42 42건 집계에서 `persistence skipped` 가 15건으로 1위처럼 나왔다. 그건 doc_id 가
비-UUID 라 DB 에 안 남겼다는 기록 경고이지 상태 판정과 무관하다. 사유 분포는 "무엇을 고쳐야
자동확정이 오르나"를 정하는 표라, 1위가 무관한 값이면 판단이 틀린다.
정정 후 같은 42건: {low-confidence 13, agreement-gate 2} = needs_review 15건과 정확히 일치.
"""

from __future__ import annotations

from pathlib import Path

from koipa.services.review_reasons import (
    REVIEW_GATES,
    UNMAPPED,
    causal_review_reason,
    count_causal_reasons,
    gate_hits,
)

_PERSIST = "persistence skipped: doc_id='tsweep-0000' is not a UUID"
_LOWCONF = "low-confidence: confidence=0.69 < 0.70 — review recommended"


def test_persistence_warning_is_not_a_review_reason():
    """기록 경고만 붙은 needs_review 는 사유가 아니라 UNMAPPED 로 드러난다."""
    assert causal_review_reason([_PERSIST], "needs_review") == UNMAPPED
    assert gate_hits([_PERSIST]) == []


def test_real_gate_wins_over_persistence_noise():
    reasons = count_causal_reasons([
        {"status": "needs_review", "warnings": [_LOWCONF, _PERSIST]},
        {"status": "needs_review", "warnings": [_PERSIST, _LOWCONF]},
    ])
    assert reasons == {"low-confidence": 2}


def test_first_gate_in_evaluation_order_is_the_cause():
    """classify_service 는 앞 게이트가 걸리면 뒤 게이트를 아예 평가하지 않는다."""
    warnings = [
        "cap-conflict: public-source cap overrode a high content grade",
        _LOWCONF,
    ]
    # 표 순서상 low-confidence(:341) 가 cap-conflict(:361) 보다 앞이다.
    assert causal_review_reason(warnings, "needs_review") == "low-confidence"
    assert gate_hits(warnings) == ["low-confidence", "cap-conflict"]


def test_icd_gate_needs_both_markers():
    """ICD 규약 밖 값이라도 **상향 게이트 입력**이 아니면 라우팅하지 않는다."""
    only_unknown = ["icd-metadata-unknown: source_type='web' 은 ICD §3 규약값이 아니다"]
    assert gate_hits(only_unknown) == []

    fnr_risk = [
        "icd-metadata-unknown: security_marking='기밀' 은 ICD §3 규약값이 아니다"
        " — 상향 게이트 입력이라 미탐 위험"
    ]
    assert causal_review_reason(fnr_risk, "needs_review") == "icd-metadata-fnr-risk"


def test_auto_confirmed_document_has_no_reason():
    """staging 인 문서에 사유를 붙이면 사유 합계가 needs_review 건수를 넘는다."""
    assert causal_review_reason([_LOWCONF, _PERSIST], "staging") is None
    assert count_causal_reasons([{"status": "staging", "warnings": [_PERSIST]}]) == {}


def test_reason_count_is_one_per_document():
    records = [
        {"status": "needs_review", "warnings": [_LOWCONF, "agreement-gate: model=S1 vs rule=S2"]},
        {"status": "needs_review", "warnings": ["agreement-gate: model=S1 vs rule=S2"]},
        {"status": "staging", "warnings": []},
    ]
    reasons = count_causal_reasons(records)
    assert sum(reasons.values()) == 2          # needs_review 건수와 같다
    assert reasons == {"agreement-gate": 1, "low-confidence": 1}


def test_every_gate_marker_still_exists_in_source():
    """표식이 코드와 어긋나면(게이트 개명·삭제) 사유 집계가 조용히 UNMAPPED 로 샌다.

    그때 사유표는 "원인 없음"으로 보이는데 실제로는 검수가 늘어난 상태다 - 여기서 먼저 깬다.
    """
    src_dir = Path(__file__).resolve().parent.parent / "src" / "koipa"
    sources = "\n".join(
        (src_dir / rel).read_text("utf-8")
        for rel in (
            "services/classify_service.py",
            "modules/m5_inference/pipeline.py",
            "modules/m3_labeling/rule_engine.py",
        )
    )
    missing = [
        (tag, marker)
        for tag, markers in REVIEW_GATES
        for marker in markers
        if marker not in sources
    ]
    assert not missing, f"표식이 소스에서 사라졌다(게이트 개명?): {missing}"
