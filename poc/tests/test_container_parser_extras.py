"""배포 이미지가 전자문서 파싱에 필요한 extras를 빠뜨리지 않는지 확인한다."""

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "dockerfile",
    ("Dockerfile.api", "Dockerfile.api.prod", "Dockerfile.worker"),
)
def test_runtime_images_include_pdf_table_extractor(dockerfile: str):
    """pdfplumber 미설치로 모든 PDF가 table_incomplete가 되는 회귀를 막는다."""
    content = (_ROOT / dockerfile).read_text(encoding="utf-8")
    assert "pdf-tables" in content

