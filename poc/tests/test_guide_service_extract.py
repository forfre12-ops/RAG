"""GuideService — extract() 배선 후 회귀테스트.

이전: _decode_best_effort() 로 바이트 직접 디코딩 → HWP/PDF 깨짐
변경: _extract_text() → extract() 포맷 파서 경유

검증:
- txt/docx/hwpx 파일 업로드 시 실제 텍스트가 추출되는지
- 이전처럼 latin-1 깨진 문자가 들어가지 않는지
- ES 미가용 환경에서 indexed=False 안전 반환 유지
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

import pytest

from lloydk.modules.m4_training.rag_indexer import IndexResult
from lloydk.services.guide_service import GuideService, _extract_text


def _stub_indexer(text_received: list) -> MagicMock:
    """index_guide 호출 시 text를 캡처하는 stub."""
    idx = MagicMock()
    def capture(**kwargs):
        text_received.append(kwargs.get("text", ""))
        return IndexResult(
            indexed=False, index_name="test", alias="test",
            chunk_count=0, vector_count=0, model="hash", warnings=["es unavailable"],
        )
    idx.index_guide.side_effect = capture
    return idx


def _make_hwpx(content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?><container><rootfiles>'
            '<rootfile full-path="Contents/content.hpf" media-type="application/hwp+zip"/>'
            '</rootfiles></container>')
        z.writestr("Contents/header.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hh:head xmlns:hh="urn:schemas-hancom-com:hh" version="1.0">'
            '<hh:docInfo><hh:title>Guide</hh:title></hh:docInfo></hh:head>')
        z.writestr("Contents/content.hpf",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hpf:package xmlns:hpf="urn:schemas-hancom-com:hpf" version="1.0">'
            '<hpf:manifest>'
            '<hpf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
            '<hpf:item id="body" href="Contents/section0.xml" media-type="application/xml"/>'
            '</hpf:manifest></hpf:package>')
        esc = content.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        z.writestr("Contents/section0.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hhs:section xmlns:hhs="urn:schemas-hancom-com:hhs">'
            f'<hhs:p><hhs:run><hhs:t>{esc}</hhs:t></hhs:run></hhs:p>'
            '</hhs:section>')
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _extract_text 단위 검증
# ---------------------------------------------------------------------------
class TestExtractText:
    def test_txt_extracted_correctly(self):
        body = "영업비밀 가이드: ALD 공정 레시피".encode("utf-8")
        text = _extract_text(body, filename="guide.txt")
        assert "ALD" in text
        assert "영업비밀" in text or len(text) > 0

    def test_docx_extracted_correctly(self):
        docx = pytest.importorskip("docx")
        d = docx.Document()
        d.add_paragraph("가이드 핵심 내용: ALD 증착 공정 조건")
        buf = io.BytesIO()
        d.save(buf)
        text = _extract_text(buf.getvalue(), filename="guide.docx")
        assert "ALD" in text

    def test_hwpx_extracted_correctly(self):
        pytest.importorskip("rhwp")
        body = _make_hwpx("ALD deposition guide content")
        text = _extract_text(body, filename="guide.hwpx")
        assert "ALD" in text

    def test_hwp_binary_does_not_produce_latin1_garbage(self):
        """이전 _decode_best_effort 는 HWP 바이너리를 latin-1로 디코딩해 쓰레기 텍스트 생성.
        _extract_text 는 빈 문자열 반환 (파서 실패) — 쓰레기보다 낫다."""
        fake_hwp = b"\xd0\xcf\x11\xe0" + b"\xff\xfe\x00\x01" * 100
        text = _extract_text(fake_hwp, filename="corrupt.hwp")
        # 이전 동작: 수백 바이트의 latin-1 쓰레기. 새 동작: 빈 문자열
        assert len(text) < 10 or text.isascii() or not text


# ---------------------------------------------------------------------------
# GuideService.upload — extract() 배선 통합 검증
# ---------------------------------------------------------------------------
class TestGuideServiceUpload:
    def setup_method(self):
        GuideService.reset_singleton()

    def test_txt_upload_text_reaches_indexer(self):
        captured = []
        svc = GuideService(indexer=_stub_indexer(captured))
        body = "영업비밀 보호 가이드: ALD 공정".encode("utf-8")
        svc.upload(
            guide_id="g1", version="v1", effective_date=None, change_summary=None,
            content_bytes=body, actor_user_id="u1", filename="guide.txt",
        )
        assert captured, "indexer가 호출되지 않음"
        assert "ALD" in captured[0], f"텍스트 미전달: {repr(captured[0][:80])}"

    def test_hwpx_upload_text_reaches_indexer(self):
        pytest.importorskip("rhwp")
        captured = []
        svc = GuideService(indexer=_stub_indexer(captured))
        body = _make_hwpx("ALD deposition guide — trade secret protection")
        svc.upload(
            guide_id="g2", version="v1", effective_date=None, change_summary=None,
            content_bytes=body, actor_user_id="u1", filename="guide.hwpx",
        )
        assert captured
        assert "ALD" in captured[0], f"HWPX 텍스트 미추출: {repr(captured[0][:80])}"

    def test_docx_upload_text_reaches_indexer(self):
        docx = pytest.importorskip("docx")
        captured = []
        svc = GuideService(indexer=_stub_indexer(captured))
        d = docx.Document()
        d.add_paragraph("ALD 증착 가이드 핵심 내용")
        buf = io.BytesIO()
        d.save(buf)
        svc.upload(
            guide_id="g3", version="v1", effective_date=None, change_summary=None,
            content_bytes=buf.getvalue(), actor_user_id="u1", filename="guide.docx",
        )
        assert captured
        assert "ALD" in captured[0]

    def test_es_unavailable_returns_safe(self):
        """ES 미가용 시 indexed=False 안전 반환 — 기존 동작 보존."""
        svc = GuideService(indexer=_stub_indexer([]))
        resp = svc.upload(
            guide_id="g4", version="v1", effective_date=None, change_summary=None,
            content_bytes=b"simple text guide", actor_user_id="u1", filename="g.txt",
        )
        assert resp.indexed is False
        assert resp.guide_id == "g4"
