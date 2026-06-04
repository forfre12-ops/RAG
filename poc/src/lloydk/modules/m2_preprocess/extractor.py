"""포맷별 텍스트 추출 라우터.

라이브러리 로드 실패는 graceful degrade — 라이브러리가 없는 환경에서도 .txt/.md는 처리.
extractor가 반환하는 ExtractResult는 추출 메서드·품질·OCR 사용 여부를 포함해
DB `documents.extraction_method/quality/ocr_used` 와 1:1.

OCR 엔진: Tesseract 5.x + 한국어팩 (kor.traineddata) — Apache 2.0.
  - 시스템 경로 자동 탐지 후 환경변수 TESSERACT_CMD 로 override 가능.
  - 폐쇄망 초기 설치: winget install UB-Mannheim.TesseractOCR 후 kor.traineddata 복사.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Tesseract 실행파일 경로 — 환경변수 > Windows 기본 경로 > PATH
_TESS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_CMD = os.environ.get("TESSERACT_CMD") or (
    _TESS_DEFAULT if Path(_TESS_DEFAULT).exists() else "tesseract"
)

# poppler 경로 (pdf2image가 사용) — 환경변수 > 사용자 tools > None(PATH 탐색)
_POPPLER_CANDIDATES = [
    os.environ.get("POPPLER_PATH", ""),
    str(Path.home() / "tools" / "poppler" / "bin"),
    r"C:\Program Files\poppler\bin",
]
POPPLER_PATH: str | None = next(
    (p for p in _POPPLER_CANDIDATES if p and (Path(p) / "pdftoppm.exe").exists()),
    None,
)


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
    if suffix in ("xlsx", "xlsm", "xls"):
        return _extract_excel(p)
    if suffix in ("pdf",):
        return _extract_pdf(p)
    if suffix in ("jpg", "jpeg", "png", "tiff", "tif", "bmp", "webp"):
        return _extract_image_ocr(p)
    return ExtractResult(text="", method="plain", quality=0.0, error=f"unsupported: {suffix}")


def _extract_plain(p: Path) -> ExtractResult:
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = p.read_text(encoding="cp949", errors="ignore")
    return ExtractResult(text=text, method="plain", quality=1.0)


def _extract_hwp(p: Path) -> ExtractResult:
    """HWP5/HWPX 추출 — rhwp-python(Rust PyO3, HWP5+HWPX 통합 파서, extra ``[hwp]``).

    rhwp가 .hwp(바이너리)·.hwpx 둘 다 파싱한다. 미설치/파싱 실패는 graceful degrade.
    구 pyhwp/hwp5 폴백 제거(2026-06-05): 설치돼 있던 hwp5 0.1.0은 무관 패키지였고
    ``hwp5.extract_text`` API도 부재해 항상 실패하던 죽은 경로였다.
    """
    try:
        import rhwp  # type: ignore
    except ImportError as exc:
        return ExtractResult(
            text="", method="rhwp", quality=0.0,
            error=f"HWP 추출에는 rhwp-python 필요 (pip install '.[hwp]'): {exc}",
        )
    try:
        text = rhwp.parse(str(p)).extract_text()
        return ExtractResult(text=text, method="rhwp", quality=0.95)
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


def _extract_excel(p: Path) -> ExtractResult:
    """Excel(.xlsx/.xlsm/.xls) → 시트별 행을 텍스트로.

    표 구조는 셀을 ' | '로 잇고 시트마다 '[sheet: 이름]' 헤더를 붙인다(DOCX 표와 동일 방식).
    .xlsx/.xlsm은 openpyxl(read_only·data_only=수식 대신 값), .xls는 xlrd(설치 시).
    라이브러리 미설치/파싱 실패는 graceful degrade — 빈 텍스트 + error.
    """
    suffix = p.suffix.lower().lstrip(".")
    if suffix in ("xlsx", "xlsm"):
        try:
            from openpyxl import load_workbook  # type: ignore
        except ImportError as exc:
            return ExtractResult(text="", method="openpyxl", quality=0.0,
                                 error=f"openpyxl not installed: {exc}")
        try:
            wb = load_workbook(str(p), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001
            return ExtractResult(text="", method="openpyxl", quality=0.0, error=str(exc))
        parts: list[str] = []
        for ws in wb.worksheets:
            parts.append(f"[sheet: {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None and str(c).strip()]
                if cells:
                    parts.append(" | ".join(cells))
        wb.close()
        return ExtractResult(text="\n".join(parts), method="openpyxl", quality=0.95)

    # .xls (구형 바이너리) — openpyxl 미지원이라 xlrd 필요. 미설치 시 변환 안내.
    try:
        import xlrd  # type: ignore
    except ImportError as exc:
        return ExtractResult(text="", method="xlrd", quality=0.0,
                             error=f".xls requires xlrd (or convert to .xlsx): {exc}")
    try:
        book = xlrd.open_workbook(str(p))
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="xlrd", quality=0.0, error=str(exc))
    parts = []
    for sh in book.sheets():
        parts.append(f"[sheet: {sh.name}]")
        for r in range(sh.nrows):
            cells = [str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)]
            cells = [c for c in cells if c]
            if cells:
                parts.append(" | ".join(cells))
    return ExtractResult(text="\n".join(parts), method="xlrd", quality=0.9)


def _extract_pdf(p: Path) -> ExtractResult:
    """PDF 추출.

    1) pdfminer.six (BSD-3) — 텍스트 레이어 있는 경우
    2) PyMuPDF (AGPL) — 설치된 경우 fallback
    3) Tesseract OCR — 텍스트 레이어 없는 스캔본
    """
    # 1순위: pdfminer.six
    try:
        from pdfminer.high_level import extract_text  # type: ignore

        text = extract_text(str(p)) or ""
        if text.strip():
            pages = max(1, text.count("\x0c") + 1)
            return ExtractResult(text=text, method="parser", quality=0.92, pages=pages)
    except Exception:  # noqa: BLE001
        pass

    # 2순위: PyMuPDF (AGPL 옵트인)
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(p))
        try:
            page_texts = [page.get_text() for page in doc]
            n_pages = len(page_texts)
        finally:
            doc.close()
        text = "\n".join(page_texts)
        if text.strip():
            return ExtractResult(text=text, method="parser", quality=0.95, pages=n_pages)
        # 텍스트 레이어 없음 → OCR 시도
        return _ocr_pdf_pages(p, n_pages)
    except Exception:  # noqa: BLE001
        pass

    # 3순위: pdfminer로 읽혔지만 빈 텍스트 → OCR 시도
    return _ocr_pdf_pages(p, None)


def _ocr_pdf_pages(p: Path, n_pages: int | None) -> ExtractResult:
    """PDF 페이지를 이미지로 변환 후 Tesseract OCR."""
    try:
        from pdf2image import convert_from_path  # type: ignore

        kwargs = {"dpi": 200}
        if POPPLER_PATH:
            kwargs["poppler_path"] = POPPLER_PATH
        images = convert_from_path(str(p), **kwargs)
        texts = [_tess_image(img) for img in images]
        text = "\n".join(t for t in texts if t)
        if text.strip():
            return ExtractResult(
                text=text, method="ocr", quality=0.75,
                ocr_used=True, pages=len(images),
            )
    except Exception:  # noqa: BLE001
        pass
    return ExtractResult(
        text="", method="ocr", quality=0.0, ocr_used=False,
        pages=n_pages, error="OCR failed (no text layer, pdf2image/tesseract unavailable)",
    )


def _extract_image_ocr(p: Path) -> ExtractResult:
    """이미지 파일(jpg/png/tiff 등) 직접 OCR."""
    try:
        from PIL import Image  # type: ignore

        img = Image.open(str(p))
        text = _tess_image(img)
        if text.strip():
            return ExtractResult(text=text, method="ocr", quality=0.75, ocr_used=True)
        return ExtractResult(text="", method="ocr", quality=0.0, ocr_used=True,
                             error="OCR produced empty text")
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="ocr", quality=0.0,
                             ocr_used=True, error=str(exc))


def _tess_image(img) -> str:
    """PIL Image → Tesseract OCR 텍스트. kor+eng 병행 인식."""
    import pytesseract  # type: ignore

    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    langs = "+".join(_available_tess_langs(["kor", "eng"]))
    return pytesseract.image_to_string(img, lang=langs, config="--psm 3")


def _available_tess_langs(preferred: list[str]) -> list[str]:
    """설치된 언어팩 중 preferred 교집합. 없으면 eng 폴백."""
    try:
        import pytesseract  # type: ignore

        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        installed = set(pytesseract.get_languages())
        result = [lg for lg in preferred if lg in installed]
        return result if result else ["eng"]
    except Exception:  # noqa: BLE001
        return ["eng"]
