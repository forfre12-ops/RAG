"""Exact evidence-card construction tests."""
from __future__ import annotations

import pytest

from koipa.proxy_evidence import ProxyEvidenceError, build_evidence_card


def _record(label: str = "S1") -> dict:
    scores = {
        "TS": {"secrecy": 2, "value": 2, "management": 2},
        "S1": {"secrecy": 2, "value": 2, "management": 0},
        "S2": {"secrecy": 1, "value": 1, "management": 1},
        "S3": {"secrecy": 0, "value": 0, "management": 0},
    }[label]
    return {
        "label": label,
        "expected_factor_scores": scores,
        "text": (
            "전체 결합조건은 외부 자료만으로 알 수 없고 공개 설명에서는 상세 수치를 제외한다.\n\n"
            "경쟁사가 결과를 재현하면 개발기간 14주와 반복 시험 비용 3천만원을 줄일 수 있다.\n\n"
            "공용 저장소에는 권한 구분 없이 보관되어 승인이나 반출 기록 절차가 마련되지 않았다."
        ),
        "evidence_card": {
            "nonpublicity": "핵심 결합조건은 외부에서 재구성 불가",
            "competitive_value": "개발기간과 시험비용 절감 가능",
            "access_controls": "객관적인 관리조치 없음",
        },
    }


def test_builds_exact_offsets_hashes_and_factor_levels():
    record = _record()
    card = build_evidence_card(record)
    assert card["schema"] == "proxy-evidence-v1"
    assert set(card["factors"]) == {
        "nonpublicity", "competitive_value", "access_controls"
    }
    for factor in card["factors"].values():
        span = factor["spans"][0]
        assert record["text"][span["start"]:span["end"]] == span["quote"]
        assert len(span["quote_sha256"]) == 64
    assert card["factors"]["access_controls"]["expected_level"] == 0


def test_rejects_factor_label_mismatch():
    record = _record("S1")
    record["expected_factor_scores"]["management"] = 2
    with pytest.raises(ProxyEvidenceError, match="derive TS"):
        build_evidence_card(record)


def test_public_s3_counterfactual_has_three_distinct_text_backed_factors():
    record = _record("S3")
    record["text"] = (
        "전체 내용은 공식 웹페이지에 이미 공개되어 누구나 동일한 자료를 열람할 수 있다.\n\n"
        "독점적 방법이 없어 추가 복제로 개발 비용 절감이나 새로운 경쟁상 피해가 발생하지 않는다.\n\n"
        "로그인이나 승인 권한 없이 다운로드할 수 있고 열람 기록과 반출 통제 절차를 적용하지 않는다."
    )

    card = build_evidence_card(record)

    spans = [
        factor["spans"][0]
        for factor in card["factors"].values()
    ]
    assert {factor["expected_level"] for factor in card["factors"].values()} == {0}
    assert len({(span["start"], span["end"]) for span in spans}) == 3
    assert all(
        record["text"][span["start"]:span["end"]] == span["quote"]
        for span in spans
    )


def test_rejects_missing_competitive_text_evidence():
    record = _record()
    record["text"] = record["text"].replace(
        "경쟁사가 결과를 재현하면 개발기간 14주와 반복 시험 비용 3천만원을 줄일 수 있다.",
        "관찰 결과는 별도 표에 정리했다.",
    )
    with pytest.raises(ProxyEvidenceError, match="competitive_value"):
        build_evidence_card(record)


def test_rejects_direct_grade_marker_as_evidence():
    record = _record()
    record["text"] = (
        "S1 등급은 외부 공개가 제한된다.\n\n"
        "경쟁사가 결과를 재현하면 개발기간 14주와 반복 시험 비용 3천만원을 줄일 수 있다.\n\n"
        "공용 저장소에는 권한 구분 없이 보관되어 승인이나 반출 기록 절차가 마련되지 않았다."
    )
    with pytest.raises(ProxyEvidenceError, match="nonpublicity"):
        build_evidence_card(record)
