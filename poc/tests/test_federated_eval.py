"""연합평가 집계 — context 층화·방향별 오류·보정·반출 스칼라 불변식(유출 0) 테스트."""
from __future__ import annotations

import pytest

from koipa.modules.m6_evaluation.federated_eval import (
    CTX_ESCALATED_PROXY,
    CTX_HIGH_CONF_PROXY,
    CTX_RANDOM_AUDIT,
    CTX_UNKNOWN,
    EvalPair,
    assert_export_safe,
    build_federated_report,
    redact_for_export,
)


def _pairs(spec, ctx=CTX_RANDOM_AUDIT, conf=0.9):
    """spec=[(true, pred, count), ...] → EvalPair 리스트."""
    out = []
    for true, pred, cnt in spec:
        out += [EvalPair(model_pred=pred, human_truth=true, confidence=conf, review_context=ctx)
                for _ in range(cnt)]
    return out


def test_underclass_and_overclass_directions():
    # S1 정답: S2/S3 예측=under(미탐), TS 예측=over(과분류), S1=correct
    pairs = _pairs([("S1", "S3", 2), ("S1", "TS", 1), ("S1", "S1", 7)])
    rep = build_federated_report(pairs, min_n=1)
    card = next(c for c in rep["by_context"][CTX_RANDOM_AUDIT]["cards"] if c["grade"] == "S1")
    assert card["n"] == 10
    assert abs(card["underclass_fnr"] - 0.2) < 1e-9    # 2/10 under
    assert abs(card["overclass_rate"] - 0.1) < 1e-9    # 1/10 over
    assert abs(card["recall"] - 0.7) < 1e-9


def test_context_stratification_no_single_headline():
    pairs = (_pairs([("S1", "S3", 5)], ctx=CTX_ESCALATED_PROXY)
             + _pairs([("S1", "S1", 5)], ctx=CTX_RANDOM_AUDIT))
    rep = build_federated_report(pairs, min_n=1)
    assert set(rep["by_context"]) == {CTX_ESCALATED_PROXY, CTX_RANDOM_AUDIT}
    # escalated는 FNR 1.0, audit는 0.0 — 섞이지 않는다(단일 헤드라인 없음)
    esc = next(c for c in rep["by_context"][CTX_ESCALATED_PROXY]["cards"] if c["grade"] == "S1")
    aud = next(c for c in rep["by_context"][CTX_RANDOM_AUDIT]["cards"] if c["grade"] == "S1")
    assert esc["underclass_fnr"] == 1.0 and aud["underclass_fnr"] == 0.0
    assert "population" not in rep  # 단일 population 숫자 필드 없음
    assert "쏠림" in rep["by_context"][CTX_ESCALATED_PROXY]["caveat"]


def test_confidence_proxy_never_labeled_unbiased():
    # 적대검증 B: 신뢰도 프록시 고신뢰 층은 random_audit(무편향)이 아니어야 한다.
    proxy = _pairs([("S1", "S1", 3)], ctx=CTX_HIGH_CONF_PROXY)
    rep = build_federated_report(proxy, min_n=1)
    body = rep["by_context"][CTX_HIGH_CONF_PROXY]
    assert body["context_source"] == "confidence_proxy"
    assert "무편향" not in body["caveat"] or "금지" in body["caveat"]  # 무편향 단정 없음
    assert "감사 아님" in body["caveat"] or "무편향 성능 인용 금지" in body["caveat"]
    # random_audit만 무편향 자격 + explicit source
    aud = build_federated_report(_pairs([("S1", "S1", 3)], ctx=CTX_RANDOM_AUDIT), min_n=1)
    ab = aud["by_context"][CTX_RANDOM_AUDIT]
    assert ab["context_source"] == "explicit_or_declared"
    assert "무편향 추정에 가장 가깝다" in ab["caveat"]
    # generated_note가 confidence_proxy를 무편향으로 인용 금지라고 명시
    assert "무편향 아님" in rep["generated_note"] or "인용 금지" in rep["generated_note"]


def test_small_sample_inconclusive_fail_secure():
    pairs = _pairs([("S1", "S3", 2), ("S1", "S1", 3)])  # n=5 < min_n
    rep = build_federated_report(pairs, min_n=30)
    card = next(c for c in rep["by_context"][CTX_RANDOM_AUDIT]["cards"] if c["grade"] == "S1")
    assert card["verdict"] == "INCONCLUSIVE" and "표본 부족" in card["cannot_measure"]


def test_lowest_grade_na_prevents_all_s3_false_pass():
    pairs = _pairs([("S3", "S3", 50)])
    rep = build_federated_report(pairs, min_n=1)
    card = next(c for c in rep["by_context"][CTX_RANDOM_AUDIT]["cards"] if c["grade"] == "S3")
    assert card["verdict"] == "N/A"  # 최저등급 under-class 미정의 — PASS로 안 셈


def test_calibration_overconfidence_gap():
    # 신뢰도 0.9인데 실제 정확도 0.5 → gap +0.4(과신)
    pairs = _pairs([("S1", "S1", 5), ("S1", "S3", 5)], conf=0.9)
    rep = build_federated_report(pairs, min_n=1)
    bins = rep["by_context"][CTX_RANDOM_AUDIT]["calibration_bins"]
    hi = next(b for b in bins if b["mean_confidence"] >= 0.8)
    assert abs(hi["empirical_accuracy"] - 0.5) < 1e-9
    assert hi["gap"] > 0.3  # 과신 신호


def test_export_redaction_is_scalar_only():
    pairs = _pairs([("S1", "S3", 3)])
    rep = build_federated_report(pairs, min_n=1)
    payload = redact_for_export(rep)
    # 화이트리스트 밖 최상위 키 제거됨(generated_note는 허용, by_context는 축소)
    assert "by_context" in payload and payload["export_contract"].startswith("SCALAR-ONLY")
    for body in payload["by_context"].values():
        assert set(body) <= {"n", "caveat", "context_source", "confusion_counts",
                             "cards", "calibration_bins"}
    assert_export_safe(payload)  # 재검증 통과


def test_export_safe_rejects_injected_identifier():
    pairs = _pairs([("S1", "S3", 3)])
    payload = redact_for_export(build_federated_report(pairs, min_n=1))
    # 누군가 문서/신원 필드를 끼워넣으면 fail-closed
    bad = {**payload, "doc_ids": ["a", "b"]}
    with pytest.raises(AssertionError):
        assert_export_safe(bad)
    bad2 = {**payload}
    bad2["by_context"] = {CTX_UNKNOWN: {"reviewer_id": "kim", "n": 3}}
    with pytest.raises(AssertionError):
        assert_export_safe(bad2)


def test_export_safe_is_depth_complete():
    # 적대검증 A: depth≥3(cards/confusion_counts/bins 내부)에 식별키가 끼면 fail-closed.
    import copy
    base = redact_for_export(build_federated_report(_pairs([("S1", "S3", 3)]), min_n=1))
    ctx = next(iter(base["by_context"]))
    # cards 항목에 검수자 신원 주입
    p1 = copy.deepcopy(base)
    p1["by_context"][ctx]["cards"][0]["leaked_reviewer"] = "kim"
    with pytest.raises(AssertionError):
        assert_export_safe(p1)
    # confusion_counts에 등급 아닌 키(doc_id) 주입
    p2 = copy.deepcopy(base)
    p2["by_context"][ctx]["confusion_counts"]["doc-uuid-123"] = {"S1": 1}
    with pytest.raises(AssertionError):
        assert_export_safe(p2)
    # calibration_bins 항목에 자유텍스트 키 주입
    p3 = copy.deepcopy(base)
    if not p3["by_context"][ctx]["calibration_bins"]:
        p3["by_context"][ctx]["calibration_bins"] = [{"bin": "[0.8,1.0)", "n": 3}]
    p3["by_context"][ctx]["calibration_bins"][0]["reason_text"] = "유출된 사유"
    with pytest.raises(AssertionError):
        assert_export_safe(p3)


def test_unknown_context_normalized_and_caveated():
    pairs = [EvalPair("S1", "S3", 0.8, review_context="weird_value")]
    rep = build_federated_report(pairs, min_n=1)
    assert CTX_UNKNOWN in rep["by_context"]
    assert "단독 성능 인용 금지" in rep["by_context"][CTX_UNKNOWN]["caveat"]


def test_cli_rows_to_pairs_admission_and_context_proxy():
    # CLI 순수 매핑 헬퍼: 머신 검수자 제외 + 신뢰도 프록시 층화(DB 불요).
    from scripts.federated_eval_report import rows_to_pairs

    rows = [
        {"model_pred": "S3", "human_truth": "S1", "confidence": 0.6, "corrected_by": "admin_kim"},
        {"model_pred": "S1", "human_truth": "S1", "confidence": 0.95, "corrected_by": "reviewer_lee"},
        {"model_pred": "S3", "human_truth": "TS", "confidence": 0.9, "corrected_by": "llm_judge_primary"},
        {"model_pred": "", "human_truth": "S1", "confidence": 0.9, "corrected_by": "admin_kim"},
    ]
    pairs = rows_to_pairs(rows, escalation_tau=0.8)
    # 머신 검수자(llm_judge)·빈 등급 제외 → 2건
    assert len(pairs) == 2
    ctx = {(p.model_pred, p.human_truth): p.review_context for p in pairs}
    assert ctx[("S3", "S1")] == CTX_ESCALATED_PROXY   # conf 0.6 < 0.8
    # 고신뢰 검수분은 프록시 층(무편향 random_audit 아님)
    assert ctx[("S1", "S1")] == CTX_HIGH_CONF_PROXY   # conf 0.95 >= 0.8
    # τ 미지정이면 전부 unknown
    pairs2 = rows_to_pairs(rows[:1], escalation_tau=None)
    assert pairs2[0].review_context == CTX_UNKNOWN


def test_cli_audit_manifest_overrides_to_unbiased():
    # 감사 매니페스트 doc_id는 신뢰도 프록시를 이기고 random_audit(무편향)으로 라벨된다.
    from scripts.federated_eval_report import rows_to_pairs

    rows = [
        {"model_pred": "S3", "human_truth": "S1", "confidence": 0.6,
         "corrected_by": "admin_kim", "doc_id": "doc-audit-1"},   # 감사 대상(conf 낮아도 audit 우선)
        {"model_pred": "S1", "human_truth": "S1", "confidence": 0.95,
         "corrected_by": "admin_kim", "doc_id": "doc-normal-2"},  # 감사 아님 → 고신뢰 프록시
    ]
    pairs = rows_to_pairs(rows, escalation_tau=0.8, audit_doc_ids=frozenset({"doc-audit-1"}))
    ctx = {(p.model_pred, p.human_truth): p.review_context for p in pairs}
    assert ctx[("S3", "S1")] == CTX_RANDOM_AUDIT          # 매니페스트 우선(무편향)
    assert ctx[("S1", "S1")] == CTX_HIGH_CONF_PROXY        # 비-감사 → 프록시(편향)
    # doc_id는 EvalPair에 실리지 않는다(구조적 무유출)
    assert not any(hasattr(p, "doc_id") for p in pairs)


def test_load_audit_manifest_formats(tmp_path):
    from scripts.federated_eval_report import load_audit_manifest

    f = tmp_path / "audit.jsonl"
    f.write_text('{"doc_id": "d1"}\n# comment\nd2\n{"doc_id": "d3", "extra": 1}\n\n',
                 encoding="utf-8")
    ids = load_audit_manifest(str(f))
    assert ids == frozenset({"d1", "d2", "d3"})
