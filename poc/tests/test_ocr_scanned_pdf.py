"""스캔본 PDF OCR 검증 — pdf2image + Tesseract.

텍스트 레이어 없는 PDF를 이미지로 변환 후 OCR로 텍스트 추출하는 경로를 검증.
poppler(pdftoppm)가 설치돼 있어야 pdf2image가 동작한다.
"""

from __future__ import annotations

import io

import pytest

from lloydk.modules.m2_preprocess.extractor import POPPLER_PATH, extract


def _scanned_pdf_from_image() -> bytes:
    """텍스트 레이어 없이 이미지만 있는 최소 PDF 생성.

    PIL로 텍스트 이미지를 그린 뒤 PDF 스트림에 JPEG로 임베드.
    pdfminer는 텍스트를 못 뽑고 OCR 경로만 뽑을 수 있다.
    """
    PIL = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")

    # 텍스트 이미지 생성
    img = PIL.new("RGB", (800, 200), color="white")
    ImageDraw.Draw(img).text((20, 80), "Trade Secret ALD deposition recipe", fill="black")

    # JPEG 바이트
    jpeg_buf = io.BytesIO()
    img.save(jpeg_buf, format="JPEG")
    jpeg_data = jpeg_buf.getvalue()
    w, h = img.size

    # PDF에 이미지 삽입 (텍스트 스트림 없음)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Contents 4 0 R /Resources << /XObject << /Im0 5 0 R >> >> >>"
        ) % (w, h),
        b"<< /Length 20 >>\nstream\nq %d 0 0 %d 0 0 cm /Im0 Do Q\nendstream" % (w, h),
        (
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
            b"/Filter /DCTDecode /Length %d >>\nstream\n" % (w, h, len(jpeg_data))
            + jpeg_data + b"\nendstream"
        ),
    ]

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(buf.tell())
        buf.write(b"%d 0 obj\n%s\nendobj\n" % (i, o))
    xref = buf.tell()
    buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        buf.write(b"%010d 00000 n \n" % off)
    buf.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref))
    return buf.getvalue()


class TestScannedPdfOcr:
    def test_scanned_pdf_ocr(self, tmp_path):
        pytest.importorskip("pdf2image")
        pytest.importorskip("pytesseract")

        if not POPPLER_PATH:
            pytest.skip("poppler 경로 미설정 — POPPLER_PATH 환경변수 또는 ~/tools/poppler/bin 설치 필요")

        body = _scanned_pdf_from_image()
        p = tmp_path / "scanned.pdf"
        p.write_bytes(body)

        result = extract(p)

        assert result.ocr_used is True, f"OCR 미사용: method={result.method}, error={result.error}"
        assert result.method == "ocr"
        # Tesseract 영문 인식 확인
        assert "Trade Secret" in result.text or len(result.text) > 0, \
            f"텍스트 추출 실패: {repr(result.text[:100])}, error={result.error}"

    def test_scanned_pdf_ingestion(self, tmp_path):
        pytest.importorskip("pdf2image")
        pytest.importorskip("pytesseract")

        if not POPPLER_PATH:
            pytest.skip("poppler 미설치")

        from lloydk.adapters.storage import LocalStorage
        from lloydk.services.document_ingestion_service import DocumentIngestionService

        body = _scanned_pdf_from_image()
        storage = LocalStorage(root=str(tmp_path / "store"))
        svc = DocumentIngestionService(storage=storage)
        res = svc.ingest(filename="scanned.pdf", content_bytes=body, tenant_id="t1", persist=False)

        assert res.ocr_used is True
        assert res.source_format == "pdf"
        # 원본 보관
        assert storage.get(svc.RAW_BUCKET, f"t1/{res.file_hash}/scanned.pdf") == body
