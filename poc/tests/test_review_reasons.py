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
    REVIEW_GATE_TAGS,
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
            "api/documents.py",   # 업로드 경로 전용 추출 검수게이트
        )
    )
    missing = [
        (tag, marker)
        for tag, markers in REVIEW_GATES
        for marker in markers
        if marker not in sources
    ]
    assert not missing, f"표식이 소스에서 사라졌다(게이트 개명?): {missing}"


def test_upload_path_extraction_gate_is_mapped():
    """업로드(analyze) 경로의 추출 검수게이트도 사유로 잡힌다.

    실측 2026-08-22: 시연 문서 05_S1_tech_transfer.pdf 가 표 추출 불완전으로 검수행인데
    표에 없어 unmapped 로 나왔다. classify_service 게이트가 아니라 그 뒤에 오는 경로라
    표의 맨 끝에 둔다 - 앞 게이트가 이미 걸렸으면 그쪽이 원인이다.
    """
    warn = "extraction_gate: 열화 추출(표누락/OCR/저품질)→검수 라우팅 (table_incomplete)"
    assert causal_review_reason([warn], "needs_review") == "extraction-gate"
    assert causal_review_reason([_LOWCONF, warn], "needs_review") == "low-confidence"


def test_every_gate_tag_has_korean_text():
    """화면 사유표(review_reason_ko.js)가 서버 게이트 태그를 전부 덮는지 고정한다.

    왜 필요한가(2026-08-24). 관리자 콘솔의 「근거」 패널은 warnings 원문이 아니라 서버가
    하나로 줄여 준 태그(automation_assessment.causal_review_reason)를 받는다. 종전에는 그
    태그를 'agreement-gate' 처럼 **영문 그대로** 화면에 찍었다. 한글표를 화면 쪽에 두면
    게이트를 추가·개명했을 때 조용히 뒤처져 다시 영문이 뜬다 — 여기서 먼저 깨뜨린다.
    """
    js = (
        Path(__file__).resolve().parent.parent
        / "src" / "koipa" / "api" / "static" / "review_reason_ko.js"
    ).read_text("utf-8")
    table = js.split("var TAG_TEXT = {", 1)[1].split("};", 1)[0]
    missing = [t for t in REVIEW_GATE_TAGS if f'"{t}":' not in table]
    assert not missing, f"화면 사유표에 한글 문구가 없는 게이트: {missing}"
    assert f'"{UNMAPPED}":' in table, "unmapped 도 사람 말로 옮겨 둬야 한다"


# ── metadata-management-underclass (2026-09-22) ───────────────────────────────
# 실측: 배포 프로필 + typed_facts metadata 주입 154건(reports/mock_final_train_20260921/
# serving_v2_FS_s42_facts.json) 에서 검수 105건 중 57건의 사유가 'unmapped' 였다. 그 57건의 경고가
# 아래 두 줄이다. classify_service.py:581-588 이 status 를 needs_review 로 올리는데 표에 없었다.
_UNDERCLASS_PIPELINE = (
    "metadata-management-underclass: 요소 (S,V,M=2) 의 정본 공식은 S1 인데 예측 S2"
    " → 검수 라우팅 (미탐 방향 · 등급 미변경)"
)
_UNDERCLASS_ROUTED = (
    "metadata-management-underclass: confirmed management level implies a higher"
    " grade than predicted — routed to human review (grade unchanged, FNR-safe)"
)
_MGMT_CONFLICT = (
    "metadata-management-conflict: proven-absent management vs non-public "
    "predicted grade — routed to human review"
)
_S2_UNDER = "s2-underclass-risk: internal/non-public signals with S3 prediction — routed to human review"
_ICD_FNR = (
    "icd-metadata-unknown: security_marking='기밀' 은 ICD §3 규약값이 아니다"
    " — 상향 게이트 입력이라 미탐 위험"
)


def test_underclass_warning_is_mapped_not_unmapped():
    """이 경고로 검수된 건은 사유가 'unmapped' 가 아니라 자기 태그여야 한다."""
    for warnings in ([_UNDERCLASS_ROUTED], [_UNDERCLASS_PIPELINE, _UNDERCLASS_ROUTED]):
        assert causal_review_reason(warnings, "needs_review") == "metadata-management-underclass"
        assert gate_hits(warnings) == ["metadata-management-underclass"]
    counted = count_causal_reasons([
        {"status": "needs_review", "warnings": [_UNDERCLASS_PIPELINE, _UNDERCLASS_ROUTED, _PERSIST]},
        {"status": "needs_review", "warnings": [_UNDERCLASS_ROUTED]},
    ])
    assert counted == {"metadata-management-underclass": 2}
    assert UNMAPPED not in counted


def test_underclass_tag_is_in_gate_tags_and_between_conflict_and_icd():
    tags = list(REVIEW_GATE_TAGS)
    assert "metadata-management-underclass" in tags
    assert (
        tags.index("metadata-management-conflict")
        < tags.index("metadata-management-underclass")
        < tags.index("icd-metadata-fnr-risk")
    )


def test_underclass_priority_follows_classify_evaluation_order():
    """앞 게이트가 걸리면 뒤 게이트는 평가되지 않는다 — 사유는 표 순서상 첫 게이트."""
    # 앞선 게이트(low-confidence · metadata-management-conflict)가 함께 있으면 그쪽이 원인이다.
    assert causal_review_reason([_UNDERCLASS_ROUTED, _LOWCONF], "needs_review") == "low-confidence"
    assert causal_review_reason(
        [_UNDERCLASS_ROUTED, _MGMT_CONFLICT], "needs_review"
    ) == "metadata-management-conflict"
    # 뒤에 오는 게이트(icd · s2-underclass-risk · agreement-gate)가 함께 있으면 underclass 가 원인이다.
    later = [_ICD_FNR, _S2_UNDER, "agreement-gate: model=S1 vs rule=S2"]
    assert causal_review_reason(later + [_UNDERCLASS_ROUTED], "needs_review") == (
        "metadata-management-underclass"
    )
    assert gate_hits(later + [_UNDERCLASS_ROUTED]) == [
        "metadata-management-underclass", "icd-metadata-fnr-risk",
        "s2-underclass-risk", "agreement-gate",
    ]


def test_underclass_table_position_matches_classify_source_order():
    """표에서의 자리가 classify() 소스의 게이트 평가 순서와 같다(conflict → underclass → icd)."""
    src = (
        Path(__file__).resolve().parent.parent / "src" / "koipa" / "services" / "classify_service.py"
    ).read_text("utf-8")
    pos = {
        "metadata-management-conflict": src.index('"metadata-management-conflict" in w'),
        "metadata-management-underclass": src.index('"metadata-management-underclass" in w'),
        "icd-metadata-fnr-risk": src.index('"미탐 위험" in w'),
    }
    by_source = sorted(pos, key=pos.get)
    by_table = sorted(pos, key=list(REVIEW_GATE_TAGS).index)
    assert by_source == by_table, f"소스 순서 {by_source} != 표 순서 {by_table}"


def test_underclass_reason_text_is_in_both_screen_tables():
    """화면 표 두 곳(태그 표 TAG_TEXT · 경고 원문 표 GATE_REASONS)에 모두 있어야 한다."""
    js = (
        Path(__file__).resolve().parent.parent / "src" / "koipa" / "api" / "static" / "review_reason_ko.js"
    ).read_text("utf-8")
    assert '"metadata-management-underclass":' in js.split("var TAG_TEXT = {", 1)[1].split("};", 1)[0]
    gate_reasons = js.split("var GATE_REASONS = [", 1)[1].split("\n  ];", 1)[0]
    assert "/metadata-management-underclass/" in gate_reasons
