"""build_review_assist: 공개 판결문 → S3, 비-판결문 애매 케이스 → FNR-safe 상향."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "build_review_assist", Path(__file__).resolve().parent.parent / "scripts" / "build_review_assist.py"
)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)


def test_public_ruling_recommends_s3_regardless_of_pseudo():
    row = {
        "doc_id": "r1",
        "model_label": "S3",
        "human_label": "S2",  # pseudo 오탐
        "text": "거절사정\n\n【주 문】 상고를 기각한다. 대법원 ... 심판청구인 소송대리인 변리사 ...",
    }
    a = ra.assess(row, {})
    assert a["recommend"] == "S3"
    assert a["reason_code"] == "public_court_ruling"
    assert a["triage"] in {"AUTO-CONFIRM", "QUICK-CHECK"}


def test_non_ruling_ambiguous_rounds_up_and_flags_review():
    row = {
        "doc_id": "n1",
        "model_label": "S3",
        "human_label": "S2",
        "text": "한국은행은 11월 금통위에서 기준금리를 인하했다. 당사는 동결을 예상했다 ...",
    }
    a = ra.assess(row, {})
    assert a["recommend"] == "S2"  # FNR-safe: S2가 S3보다 고위험
    assert a["triage"] == "REVIEW"


def test_more_severe_helper():
    assert ra._more_severe("S3", "S2") == "S2"
    assert ra._more_severe("TS", "S1") == "TS"
    assert ra._more_severe("", "S2") == "S2"
