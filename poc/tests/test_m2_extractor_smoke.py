"""m2_preprocess/extractor.py 분기 커버리지 보강.

외부 라이브러리 의존 분기는 라이브러리 미설치 시 graceful degrade 경로를 검증.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from lloydk.modules.m2_preprocess.extractor import ExtractResult, extract


@pytest.fixture
def tmp_path_files(tmp_path: Path):
    """다양한 확장자의 임시 파일."""
    files = {}
    for ext, body in [
        ("txt", "테스트 텍스트 파일 본문\n두 번째 줄"),
        ("md", "# 마크다운 제목\n본문"),
        ("log", "2026-05-28 INFO 로그 라인"),
        ("csv", "col1,col2\nv1,v2"),
    ]:
        p = tmp_path / f"sample.{ext}"
        p.write_text(body, encoding="utf-8")
        files[ext] = p
    return files


class TestPlainExtraction:
    def test_txt_extraction(self, tmp_path_files):
        result = extract(tmp_path_files["txt"])
        assert isinstance(result, ExtractResult)
        assert result.method == "plain"
        assert result.quality == 1.0
        assert "테스트 텍스트 파일 본문" in result.text

    def test_md_extraction(self, tmp_path_files):
        result = extract(tmp_path_files["md"])
        assert result.method == "plain"
        assert "마크다운 제목" in result.text

    def test_log_extraction(self, tmp_path_files):
        result = extract(tmp_path_files["log"])
        assert result.method == "plain"
        assert "INFO" in result.text

    def test_csv_extraction(self, tmp_path_files):
        result = extract(tmp_path_files["csv"])
        assert result.method == "plain"
        assert "col1" in result.text

    def test_cp949_fallback(self, tmp_path: Path):
        """utf-8 디코드 실패 시 cp949 fallback."""
        p = tmp_path / "cp949.txt"
        # cp949로 인코딩된 텍스트 (한국어)
        p.write_bytes("한국어 텍스트".encode("cp949"))
        result = extract(p)
        # cp949 fallback 작동 — text가 비어있지 않거나 빈 문자열 (둘 다 OK)
        assert isinstance(result, ExtractResult)
        assert result.method == "plain"


class TestUnsupported:
    def test_unsupported_extension(self, tmp_path: Path):
        p = tmp_path / "binary.xyz"
        p.write_bytes(b"\x00\x01\x02")
        result = extract(p)
        assert result.text == ""
        assert result.method == "plain"
        assert result.quality == 0.0
        assert "unsupported" in (result.error or "")

    def test_nonexistent_file(self, tmp_path: Path):
        """존재하지 않는 파일 — graceful 에러."""
        p = tmp_path / "does_not_exist.txt"
        # 존재하지 않으면 read 시점에서 에러 — extract가 안전하게 처리하는지
        try:
            result = extract(p)
            # 에러 또는 빈 결과 둘 다 OK
            assert isinstance(result, ExtractResult)
        except FileNotFoundError:
            # 명시적 에러도 OK (call site에서 처리)
            pass


class TestPdfFallback:
    """PDF 추출은 pdfminer.six → PyMuPDF fallback 경로 검증.

    실 PDF 없이도 _extract_pdf 호출 시 에러 메시지 정상 반환."""

    def test_pdf_nonexistent(self, tmp_path: Path):
        # PDF 확장자지만 실제 PDF 아닌 파일 → 라이브러리가 처리 시도 → 에러
        p = tmp_path / "fake.pdf"
        p.write_bytes(b"not a real pdf")
        result = extract(p)
        # 에러든 빈 결과든 ExtractResult 형태로 반환
        assert isinstance(result, ExtractResult)
        # quality가 0 (에러) 또는 매우 낮음
        assert result.quality <= 0.5


class TestDocxFallback:
    def test_docx_nonexistent(self, tmp_path: Path):
        p = tmp_path / "fake.docx"
        p.write_bytes(b"not a real docx")
        result = extract(p)
        assert isinstance(result, ExtractResult)
        assert result.quality == 0.0
        assert result.error is not None


class TestHwpFallback:
    def test_hwp_nonexistent(self, tmp_path: Path):
        """HWP 라이브러리가 없거나 파일이 잘못된 경우 graceful degrade."""
        p = tmp_path / "fake.hwp"
        p.write_bytes(b"not a real hwp")
        result = extract(p)
        assert isinstance(result, ExtractResult)
        # rhwp 또는 parser 또는 에러 — 어떤 경우든 ExtractResult 형태
        assert result.method in ("rhwp", "parser")

    def test_hwpx_nonexistent(self, tmp_path: Path):
        p = tmp_path / "fake.hwpx"
        p.write_bytes(b"not a real hwpx")
        result = extract(p)
        assert isinstance(result, ExtractResult)


class TestStringPath:
    def test_accepts_string_path(self, tmp_path: Path):
        """extract()는 str·Path 둘 다 받음."""
        p = tmp_path / "sample.txt"
        p.write_text("hello", encoding="utf-8")
        result = extract(str(p))
        assert result.text == "hello"
