"""포맷별 텍스트 추출 라우터.

라이브러리 로드 실패는 graceful degrade — 라이브러리가 없는 환경에서도 .txt/.md는 처리.
extractor가 반환하는 ExtractResult는 추출 메서드·품질·OCR 사용 여부를 포함해
DB `documents.extraction_method/quality/ocr_used` 와 1:1.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExtractResult:
    text: str
    method: str           # parser/rhwp/ocr/ocr_llm/libreoffice/plain
    quality: float        # 0.0~1.0 (추정)
    ocr_used: bool = False
    pages: int | None = None
    error: str | None = None


def extract(path: str | Path) -> ExtractResult:
    p = Path(path)
    suffix = p.suffix.lower().lstrip(".")
    if suffix in ("txt", "md", "log", "csv"):
        return _extract_plain(p)
    if suffix in ("hwp", "hwpx"):
        return _extract_hwp(p)
    if suffix in ("docx",):
        return _extract_docx(p)
    if suffix in ("pdf",):
        return _extract_pdf(p)
    return ExtractResult(text="", method="plain", quality=0.0, error=f"unsupported: {suffix}")


def _extract_plain(p: Path) -> ExtractResult:
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = p.read_text(encoding="cp949", errors="ignore")
    return ExtractResult(text=text, method="plain", quality=1.0)


def _extract_hwp(p: Path) -> ExtractResult:
    # 1순위: rhwp-python 0.5.x (HWP+HWPX 통합, optional extra `[hwp]`)
    # 활성: pip install -e ".[hwp]"
    try:
        import rhwp  # type: ignore

        doc = rhwp.parse(str(p))  # 0.5.x API
        text = doc.extract_text()
        return ExtractResult(text=text, method="rhwp", quality=0.95)
    except Exception:
        pass
    # 2순위: pyhwp (HWP5 전용)
    try:
        import hwp5  # type: ignore

        text = hwp5.extract_text(str(p))  # type: ignore[attr-defined]
        return ExtractResult(text=text, method="parser", quality=0.85)
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="rhwp", quality=0.0, error=str(exc))


def _extract_docx(p: Path) -> ExtractResult:
    try:
        from docx import Document  # type: ignore

        doc = Document(str(p))
        parts = [para.text for para in doc.paragraphs if para.text]
        for tbl in doc.tables:
            for row in tbl.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return ExtractResult(text="\n".join(parts), method="parser", quality=0.97)
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="parser", quality=0.0, error=str(exc))


def _extract_pdf(p: Path) -> ExtractResult:
    try:
        import fitz  # PyMuPDF  # type: ignore

        doc = fitz.open(str(p))
        try:
            pages = [page.get_text() for page in doc]
        finally:
            doc.close()
        text = "\n".join(pages)
        if text.strip():
            return ExtractResult(text=text, method="parser", quality=0.95, pages=len(pages))
        # 텍스트 0 → OCR fallback (PaddleOCR/Tesseract). 본 PoC에서는 비어있음 보고.
        return ExtractResult(text="", method="ocr", quality=0.0, ocr_used=False,
                             pages=len(pages), error="no text layer (OCR needed)")
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="parser", quality=0.0, error=str(exc))
