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

# OCR DoS 가드 — 수백쪽 스캔 PDF 한 건이 pdf2image/Tesseract를 수십분~OOM으로 몰지
# 않도록 변환 페이지 수에 보수적 상한을 둔다. settings.ocr_max_pages가 있으면 그 값을,
# 없으면 모듈 상수(50)를 사용. 0 이하면 무제한으로 본다(명시적 opt-out).
_OCR_MAX_PAGES_DEFAULT = 50


def _ocr_max_pages() -> int:
    try:
        from lloydk.config import settings  # noqa: PLC0415

        v = getattr(settings, "ocr_max_pages", None)
        if v is not None:
            return int(v)
    except Exception:  # noqa: BLE001
        pass
    return _OCR_MAX_PAGES_DEFAULT


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
    if suffix in ("doc",):
        return _extract_doc(p)
    if suffix in ("xlsx", "xlsm", "xls"):
        return _extract_excel(p)
    if suffix in ("pptx", "pptm"):
        return _extract_pptx(p)
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

    데이터 누락 가드(2026-06):
      - 숨김(hidden/veryHidden) 시트도 포함한다. 숨김 시트에 비밀 수치가 들어 있을 수
        있으므로 sheet_state로 거르지 않고 모두 추출하되 헤더에 상태를 표기한다.
      - data_only=True는 '캐시된 수식 값'을 읽는다. 엑셀이 한 번도 계산·저장하지 않은
        수식 셀은 캐시가 없어 None으로 나와 누락될 수 있다. None인 수식 셀이 감지되면
        error에 경고를 남겨 후속 검수가 LibreOffice 재계산 등을 판단하게 한다.
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
        # read_only 워크북도 hidden/veryHidden 시트를 worksheets에 노출하므로
        # 추가 필터 없이 전부 순회 — sheet_state는 헤더 표기로만 사용.
        for ws in wb.worksheets:
            state = getattr(ws, "sheet_state", "visible")
            header = f"[sheet: {ws.title}]" if state == "visible" else f"[sheet: {ws.title} ({state})]"
            parts.append(header)
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None and str(c).strip()]
                if cells:
                    parts.append(" | ".join(cells))
        wb.close()

        # 수식 캐시 부재(None) 가능성 감지 — data_only 모드에서 수식 셀이 None이면
        # 값 누락. read_only 워크북은 셀 데이터타입을 못 보므로 별도 워크북을
        # data_only=False로 재오픈해 수식 존재 여부만 가볍게 점검한다(실패는 무시).
        warn = _excel_formula_cache_warning(str(p))
        return ExtractResult(
            text="\n".join(parts), method="openpyxl", quality=0.95, error=warn,
        )

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


def _excel_formula_cache_warning(path: str) -> str | None:
    """data_only=True에서 수식 캐시 부재로 값이 None일 위험을 가볍게 점검.

    data_only=False로 재오픈해 수식 셀(value가 '='로 시작) 중 별도 캐시 워크북에서
    None인 게 있는지 본다. 라이브러리/파싱 실패는 조용히 None(경고 없음) — best-effort.
    LibreOffice 재계산 같은 본격 복구는 범위 밖(risks 참조).
    """
    try:
        from openpyxl import load_workbook  # type: ignore

        wb_f = load_workbook(path, read_only=True, data_only=False)
        wb_v = load_workbook(path, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        return None
    missing = 0
    try:
        for ws_f, ws_v in zip(wb_f.worksheets, wb_v.worksheets):
            for row_f, row_v in zip(ws_f.iter_rows(), ws_v.iter_rows()):
                for cf, cv in zip(row_f, row_v):
                    fv = cf.value
                    if isinstance(fv, str) and fv.startswith("=") and cv.value is None:
                        missing += 1
    except Exception:  # noqa: BLE001
        return None
    finally:
        wb_f.close()
        wb_v.close()
    if missing:
        return (
            f"warning: {missing} formula cell(s) had no cached value (data_only) — "
            "값이 누락됐을 수 있음. LibreOffice 등으로 재계산 후 재추출 권장."
        )
    return None


def _extract_doc(p: Path) -> ExtractResult:
    """구형 .doc(Word 97-2003) → antiword CLI 추출.

    python-docx는 .docx(OOXML) 전용이라 .doc 바이너리는 못 읽는다. 시스템에 antiword가
    있으면 그것으로 텍스트 추출, 없으면 graceful degrade(.docx 변환 안내).
    한국어는 antiword 매핑 의존 — UTF-8 매핑(-m UTF-8.txt) 우선 시도 후 기본.
    """
    import shutil
    import subprocess

    exe = shutil.which("antiword")
    if not exe:
        return ExtractResult(
            text="", method="antiword", quality=0.0,
            error=".doc 추출에는 antiword 필요 (또는 .docx로 변환)",
        )
    out = None
    for args in ([exe, "-m", "UTF-8.txt", str(p)], [exe, str(p)]):
        try:
            out = subprocess.run(args, capture_output=True, timeout=60)
        except Exception as exc:  # noqa: BLE001
            return ExtractResult(text="", method="antiword", quality=0.0, error=str(exc))
        if out.returncode == 0 and out.stdout.strip():
            return ExtractResult(
                text=out.stdout.decode("utf-8", errors="replace"),
                method="antiword", quality=0.9,
            )
    err = (out.stderr.decode("utf-8", errors="replace") if out and out.stderr else "")[:200]
    return ExtractResult(text="", method="antiword", quality=0.0,
                         error=err or "antiword: no text extracted")


def _extract_pptx(p: Path) -> ExtractResult:
    """PowerPoint(.pptx/.pptm) → 슬라이드별 도형 텍스트 + 표.

    슬라이드마다 '[slide N]' 헤더. 텍스트 도형·표 셀을 추출(이미지/차트 제외).
    .ppt(구형 바이너리)는 python-pptx 미지원 — 미지원 처리(변환 권장).
    """
    try:
        from pptx import Presentation  # type: ignore
    except ImportError as exc:
        return ExtractResult(text="", method="pptx", quality=0.0,
                             error=f"python-pptx not installed: {exc}")
    try:
        prs = Presentation(str(p))
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="pptx", quality=0.0, error=str(exc))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides):
        parts.append(f"[slide {i + 1}]")
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                for para in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in para.runs) or para.text
                    if t.strip():
                        parts.append(t)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [c.text for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
    return ExtractResult(text="\n".join(parts), method="pptx", quality=0.95)


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
    """PDF 페이지를 이미지로 변환 후 Tesseract OCR.

    DoS 가드: convert_from_path를 first_page/last_page로 [1, max_pages] 범위만
    렌더한다. 상한을 넘는 페이지는 변환·OCR하지 않고 경고를 error에 남긴다(잘림 표기).
    상한이 0 이하면 무제한(opt-out).
    """
    try:
        from pdf2image import convert_from_path  # type: ignore

        kwargs: dict = {"dpi": 200}
        if POPPLER_PATH:
            kwargs["poppler_path"] = POPPLER_PATH

        max_pages = _ocr_max_pages()
        truncated = False
        last_page: int | None = None
        if max_pages and max_pages > 0:
            # n_pages를 아는 경우 초과 여부를 미리 판정, 모르면 상한까지만 렌더.
            last_page = max_pages
            if n_pages is not None and n_pages > max_pages:
                truncated = True
            kwargs["first_page"] = 1
            kwargs["last_page"] = last_page

        images = convert_from_path(str(p), **kwargs)
        # n_pages를 몰랐어도 렌더 결과가 상한과 같으면 잘렸을 수 있음(보수적 표기).
        if last_page is not None and len(images) >= last_page and n_pages is None:
            truncated = True
        texts = [_tess_image(img) for img in images]
        text = "\n".join(t for t in texts if t)
        if text.strip():
            note = (
                f"OCR truncated to first {last_page} of {n_pages or '?'} pages (DoS guard)"
                if truncated else None
            )
            return ExtractResult(
                text=text, method="ocr", quality=0.75,
                ocr_used=True, pages=len(images), error=note,
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
