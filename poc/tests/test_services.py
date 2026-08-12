"""ClassifyService + LLMUsageService — 외부 인프라 없이 동작 확인."""

from __future__ import annotations
import pytest
pytestmark = pytest.mark.slow

import json
from pathlib import Path

from koipa.adapters.llm.base import UsageRecord
from koipa.schemas.classify import ClassifyRequest
from koipa.services.classify_service import ClassifyService
from koipa.services.llm_usage_service import LLMUsageService


def test_classify_service_singleton():
    a = ClassifyService.get_instance()
    b = ClassifyService.get_instance()
    assert a is b


def test_classify_service_end_to_end():
    svc = ClassifyService.get_instance()
    req = ClassifyRequest(
        doc_id="t-1",
        content="특급기밀 핵심 원천기술 M&A 계획 차세대 제품 설계도",
        use_rag=False,
        return_evidence=True,
    )
    res = svc.classify(req)
    assert res.label.value == "TS"
    assert res.confidence > 0.5
    assert res.doc_id == "t-1"
    assert len(res.evidence) > 0


# ── [FIX-D] 공개특허공보 마스트헤드 → source-prior 캡 (TS 과분류 완화) ──────────

def test_fix_d_masthead_detector():
    from koipa.services.classify_service import _is_published_patent_gazette
    # 실제 공개특허공보 서지헤더(3요소: 공보종별 + 특허청 + INID)
    patent_head = (
        "공개특허 10-2017-0094559\n\n(19) 대한민국특허청(KR)\n"
        "(12) 공개특허공보(A)\n(11) 공개번호   10-2017-0094559\n(43) 공개일자\n"
        "발명의 명칭 반도체 장치. CVD 공정으로 산화물 반도체 박막을 증착한다."
    )
    assert _is_published_patent_gazette(patent_head) is True
    # 진짜 기밀 — 마스트헤드 없음
    assert _is_published_patent_gazette(
        "본 자료는 특급기밀이며 반도체 공정 레시피를 CVD로 정리한다. 1급 비밀."
    ) is False
    # 공보를 본문 중간에 '인용'만 한 내부 기밀 — 머리 서지헤더 아님 → 미탐지(오탐 방지)
    assert _is_published_patent_gazette(
        "본 내부 기밀 보고서. 특급기밀. " * 30 + " 경쟁사 공개특허공보(A)를 특허청에서 인용 (11)."
    ) is False


def test_fix_d_patent_gazette_capped_to_s3_and_reviewed():
    """공개특허공보 본문 → source-prior 캡으로 최종 S3 + cap-conflict→needs_review.

    (룰-폴백 경로에서도 캡은 최종등급에 적용되므로 모델 없이 검증 가능. 청크 severe-agg가
    CVD로 TS를 냈어도 공개출처 캡이 S3로 되돌리고 사람검수로 라우팅한다.)
    """
    svc = ClassifyService.get_instance()
    body = (
        "공개특허 10-2017-0094559\n(19) 대한민국특허청(KR)\n(12) 공개특허공보(A)\n"
        "(11) 공개번호 10-2017-0094559\n(43) 공개일자\n발명의 명칭 반도체 장치.\n"
    ) + ("산화물 반도체 박막을 CVD 방식으로 증착하고 N2O 분위기에서 처리한다. " * 40)
    res = svc.classify(ClassifyRequest(doc_id="patent-x", content=body, use_rag=False))
    assert res.label.value == "S3", f"공개특허가 S3로 캡되지 않음: {res.label.value}"
    assert res.status == "needs_review"
    assert any("source-prior" in w for w in (res.warnings or []))


def test_fix_d_does_not_touch_genuine_secret():
    """마스트헤드 없는 진짜 기밀은 FIX-D 영향 없음(자동확정·등급 유지) — FNR-safe."""
    svc = ClassifyService.get_instance()
    body = (
        "본 자료는 특급기밀이며 반도체 공정 레시피와 EUV 공정 파라미터, "
        "특수 합금 조성비를 CVD·N2O 공정으로 정리한다. 1급 비밀. "
    ) * 8
    res = svc.classify(ClassifyRequest(doc_id="sec-x", content=body, use_rag=False))
    assert res.label.value == "TS"
    assert not any("source-prior" in w for w in (res.warnings or []))


def test_llm_usage_service_writes_jsonl(tmp_path: Path):
    svc = LLMUsageService(jsonl_path=str(tmp_path / "usage.jsonl"))
    rec = UsageRecord(
        provider="noop",
        model="noop",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.0,
        latency_ms=10,
    )
    svc.record(rec, purpose="test", reference_id="abc")
    svc.record(rec, purpose="test", reference_id="def")

    lines = (tmp_path / "usage.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["provider"] == "noop"
    assert parsed["purpose"] == "test"
    assert parsed["reference_id"] == "abc"
