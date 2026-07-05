"""HWP/HWPX 추출 경로 검증.

실제 .hwp 파일 없이도 rhwp 코드 경로를 mock으로 검증하고,
fixtures/sample.hwp 가 있으면 실제 파싱까지 검증한다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lloydk.modules.m2_preprocess.extractor import ExtractResult, extract

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# rhwp 코드 경로 — mock으로 배선 검증 (실파일 불필요)
# ---------------------------------------------------------------------------
class TestHwpCodePath:
    def test_hwp_calls_rhwp_parse(self, tmp_path):
        """extract()가 .hwp를 받으면 rhwp.parse()를 호출하는지 검증."""
        p = tmp_path / "test.hwp"
        p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 512)  # OLE 매직바이트

        mock_doc = MagicMock()
        mock_doc.extract_text.return_value = "영업비밀 핵심 공정 레시피 ALD 증착 조건"

        with patch.dict(sys.modules, {"rhwp": MagicMock(parse=MagicMock(return_value=mock_doc))}):
            result = extract(p)

        assert result.method == "rhwp"
        assert result.quality == 0.95
        assert "영업비밀" in result.text

    def test_hwpx_calls_rhwp_parse(self, tmp_path):
        """HWPX도 같은 경로."""
        p = tmp_path / "test.hwpx"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 100)  # ZIP 매직

        mock_doc = MagicMock()
        mock_doc.extract_text.return_value = "HWPX 테스트 본문"

        with patch.dict(sys.modules, {"rhwp": MagicMock(parse=MagicMock(return_value=mock_doc))}):
            result = extract(p)

        assert result.method == "rhwp"
        assert "HWPX" in result.text

    def test_hwp_empty_extraction_flags_warning(self, tmp_path):
        """rhwp가 빈 텍스트 반환(표·글상자 전용 HWP) → 성공이 아닌 경고로 surface.

        실측(2026-06-27): rhwp 0.5.1·0.8.0 모두 표/글상자/수식 내부 텍스트를
        추출하지 못해, 본문이 전부 표인 HWP는 빈 문자열이 된다. 빈 추출이
        quality 0.95로 통과하면 '조용한 미추출=미탐'이 되므로 quality 0.0 + error.
        """
        p = tmp_path / "table_only.hwp"
        p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 512)

        mock_doc = MagicMock()
        mock_doc.extract_text.return_value = ""  # 표만 있는 문서 — rhwp가 비움

        with patch.dict(sys.modules, {"rhwp": MagicMock(parse=MagicMock(return_value=mock_doc))}):
            result = extract(p)

        assert result.method == "rhwp"
        assert result.text == ""
        assert result.quality == 0.0
        assert result.error and "표" in result.error

    def test_hwp_prefers_pyhwp_when_richer(self, tmp_path):
        """pyhwp([hwp-tables])가 표 셀까지 더 많이 뽑으면 extract()가 그 결과를 채택.

        rhwp는 표 셀을 흘리므로(실측 noori.hwp 29/31 누락), pyhwp가 표 텍스트를
        회수해 더 길면 method=pyhwp로 채택한다.
        """
        import lloydk.modules.m2_preprocess.extractor as ex

        p = tmp_path / "withtable.hwp"
        p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 512)

        mock_doc = MagicMock()
        mock_doc.extract_text.return_value = "본문 단락만"  # rhwp: 표 누락
        pyhwp_text = "본문 단락만\n담당부서 | 거대공공연구정책과\n담당과장 | 장인숙"

        with patch.dict(sys.modules, {"rhwp": MagicMock(parse=MagicMock(return_value=mock_doc))}), \
                patch.object(ex, "_hwp_via_pyhwp", return_value=(pyhwp_text, [])):
            result = extract(p)

        assert result.method == "pyhwp"
        assert result.quality == 0.9
        assert "담당부서" in result.text  # 표 셀 회수됨

    def test_hwp_keeps_rhwp_when_pyhwp_absent(self, tmp_path):
        """pyhwp 미설치([hwp-tables] 없음)면 rhwp 본문 결과를 그대로 사용."""
        import lloydk.modules.m2_preprocess.extractor as ex

        p = tmp_path / "bodyonly.hwp"
        p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 512)

        mock_doc = MagicMock()
        mock_doc.extract_text.return_value = "본문 영업비밀 내용"

        with patch.dict(sys.modules, {"rhwp": MagicMock(parse=MagicMock(return_value=mock_doc))}), \
                patch.object(ex, "_hwp_via_pyhwp", return_value=(None, [])):
            result = extract(p)

        assert result.method == "rhwp"
        assert result.text == "본문 영업비밀 내용"
        assert result.quality == 0.95

    def test_hwp_falls_back_on_rhwp_error(self, tmp_path):
        """rhwp.parse() 실패 시 pyhwp fallback, 그것도 없으면 error ExtractResult."""
        p = tmp_path / "corrupt.hwp"
        p.write_bytes(b"not valid hwp content")

        mock_rhwp = MagicMock()
        mock_rhwp.parse.side_effect = Exception("parse failed")

        with patch.dict(sys.modules, {"rhwp": mock_rhwp, "hwp5": None}):
            result = extract(p)

        assert isinstance(result, ExtractResult)
        assert result.text == "" or result.method in ("rhwp", "parser")

    def test_hwp_ingestion_pipeline(self, tmp_path):
        """ingestion 서비스가 HWP를 받으면 rhwp를 타는지 end-to-end."""
        from lloydk.adapters.storage import LocalStorage
        from lloydk.services.document_ingestion_service import DocumentIngestionService

        storage = LocalStorage(root=str(tmp_path / "store"))
        svc = DocumentIngestionService(storage=storage)

        hwp_bytes = b"\xd0\xcf\x11\xe0" + b"\x00" * 512
        mock_doc = MagicMock()
        mock_doc.extract_text.return_value = "HWP 가이드 문서 본문. 핵심 공정 레시피 포함."

        with patch.dict(sys.modules, {"rhwp": MagicMock(parse=MagicMock(return_value=mock_doc))}):
            res = svc.ingest(
                filename="guide.hwp",
                content_bytes=hwp_bytes,
                persist=False,
            )

        assert res.source_format == "hwp"
        assert res.char_count > 0
        assert res.chunk_count >= 1
        # 원본 HWP 바이트가 보관됐는지
        assert storage.get(svc.RAW_BUCKET, f"{res.file_hash}/guide.hwp") == hwp_bytes


# ---------------------------------------------------------------------------
# 실제 HWP/HWPX 픽스처 (있으면 자동 실행)
# ---------------------------------------------------------------------------
class TestHwpRealFixture:
    @pytest.mark.parametrize("name", ["sample.hwp", "sample.hwpx"])
    def test_real_hwp_parsed(self, name, tmp_path):
        fx = FIXTURES / name
        if not fx.exists():
            pytest.skip(f"{name} 없음 — 한글에서 저장 후 tests/fixtures/에 두면 자동 실행")

        body = fx.read_bytes()
        result = extract(fx)

        # hwpx는 hwp5 모듈 의존성 — 미충족 시 xfail
        if result.error and "hwp5" in str(result.error):
            pytest.xfail(f"hwpx 파서 hwp5 모듈 의존성 미충족: {result.error}")
        assert result.method == "rhwp", f"expected rhwp, got {result.method}: {result.error}"
        assert result.text, f"본문 추출 실패: {result.error}"
        assert result.quality > 0

        # ingestion 전 구간
        from lloydk.adapters.storage import LocalStorage
        from lloydk.services.document_ingestion_service import DocumentIngestionService

        storage = LocalStorage(root=str(tmp_path / "store"))
        svc = DocumentIngestionService(storage=storage)
        res = svc.ingest(filename=name, content_bytes=body, persist=False)

        assert res.char_count > 0, f"ingestion 후 본문 없음: {res.warnings}"
        assert res.chunk_count >= 1
        assert storage.get(svc.RAW_BUCKET, f"{res.file_hash}/{name}") == body
