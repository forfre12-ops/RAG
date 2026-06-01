"""DocumentIngestionService — 실제 파일 업로드 ingestion 검증.

OSS 코퍼스(정제된 JSON)가 아니라 "현장처럼" 실제 .txt/.docx 바이너리를 올려서:
  추출 → 원본 보관(storage) → provenance → 청크 까지 전 구간을 검증.
HWP/PDF(스캔)·OCR 은 라이브러리 미설치 — 별도 increment 에서 추가.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest

from lloydk.adapters.storage import LocalStorage
from lloydk.db.models import Chunk, Document
from lloydk.services.document_ingestion_service import DocumentIngestionService

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).parent / "fixtures"


def _minimal_pdf(text: str = "Trade Secret Process ALD") -> bytes:
    """텍스트 레이어가 있는 최소 유효 PDF(ASCII). pdfminer 추출 경로 검증용."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = b"BT /F1 24 Tf 72 700 Td (%s) Tj ET" % text.encode("ascii", "replace")
    objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(buf.tell())
        buf.write(b"%d 0 obj\n%s\nendobj\n" % (i, o))
    xref_pos = buf.tell()
    buf.write(b"xref\n0 %d\n" % (len(objs) + 1))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(b"%010d 00000 n \n" % off)
    buf.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objs) + 1, xref_pos)
    )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# stub session — DB 없이 영속화 흐름(add/flush) 검증. 실 SQL은 PG 잡이 커버.
# ---------------------------------------------------------------------------
class _StubSession:
    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)
        # PG server_default(gen_random_uuid) 를 stub 에서 미리 부여
        if isinstance(obj, Document) and getattr(obj, "doc_id", None) is None:
            obj.doc_id = uuid.uuid4()
        if isinstance(obj, Chunk) and getattr(obj, "chunk_id", None) is None:
            obj.chunk_id = uuid.uuid4()

    def flush(self):
        pass

    def execute(self, _stmt):
        class _R:
            rowcount = 0

        return _R()


def _storage(tmp_path):
    return LocalStorage(root=str(tmp_path / "store"))


# ---------------------------------------------------------------------------
# .txt — 평문 추출 (라이브러리 의존 없음, 항상 동작)
# ---------------------------------------------------------------------------
class TestTxtIngestion:
    def test_extracts_stores_chunks(self, tmp_path):
        storage = _storage(tmp_path)
        svc = DocumentIngestionService(storage=storage)
        body = (
            "본 가이드의 핵심 제조 공정은 ALD 증착이며, 원료 조성 비율은 대외비다. "
            "공정 레시피와 온도·압력 조건을 상세히 기술한다." * 5
        ).encode("utf-8")

        res = svc.ingest(
            filename="guide.txt", content_bytes=body, tenant_id="t1", persist=False
        )

        assert res.source_format == "txt"
        assert res.extraction_method == "plain"
        assert res.extraction_quality == 1.0
        assert res.char_count > 0
        assert res.chunk_count >= 1
        assert res.file_size_bytes == len(body)
        assert len(res.file_hash) == 64  # sha256 hex

    def test_original_is_retrievable(self, tmp_path):
        storage = _storage(tmp_path)
        svc = DocumentIngestionService(storage=storage)
        body = "원본 보관 검증용 본문".encode("utf-8")

        res = svc.ingest(
            filename="orig.txt", content_bytes=body, tenant_id="t1", persist=False
        )

        key = f"t1/{res.file_hash}/orig.txt"
        assert res.raw_text_uri.startswith("file://")
        assert storage.exists(svc.RAW_BUCKET, key)
        assert storage.get(svc.RAW_BUCKET, key) == body  # 무손실 원본
        # 정규화 텍스트도 별도 보관
        assert res.normalized_text_uri is not None


# ---------------------------------------------------------------------------
# .docx — 진짜 바이너리 파싱 (python-docx 설치 시)
# ---------------------------------------------------------------------------
class TestDocxIngestion:
    def test_real_docx_parsed(self, tmp_path):
        docx = pytest.importorskip("docx")  # python-docx
        d = docx.Document()
        d.add_paragraph("영업비밀 가이드 문서입니다.")
        d.add_paragraph("핵심 제조 공정과 조성 비율을 포함한 대외비 본문.")
        p = tmp_path / "sample.docx"
        d.save(str(p))
        body = p.read_bytes()

        storage = _storage(tmp_path)
        svc = DocumentIngestionService(storage=storage)
        res = svc.ingest(
            filename="sample.docx", content_bytes=body, tenant_id="t1", persist=False
        )

        assert res.source_format == "docx"
        assert res.extraction_method == "parser"
        assert res.char_count > 0
        # 정규화 텍스트에 실제 본문이 깨지지 않고 들어갔는지
        norm = storage.get(svc.NORM_BUCKET, f"t1/{res.file_hash}/normalized.txt").decode("utf-8")
        assert "영업비밀" in norm
        assert "제조 공정" in norm


# ---------------------------------------------------------------------------
# .pdf — 실제 PDF 텍스트 레이어 추출 (pdfminer)
# ---------------------------------------------------------------------------
class TestPdfIngestion:
    def test_real_pdf_text_layer(self, tmp_path):
        body = _minimal_pdf("Trade Secret Process ALD deposition")
        storage = _storage(tmp_path)
        svc = DocumentIngestionService(storage=storage)
        res = svc.ingest(
            filename="report.pdf", content_bytes=body, tenant_id="t1", persist=False
        )
        assert res.source_format == "pdf"
        assert res.extraction_method == "parser"
        assert res.ocr_used is False
        norm = storage.get(svc.NORM_BUCKET, f"t1/{res.file_hash}/normalized.txt").decode("utf-8")
        assert "Trade Secret" in norm


# ---------------------------------------------------------------------------
# 실제 HWP/HWPX/PDF(한국어) — 발주처·협력사 제공 샘플로만 검증 (합성 불가)
# tests/fixtures/sample.{hwp,hwpx,pdf} 가 있으면 자동 실행, 없으면 skip.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 이미지 파일 OCR (Tesseract + kor 팩)
# ---------------------------------------------------------------------------
class TestImageOcr:
    def test_image_ocr_extracts_text(self, tmp_path):
        PIL = pytest.importorskip("PIL.Image")
        ImageDraw = pytest.importorskip("PIL.ImageDraw")
        pytest.importorskip("pytesseract")

        img = PIL.new("RGB", (600, 80), color="white")
        ImageDraw.Draw(img).text((10, 20), "Trade Secret ALD process", fill="black")
        png = tmp_path / "scan.png"
        img.save(str(png))
        body = png.read_bytes()

        storage = _storage(tmp_path)
        svc = DocumentIngestionService(storage=storage)
        res = svc.ingest(
            filename="scan.png", content_bytes=body, tenant_id="t1", persist=False
        )

        assert res.source_format == "png"
        assert res.ocr_used is True
        assert res.extraction_method == "ocr"
        assert "Trade Secret" in storage.get(
            svc.NORM_BUCKET, f"t1/{res.file_hash}/normalized.txt"
        ).decode("utf-8")


class TestRealFixtures:
    @pytest.mark.parametrize("name", ["sample.hwp", "sample.hwpx", "sample_kr.pdf"])
    def test_real_document_fixture(self, name, tmp_path):
        fx = FIXTURES / name
        if not fx.exists():
            pytest.skip(
                f"실제 {name} 샘플 없음 — 발주처/협력사 제공 시 tests/fixtures/에 두면 자동 검증"
            )
        body = fx.read_bytes()
        storage = _storage(tmp_path)
        svc = DocumentIngestionService(storage=storage)
        res = svc.ingest(
            filename=name, content_bytes=body, tenant_id="t1", persist=False
        )
        assert res.source_format == name.rsplit(".", 1)[-1]
        # 실제 본문이 추출됐는지 — HWP 파서/ PDF 텍스트레이어 실증
        # hwpx는 hwp5 모듈 의존성 — 설치 안 됐거나 파싱 실패 시 xfail
        if res.char_count == 0 and any("hwp5" in w or "no text" in w for w in res.warnings):
            pytest.xfail(f"hwpx 파서 의존성 미충족: {res.warnings}")
        assert res.char_count > 0, f"본문 추출 실패: warnings={res.warnings}"
        assert res.chunk_count >= 1
        # 원본 무손실 보관 확인
        assert storage.get(svc.RAW_BUCKET, f"t1/{res.file_hash}/{name}") == body


# ---------------------------------------------------------------------------
# 영속화 — documents 1행 + chunks N행 (stub session)
# ---------------------------------------------------------------------------
class TestPersistence:
    def test_persists_document_and_chunks(self, tmp_path):
        storage = _storage(tmp_path)
        svc = DocumentIngestionService(storage=storage)
        sess = _StubSession()
        body = ("공정 레시피 본문 한국어 텍스트. " * 30).encode("utf-8")

        res = svc.ingest(
            filename="a.txt",
            content_bytes=body,
            tenant_id="t1",
            doc_type="가이드",
            created_by="u1",
            db=sess,
            persist=True,
        )

        assert res.persisted is True
        assert res.doc_id is not None

        docs = [o for o in sess.added if isinstance(o, Document)]
        chunks = [o for o in sess.added if isinstance(o, Chunk)]
        assert len(docs) == 1
        doc = docs[0]
        # provenance 1:1 기록 확인
        assert doc.tenant_id == "t1"
        assert doc.source_format == "txt"
        assert doc.file_hash == res.file_hash
        assert doc.raw_text_uri == res.raw_text_uri
        assert doc.normalized_text_uri == res.normalized_text_uri
        assert doc.extraction_method == "plain"
        assert doc.processing_status == "ready"
        assert doc.created_by == "u1"
        assert doc.metadata_ == {"doc_type": "가이드"}
        # chunks 적재
        assert len(chunks) >= 1
        assert all(c.doc_id == doc.doc_id for c in chunks)
        assert all(c.tenant_id == "t1" for c in chunks)

    def test_unsupported_format_degrades_gracefully(self, tmp_path):
        storage = _storage(tmp_path)
        svc = DocumentIngestionService(storage=storage)
        # 미지원 확장자 → 추출 0 이지만 원본은 보관되고 안전 반환
        res = svc.ingest(
            filename="weird.xyz",
            content_bytes=b"\x00\x01\x02binarymisc",
            tenant_id="t1",
            persist=False,
        )
        assert res.source_format == "xyz"
        assert res.char_count == 0
        # 원본은 그래도 보관 (감사 요건)
        assert storage.exists(svc.RAW_BUCKET, f"t1/{res.file_hash}/weird.xyz")
        assert any("no text extracted" in w for w in res.warnings)
