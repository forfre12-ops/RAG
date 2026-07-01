"""[P0#3] 저품질/OCR 추출 → 분류 전 검수(needs_review) 라우팅 게이트.

배경: extraction_quality/ocr_used 는 계산·저장되나 소비하는 review 트리거가 0건이라, 깨끗한
파서 추출과 OCR/스캔본이 동일 입력으로 분류기에 진입해 표 누락·깨진 문자로 조용히 오분류됐다.
본 게이트가 (1) 순수 판정 (2) ingest() 통합(requires_review·warning·processing_status·메트릭)을
검증한다. DB 불요 — 스토리지/전처리 스텁 + persist=False(processing_status는 _persist 단위로 별도 검증).
"""

from __future__ import annotations

import pytest
from prometheus_client import generate_latest

from lloydk.api.prom_metrics import registry
from lloydk.modules.m2_preprocess.extractor import ExtractResult
from lloydk.modules.m2_preprocess.pipeline import PreprocessResult
from lloydk.services.document_ingestion_service import (
    DocumentIngestionService,
    extraction_review_decision,
)


# ── 순수 판정 ────────────────────────────────────────────────────────────────

def test_clean_parser_extraction_no_review():
    d = extraction_review_decision(
        quality=0.95, ocr_used=False, error=None, min_quality=0.6, ocr_requires_review=True
    )
    assert d.requires_review is False and d.reasons == []


def test_ocr_routes_to_review():
    d = extraction_review_decision(
        quality=0.9, ocr_used=True, error=None, min_quality=0.6, ocr_requires_review=True
    )
    assert d.requires_review is True and "ocr" in d.reasons


def test_low_quality_routes_to_review():
    d = extraction_review_decision(
        quality=0.4, ocr_used=False, error=None, min_quality=0.6, ocr_requires_review=True
    )
    assert d.requires_review is True and d.reasons == ["low_quality"]


def test_extract_error_routes_to_review():
    d = extraction_review_decision(
        quality=0.9, ocr_used=False, error="table cells missing", min_quality=0.6,
        ocr_requires_review=True,
    )
    assert d.requires_review is True and "extract_error" in d.reasons


def test_ocr_review_can_be_disabled():
    # OCR 검수 라우팅을 끄면(고신뢰 OCR 환경) ocr 사유는 빠진다 — 저품질은 여전히 잡힘.
    d = extraction_review_decision(
        quality=0.9, ocr_used=True, error=None, min_quality=0.6, ocr_requires_review=False
    )
    assert d.requires_review is False and d.reasons == []


def test_boundary_quality_at_threshold_passes():
    # quality == min_quality 는 통과(미만일 때만 라우팅).
    d = extraction_review_decision(
        quality=0.6, ocr_used=False, error=None, min_quality=0.6, ocr_requires_review=True
    )
    assert d.requires_review is False


# ── ingest() 통합 (DB 불요) ──────────────────────────────────────────────────

class _FakeStorage:
    def put(self, bucket, key, data):  # noqa: D401
        return f"mem://{bucket}/{key}"


class _FakePre:
    """run_file 이 주어진 ExtractResult 기반 PreprocessResult 를 돌려주는 전처리 스텁."""

    def __init__(self, ext: ExtractResult):
        self._ext = ext

    def run_file(self, path):
        return PreprocessResult(
            text=self._ext.text, chunks=[], extraction=self._ext,
            quality=self._ext.quality, metadata={}, pii_counts={},
        )


def _ingest(ext: ExtractResult):
    svc = DocumentIngestionService(storage=_FakeStorage(), preprocess=_FakePre(ext))
    return svc.ingest(filename="doc.pdf", content_bytes=b"%PDF-1.4 ...", persist=False)


def _metric(reason: str) -> float:
    return registry.get_sample_value("lloydk_extraction_degraded_total", {"reason": reason}) or 0.0


def test_ingest_clean_doc_not_flagged():
    res = _ingest(ExtractResult(text="충분히 긴 정상 본문입니다." * 5, method="parser", quality=0.95))
    assert res.requires_review is False
    assert res.review_reasons == []
    assert not any("degraded" in w for w in res.warnings)


def test_ingest_ocr_doc_flagged_and_metric():
    before = _metric("ocr")
    res = _ingest(ExtractResult(text="OCR 로 뽑은 본문", method="ocr", quality=0.75, ocr_used=True))
    assert res.requires_review is True
    assert "ocr" in res.review_reasons
    assert any("degraded" in w for w in res.warnings)          # 무음 아님 — 경고 노출
    assert _metric("ocr") >= before + 1                        # 열화 메트릭 증가


def test_ingest_empty_extraction_counts_empty_not_review():
    before = _metric("empty")
    res = _ingest(ExtractResult(text="", method="ocr", quality=0.0, ocr_used=False, error="unreadable"))
    # 빈 본문은 processing_status='failed'로 별도 격리 — requires_review 판정 대상 아님.
    assert res.requires_review is False
    assert _metric("empty") >= before + 1
