"""스캔본 PDF(텍스트 레이어 없음) — OCR 을 하지 않으므로 본문 없이 등록된다.

2026-09-26 OCR 을 제거했다(요건 밖). 이 경로가 조용히 통과하지 않는지 — 본문 0자·경고·원본 보관 — 를 지킨다.
"""

from __future__ import annotations

import io

from koipa.modules.m2_preprocess.extractor import extract


def _scanned_pdf_from_image() -> bytes:
    """텍스트 레이어 없이 이미지만 있는 최소 PDF 생성.

    필터 없는 회색조 원시 픽셀을 이미지 XObject 로 넣는다(Pillow 불필요 — 최소 CI 환경에서도 건너뛰지 않는다).
    pdfminer 는 텍스트를 못 뽑는다(OCR 은 하지 않는다).
    """
    w, h = 64, 16
    image_data = bytes((x * 4 + y) % 256 for y in range(h) for x in range(w))

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
            b"/ColorSpace /DeviceGray /BitsPerComponent 8 "
            b"/Length %d >>\nstream\n" % (w, h, len(image_data))
            + image_data + b"\nendstream"
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


class TestScannedPdfWithoutText:
    def test_extract_returns_no_text_and_says_why(self, tmp_path):
        p = tmp_path / "scanned.pdf"
        p.write_bytes(_scanned_pdf_from_image())

        result = extract(p)

        assert result.text.strip() == ""
        assert "pdf_no_text_layer" in result.warnings
        assert result.method != "ocr"
        assert "OCR is not supported" in (result.error or "")

    def test_ingestion_registers_without_text_and_keeps_the_original(self, tmp_path):
        from koipa.adapters.storage import LocalStorage
        from koipa.services.document_ingestion_service import DocumentIngestionService

        body = _scanned_pdf_from_image()
        storage = LocalStorage(root=str(tmp_path / "store"))
        svc = DocumentIngestionService(storage=storage)
        res = svc.ingest(filename="scanned.pdf", content_bytes=body, persist=False)

        assert res.source_format == "pdf"
        assert res.char_count == 0
        assert any("pdf_no_text_layer" in w for w in res.warnings)
        # 원본은 보관한다
        assert storage.get(svc.RAW_BUCKET, f"{res.file_hash}/scanned.pdf") == body
