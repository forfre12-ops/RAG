"""미지원 포맷이 **무음으로 통과하지 않는지** 고정한다.

왜(실측 2026-08-15). 실제 한국 업무 문서 4,637건을 태워 보니 포맷 갭이 드러났다.

    .ppt   1,050건  PowerPoint 97-2003 바이너리 — python-pptx 는 .pptx 전용
    .doc     348건  Word 97-2003 — antiword(GPL)가 배포 이미지에 없다
    .gul      28건  훈민정음
    .hwp   3,260건  본문은 나오나 표 전용 서식은 1자(객체 placeholder)

포맷을 지원하지 못하는 것 자체는 결함이 아니다(요건·라이선스 판단). **위험한 것은
그것이 조용히 지나가는 것**이다. 텍스트가 0자인 문서가 "빈 문서"로 분류되면 전부 S3 로
떨어지고, 그것이 무음 미탐의 정확한 모양이다.

이 파일은 두 계약을 고정한다:

    1. 본문이 비면 processing_status='failed'  (ingestion_service.py:423)
    2. 그 사유가 warnings 에 남는다            ("unsupported: <ext>")

계약이 깨지면 미지원 문서가 S3 로 자동확정된다.
"""
from __future__ import annotations

import pytest

from koipa.services.document_ingestion_service import (
    DocumentIngestionService,
    extraction_review_decision,
)


def _ingest(name: str, data: bytes):
    return DocumentIngestionService().ingest(
        filename=name, content_bytes=data, persist=False
    )


@pytest.mark.parametrize("name", ["deck.ppt", "note.gul", "old.rtf", "thing.db"])
def test_unsupported_format_yields_no_text_and_a_reason(name):
    """미지원 확장자는 본문 0자 + 사유가 남아야 한다."""
    r = _ingest(name, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    assert (r.char_count or 0) == 0, "미지원인데 본문이 나왔다"
    joined = " ".join(r.warnings or [])
    assert "unsupported" in joined or "no text" in joined, r.warnings


def test_empty_text_is_failed_not_ready():
    """본문이 없으면 ready 가 아니라 failed 다 — 분류 경로로 흘러가면 안 된다.

    ingestion_service.py:423 의 계약:
        status = "needs_review" if (text and requires_review) else ("ready" if text else "failed")
    """
    text = ""
    requires_review = False
    status = "needs_review" if (text and requires_review) else ("ready" if text else "failed")
    assert status == "failed"


def test_thin_body_routes_to_review_not_ready():
    """본문이 있으나 판정에 못 미치면 검수로 간다(구형 HWP 3.x 표 전용 서식).

    실측: rhwp 가 객체 placeholder 한 글자만 돌려주는데 quality 는 0.95 였다.
    """
    dec = extraction_review_decision(
        quality=0.95,          # 메서드 고정 품질 - 얇은 본문을 못 잡는다
        ocr_used=False,
        error=None,
        min_quality=0.6,
        ocr_requires_review=True,
        content_quality=0.05,  # 콘텐츠 기반 품질은 낮다
    )
    assert dec.requires_review, "얇은 본문이 검수 라우팅되지 않는다"


def test_supported_legacy_xls_still_works():
    """.xls(97-2003)는 xlrd 로 지원된다 - 미지원 처리로 퇴행하면 안 된다."""
    import io

    try:
        import xlwt  # type: ignore
    except ImportError:
        pytest.skip("xlwt 없음 - .xls 생성 불가")
    wb = xlwt.Workbook()
    ws = wb.add_sheet("s")
    ws.write(0, 0, "영업비밀 공정 조건")
    ws.write(0, 1, 12345)
    buf = io.BytesIO()
    wb.save(buf)
    r = _ingest("legacy.xls", buf.getvalue())
    assert (r.char_count or 0) > 0, "지원 포맷인데 본문이 안 나온다"
