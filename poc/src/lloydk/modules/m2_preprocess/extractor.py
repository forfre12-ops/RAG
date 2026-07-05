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
from dataclasses import dataclass, field
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


SUPPORTED_FORMAT_GROUPS: dict[str, tuple[str, ...]] = {
    "plain": ("txt", "md", "log", "csv"),
    "hwp": ("hwp", "hwpx"),
    "word": ("docx", "doc"),
    "excel": ("xlsx", "xlsm", "xls"),
    "powerpoint": ("pptx", "pptm"),
    "pdf": ("pdf",),
    "image_ocr": ("jpg", "jpeg", "png", "tiff", "tif", "bmp", "webp"),
}
SUPPORTED_PARSE_EXTENSIONS: tuple[str, ...] = tuple(
    ext for group in SUPPORTED_FORMAT_GROUPS.values() for ext in group
)


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
class ExtractedTable:
    source: str
    name: str
    rows: list[list[str]]
    sheet: str | None = None
    page: int | None = None
    table_index: int | None = None


@dataclass
class ExtractResult:
    text: str
    method: str           # parser/rhwp/pyhwp/ocr/ocr_llm/libreoffice/plain
    quality: float        # 0.0~1.0 (추정)
    ocr_used: bool = False
    pages: int | None = None
    error: str | None = None
    # 표 커버리지 신호 — 원문에 표가 있는데 셀 텍스트가 추출본에 빠졌을 때 "incomplete".
    # HWP/HWPX에서 rhwp가 표·글상자 셀을 흘리면 본문만 분류기에 들어가 표 속 영업비밀이
    # '조용히' 미탐(FNR)되는 것을 가시화한다. None=해당없음/회수됨/판정불가. 메서드 고정
    # quality(0.95 등)는 '무엇을 놓쳤나'가 아니라 '어떤 파서를 썼나'만 반영해 부분손실을
    # 가리므로, 이 신호로 검수 라우팅을 발동한다(등급 불변 — FNR-safe).
    table_coverage: str | None = None
    tables: list[ExtractedTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def extract(path: str | Path) -> ExtractResult:
    p = Path(path)
    suffix = p.suffix.lower().lstrip(".")
    if suffix in SUPPORTED_FORMAT_GROUPS["plain"]:
        return _extract_plain(p)
    if suffix in SUPPORTED_FORMAT_GROUPS["hwp"]:
        return _extract_hwp(p)
    if suffix == "docx":
        return _extract_docx(p)
    if suffix == "doc":
        return _extract_doc(p)
    if suffix in SUPPORTED_FORMAT_GROUPS["excel"]:
        return _extract_excel(p)
    if suffix in SUPPORTED_FORMAT_GROUPS["powerpoint"]:
        return _extract_pptx(p)
    if suffix == "pdf":
        return _extract_pdf(p)
    if suffix in SUPPORTED_FORMAT_GROUPS["image_ocr"]:
        return _extract_image_ocr(p)
    return ExtractResult(text="", method="plain", quality=0.0, error=f"unsupported: {suffix}")


def _extract_plain(p: Path) -> ExtractResult:
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = p.read_text(encoding="cp949", errors="ignore")
    return ExtractResult(text=text, method="plain", quality=1.0)


def _cell_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _trim_trailing_empty(row: list[str]) -> list[str]:
    while row and not row[-1]:
        row.pop()
    return row


def _row_has_text(row: list[str]) -> bool:
    return any(c.strip() for c in row)


def _table_text_line(row: list[str]) -> str:
    return " | ".join(c for c in row if c.strip())


def _tables_to_text(tables: list[ExtractedTable]) -> str:
    lines: list[str] = []
    for table in tables:
        for row in table.rows:
            line = _table_text_line(row)
            if line:
                lines.append(line)
    return "\n".join(lines)


def _append_if_missing(text: str, addition: str) -> str:
    if not addition.strip():
        return text or ""
    norm = "".join((text or "").split())
    missing = []
    for line in addition.splitlines():
        if line.strip() and "".join(line.split()) not in norm:
            missing.append(line)
    if not missing:
        return text or ""
    return ((text or "").rstrip() + "\n" + "\n".join(missing)).strip()


def _warn_once(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def _zip_names(path: Path) -> list[str]:
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    except Exception:  # noqa: BLE001
        return []


_PACKAGE_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")
_PACKAGE_OCR_LIMIT = 30


def _ocr_package_images(
    p: Path,
    *,
    prefix: str,
    label: str,
    warnings: list[str],
) -> list[str]:
    import io
    import zipfile

    names = [
        n for n in _zip_names(p)
        if n.startswith(prefix) and n.lower().endswith(_PACKAGE_IMAGE_SUFFIXES)
    ]
    if not names:
        return []
    try:
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore  # noqa: F401
    except ImportError:
        _warn_once(warnings, f"{label}_media_ocr_unavailable")
        return []

    parts: list[str] = []
    try:
        with zipfile.ZipFile(p) as zf:
            for name in names[:_PACKAGE_OCR_LIMIT]:
                try:
                    with Image.open(io.BytesIO(zf.read(name))) as img:
                        text = _tess_image(img)
                except Exception:  # noqa: BLE001
                    continue
                if text and text.strip():
                    parts.append(f"[{label} image OCR {name}]\n{text.strip()}")
                    try:
                        warnings.remove(f"{label}_media_not_ocrd")
                    except ValueError:
                        pass
                    _warn_once(warnings, f"{label}_media_ocr_extracted")
    except Exception as exc:  # noqa: BLE001
        _warn_once(warnings, f"{label}_media_ocr_error:{type(exc).__name__}")
        return parts

    if len(names) > _PACKAGE_OCR_LIMIT:
        _warn_once(warnings, f"{label}_media_ocr_truncated")
    if not parts:
        _warn_once(warnings, f"{label}_media_ocr_empty")
    return parts


def _xml_text(xml: str) -> str:
    import html as _html
    import re as _re

    chunks = _re.findall(r"<(?:\w+:)?t\b[^>]*>(.*?)</(?:\w+:)?t>", xml, _re.S)
    text = "\n".join(_html.unescape(_re.sub(r"<[^>]+>", "", c)).strip() for c in chunks)
    return "\n".join(line for line in text.splitlines() if line.strip())


def _html_to_text(raw: str) -> str:
    import html as _html
    import re as _re

    raw = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=_re.S | _re.I)
    text = _re.sub(r"<[^>]+>", " ", raw)
    text = _html.unescape(text)
    text = _re.sub(r"[ \t\u00a0]+", " ", text)
    text = _re.sub(r"\s*\n\s*", "\n", text).strip()
    return text


def _html_tables(raw: str, *, source: str, name_prefix: str) -> list[ExtractedTable]:
    import html as _html
    import re as _re

    tables: list[ExtractedTable] = []
    for tidx, table in enumerate(
        _re.findall(r"<table\b[^>]*>(.*?)</table>", raw, _re.S | _re.I),
        start=1,
    ):
        rows: list[list[str]] = []
        for tr in _re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, _re.S | _re.I):
            cells: list[str] = []
            for cell in _re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, _re.S | _re.I):
                text = _html_to_text(_html.unescape(cell))
                cells.append(text)
            cells = _trim_trailing_empty(cells)
            if _row_has_text(cells):
                rows.append(cells)
        if rows:
            tables.append(
                ExtractedTable(
                    source=source,
                    name=f"{name_prefix} table {tidx}",
                    rows=rows,
                    table_index=tidx,
                )
            )
    return tables


def _extract_hwp(p: Path) -> ExtractResult:
    """HWP5/HWPX 추출 — rhwp-python(Rust PyO3, HWP5+HWPX 통합 파서, extra ``[hwp]``).

    rhwp가 .hwp(바이너리)·.hwpx 둘 다 파싱한다. 미설치/파싱 실패는 graceful degrade.
    구 pyhwp/hwp5 폴백 제거(2026-06-05): 설치돼 있던 hwp5 0.1.0은 무관 패키지였고
    ``hwp5.extract_text`` API도 부재해 항상 실패하던 죽은 경로였다.

    알려진 한계(실측 2026-06-27, 공개 보도자료·서식 .hwp 대상, rhwp 0.5.1·0.8.0 동일):
      본문 단락은 정상 추출하나 **표·글상자 셀 텍스트를 거의 전부 흘린다**
      (noori.hwp 표셀 29/31 누락, basicsReport.hwp 23/23 누락). 영업비밀 문서는
      등급·원가·담당 정보가 표에 들어가는 일이 많아 '조용한 표 미추출'이 곧 미탐이다.

    표 보강(opt-in): pyhwp([hwp-tables] extra)가 설치돼 있으면 표 셀까지 렌더하는
      pyhwp 경로로 더 많은 텍스트를 뽑아 그쪽을 채택(_hwp_text_via_pyhwp). pyhwp는
      AGPL이라 PyMuPDF([pdf-agpl])와 동일하게 기본 제외·옵트인. **AGPL 전염을 막기
      위해 in-process import가 아니라 hwp5html 콘솔 스크립트를 별도 프로세스로 호출**
      (데이터는 파일로만 주고받음 → arm's length). 미설치 시 rhwp 결과 유지하되,
      rhwp가 빈 텍스트면 표 전용 문서 가능성을 경고로 surface한다.
    """
    try:
        import rhwp  # type: ignore
    except ImportError as exc:
        return ExtractResult(
            text="", method="rhwp", quality=0.0,
            error=f"HWP 추출에는 rhwp-python 필요 (pip install '.[hwp]'): {exc}",
        )
    try:
        doc = rhwp.parse(str(p))
        text = doc.extract_text()
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="rhwp", quality=0.0, error=str(exc))

    # 표·글상자 보강(opt-in pyhwp). rhwp보다 많이 뽑히면 = 표 셀을 회수했단 뜻이라 채택.
    warnings: list[str] = []
    tables: list[ExtractedTable] = []
    if p.suffix.lower() == ".hwpx":
        tables = _hwpx_tables(p.read_bytes(), source="hwpx")
        if tables:
            text = _append_if_missing(text or "", _tables_to_text(tables))
            _warn_once(warnings, "hwpx_tables_structured")

    full, pyhwp_tables = _hwp_via_pyhwp(p)
    if full and len(full) > len(text or ""):
        pyhwp_warnings = ["hwp_tables_extracted_by_hwp5html"] if pyhwp_tables else [
            "hwp_tables_flattened_by_hwp5html"
        ]
        return ExtractResult(
            text=full,
            method="pyhwp",
            quality=0.9,
            tables=pyhwp_tables,
            warnings=pyhwp_warnings,
        )

    if not text or not text.strip():
        # 빈 추출 = 성공 아님. 표/글상자 전용 HWP일 가능성을 경고로 surface.
        # ([hwp-tables] 미설치 시 표 회수 불가 — 설치하면 위 pyhwp 경로로 복구.)
        return ExtractResult(
            text="", method="rhwp", quality=0.0,
            error="rhwp가 본문 텍스트를 추출하지 못함 — 표·글상자 전용 문서일 수 있음. "
                  "표 셀 회수에는 pyhwp 필요: pip install '.[hwp-tables]' (AGPL).",
        )
    # 본문은 뽑혔지만 표 셀이 빠졌을 수 있다(rhwp의 조용한 표 미추출) — 커버리지 판정.
    coverage = _hwp_table_coverage(p, doc, text)
    if coverage == "incomplete":
        _warn_once(warnings, "hwp_table_cells_may_be_missing")
    return ExtractResult(
        text=text,
        method="rhwp",
        quality=0.95,
        table_coverage=coverage,
        tables=tables,
        warnings=warnings,
    )


def _hwp_table_coverage(p: Path, doc, text: str) -> str | None:
    """rhwp가 본문은 뽑았으나 표 셀을 흘렸는지 판정 → "incomplete" | None.

    표 속 등급·원가·담당·임원보상 같은 영업비밀이 본문에 없으면 분류기가 못 보고
    저등급으로 '조용히' 미탐한다. 메서드 고정 quality(0.95)로는 안 잡히는 사각.

    - **.hwpx**: 원본 zip의 section XML에서 표 셀 텍스트(<...:t>)를 뽑아 추출본과 대조.
      셀 텍스트가 추출본에 없으면 "incomplete"(정밀 — rhwp가 회수했으면 flag 안 함).
    - **.hwp(바이너리)**: 셀 텍스트는 rhwp HWPX 변환에도 빠지므로 대조 불가. 표 *구조*
      존재만 보고 "incomplete"(pyhwp로 회수했으면 이 경로에 안 옴). 보수적(FNR-safe).

    판정 실패·예외·표 없음은 None(graceful) — 과라우팅을 피한다.
    """
    try:
        suffix = p.suffix.lower()
        if suffix == ".hwpx":
            if _hwpx_uncaptured_table_cells(p.read_bytes(), text or ""):
                return "incomplete"
            return None
        if suffix == ".hwp":
            hwpx_bytes = doc.to_hwpx_bytes()
            if _hwpx_bytes_has_table(hwpx_bytes):
                return "incomplete"
            return None
    except Exception:  # noqa: BLE001
        return None
    return None


def _hwpx_uncaptured_table_cells(hwpx_bytes: bytes, extracted_text: str) -> bool:
    """HWPX(zip)의 표 셀 텍스트 중 추출본에 없는 게 하나라도 있으면 True.

    section XML의 <...:tbl> 블록 안 <...:t> 텍스트를 모아, 공백 제거 후 추출본(공백 제거)에
    부분문자열로 존재하는지 본다. 접두 네임스페이스 무관(hp:/hhs:/무접두 모두 매칭).
    셀 텍스트가 이미 본문에 있으면(회수됨/반복) flag 안 함 — 오탐 최소화.
    """
    import html as _html
    import io
    import re as _re
    import zipfile

    norm = _re.sub(r"\s+", "", extracted_text or "")
    try:
        z = zipfile.ZipFile(io.BytesIO(hwpx_bytes))
    except Exception:  # noqa: BLE001
        return False
    names = [n for n in z.namelist() if n.lower().endswith(".xml") and "section" in n.lower()]
    if not names:
        names = [n for n in z.namelist() if n.lower().endswith(".xml")]
    for n in names:
        try:
            xml = z.read(n).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for tbl in _re.findall(r"<(?:\w+:)?tbl\b[^>]*>(.*?)</(?:\w+:)?tbl>", xml, _re.S):
            for cell in _re.findall(r"<(?:\w+:)?t\b[^>]*>(.*?)</(?:\w+:)?t>", tbl, _re.S):
                c = _html.unescape(_re.sub(r"<[^>]+>", "", cell)).strip()
                if len(c) >= 2 and _re.sub(r"\s+", "", c) not in norm:
                    return True
    return False


def _hwpx_tables(hwpx_bytes: bytes, *, source: str = "hwpx") -> list[ExtractedTable]:
    import html as _html
    import io
    import re as _re
    import zipfile

    try:
        z = zipfile.ZipFile(io.BytesIO(hwpx_bytes))
    except Exception:  # noqa: BLE001
        return []
    names = [n for n in z.namelist() if n.lower().endswith(".xml") and "section" in n.lower()]
    if not names:
        names = [n for n in z.namelist() if n.lower().endswith(".xml")]

    out: list[ExtractedTable] = []
    for name in names:
        try:
            xml = z.read(name).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for tbl in _re.findall(r"<(?:\w+:)?tbl\b[^>]*>(.*?)</(?:\w+:)?tbl>", xml, _re.S):
            rows: list[list[str]] = []
            tr_blocks = _re.findall(r"<(?:\w+:)?tr\b[^>]*>(.*?)</(?:\w+:)?tr>", tbl, _re.S)
            if not tr_blocks:
                tr_blocks = [tbl]
            for tr in tr_blocks:
                row: list[str] = []
                for tc in _re.findall(r"<(?:\w+:)?tc\b[^>]*>(.*?)</(?:\w+:)?tc>", tr, _re.S):
                    texts = []
                    for t in _re.findall(r"<(?:\w+:)?t\b[^>]*>(.*?)</(?:\w+:)?t>", tc, _re.S):
                        cell = _html.unescape(_re.sub(r"<[^>]+>", "", t)).strip()
                        if cell:
                            texts.append(cell)
                    row.append("\n".join(texts).strip())
                row = _trim_trailing_empty(row)
                if _row_has_text(row):
                    rows.append(row)
            if rows:
                out.append(
                    ExtractedTable(
                        source=source,
                        name=f"{Path(name).name} table {len(out) + 1}",
                        rows=rows,
                        table_index=len(out) + 1,
                    )
                )
    return out


def _hwpx_bytes_has_table(hwpx_bytes: bytes) -> bool:
    """HWPX(zip) 어느 XML에든 표 구조(<...:tbl>)가 있으면 True (존재만 판정)."""
    import io
    import re as _re
    import zipfile

    try:
        z = zipfile.ZipFile(io.BytesIO(hwpx_bytes))
    except Exception:  # noqa: BLE001
        return False
    for n in z.namelist():
        if not n.lower().endswith(".xml"):
            continue
        try:
            xml = z.read(n).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        if _re.search(r"<(?:\w+:)?tbl\b", xml):
            return True
    return False


def _hwp5html_cmd() -> str | None:
    """pyhwp ``hwp5html`` 실행파일 경로 — 환경변수 > 현재 venv Scripts > PATH. 없으면 None.

    AGPL 격리를 위해 pyhwp는 라이브러리로 import하지 않고 이 실행파일로만 쓴다.
    """
    import shutil
    import sys

    env = os.environ.get("HWP5HTML_CMD")
    if env and Path(env).exists():
        return env
    exe = "hwp5html.exe" if os.name == "nt" else "hwp5html"
    local = Path(sys.executable).parent / exe
    if local.exists():
        return str(local)
    return shutil.which("hwp5html")


def _hwp_via_pyhwp(p: Path) -> tuple[str | None, list[ExtractedTable]]:
    """pyhwp(hwp5html CLI)로 .hwp의 표·글상자 포함 전체 텍스트 추출 (opt-in, AGPL).

    rhwp.extract_text()가 표 셀을 흘리는 것을 보강한다. pyhwp의 HWP5→XHTML 변환은
    표 셀까지 렌더하므로 결과 xhtml에서 태그를 제거해 평문을 얻는다.

    **AGPL 격리**: pyhwp를 in-process import하지 않고 ``hwp5html`` 콘솔 스크립트를
    별도 프로세스로 호출한다(임시 디렉터리로만 데이터 교환 → arm's length). AGPL
    경계가 pyhwp 실행파일에 국한돼 본 코드로 전염되지 않는다. [hwp-tables] extra
    (=pyhwp) 설치 시에만 동작.

    HWP5(바이너리 .hwp) 전용 — .hwpx(OWPML/zip)는 pyhwp 미지원이라 None.
    실행파일 부재·변환 실패는 None(graceful) → 호출부가 rhwp 결과를 사용.
    """
    if p.suffix.lower() != ".hwp":
        return None, []
    cmd = _hwp5html_cmd()
    if not cmd:
        return None, []

    import html as _html
    import re as _re
    import shutil
    import subprocess
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="hwp5html_")
    try:
        proc = subprocess.run(
            [cmd, "--output", tmpdir, str(p)],
            capture_output=True, timeout=120,
        )
        xhtml = Path(tmpdir) / "index.xhtml"
        if proc.returncode != 0 or not xhtml.exists():
            return None, []
        raw = xhtml.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None, []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    raw = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=_re.S)
    text = _re.sub(r"<[^>]+>", " ", raw)
    text = _html.unescape(text)
    text = _re.sub(r"[ \t ]+", " ", text)
    text = _re.sub(r"\s*\n\s*", "\n", text).strip()
    tables = _html_tables(raw, source="hwp", name_prefix=p.name)
    text = _append_if_missing(_html_to_text(raw), _tables_to_text(tables))
    return (text or None), tables


def _hwp_text_via_pyhwp(p: Path) -> str | None:
    text, _tables = _hwp_via_pyhwp(p)
    return text


def _docx_collect_tables(container, *, source: str, tables: list[ExtractedTable],
                         parts: list[str]) -> None:
    for idx, tbl in enumerate(getattr(container, "tables", []), start=1):
        rows: list[list[str]] = []
        for row in tbl.rows:
            cells = _trim_trailing_empty([_cell_text(cell.text) for cell in row.cells])
            if _row_has_text(cells):
                rows.append(cells)
                parts.append(_table_text_line(cells))
        if rows:
            tables.append(
                ExtractedTable(
                    source=source,
                    name=f"{source} table {idx}",
                    rows=rows,
                    table_index=idx,
                )
            )


def _docx_ooxml_extras(p: Path, warnings: list[str]) -> list[str]:
    import re as _re
    import zipfile

    extras: list[str] = []
    names = _zip_names(p)
    if any(n.startswith("word/media/") for n in names):
        _warn_once(warnings, "docx_media_not_ocrd")
        extras.extend(_ocr_package_images(p, prefix="word/media/", label="docx", warnings=warnings))
    if any(n.startswith("word/charts/") for n in names):
        _warn_once(warnings, "docx_charts_not_extracted")
    if any(n.startswith("word/embeddings/") for n in names):
        _warn_once(warnings, "docx_embedded_objects_not_extracted")

    try:
        with zipfile.ZipFile(p) as zf:
            named_parts = {
                "word/comments.xml": "docx_comments_extracted",
                "word/footnotes.xml": "docx_footnotes_extracted",
                "word/endnotes.xml": "docx_endnotes_extracted",
            }
            for name, warning in named_parts.items():
                if name not in names:
                    continue
                text = _xml_text(zf.read(name).decode("utf-8", errors="replace"))
                if text:
                    extras.append(f"[{name}]\n{text}")
                    _warn_once(warnings, warning)

            xml_parts = [
                n for n in names
                if n.startswith("word/") and n.endswith(".xml")
                and (n == "word/document.xml" or n.startswith("word/header") or n.startswith("word/footer"))
            ]
            for name in xml_parts:
                xml = zf.read(name).decode("utf-8", errors="replace")
                for box in _re.findall(
                    r"<(?:\w+:)?txbxContent\b[^>]*>(.*?)</(?:\w+:)?txbxContent>",
                    xml,
                    _re.S,
                ):
                    text = _xml_text(box)
                    if text:
                        extras.append(f"[{name}:textbox]\n{text}")
                        _warn_once(warnings, "docx_textboxes_extracted")
    except Exception:  # noqa: BLE001
        return extras
    return extras


def _docx_ooxml_tables(p: Path, warnings: list[str]) -> list[ExtractedTable]:
    import re as _re
    import zipfile

    tables: list[ExtractedTable] = []
    names = _zip_names(p)
    xml_parts = [
        n for n in names
        if n.startswith("word/") and n.endswith(".xml")
        and (n == "word/document.xml" or n.startswith("word/header") or n.startswith("word/footer"))
    ]
    try:
        with zipfile.ZipFile(p) as zf:
            for name in xml_parts:
                xml = zf.read(name).decode("utf-8", errors="replace")
                for raw_tbl in _re.findall(
                    r"<(?:\w+:)?tbl\b[^>]*>(.*?)</(?:\w+:)?tbl>",
                    xml,
                    _re.S,
                ):
                    rows: list[list[str]] = []
                    for raw_tr in _re.findall(
                        r"<(?:\w+:)?tr\b[^>]*>(.*?)</(?:\w+:)?tr>",
                        raw_tbl,
                        _re.S,
                    ):
                        row: list[str] = []
                        for raw_tc in _re.findall(
                            r"<(?:\w+:)?tc\b[^>]*>(.*?)</(?:\w+:)?tc>",
                            raw_tr,
                            _re.S,
                        ):
                            row.append(_xml_text(raw_tc))
                        row = _trim_trailing_empty(row)
                        if _row_has_text(row):
                            rows.append(row)
                    if rows:
                        tables.append(
                            ExtractedTable(
                                source="docx_ooxml",
                                name=f"{Path(name).name} table {len(tables) + 1}",
                                rows=rows,
                                table_index=len(tables) + 1,
                            )
                        )
    except Exception:  # noqa: BLE001
        return tables
    if tables:
        _warn_once(warnings, "docx_ooxml_tables_extracted")
    return tables


def _docx_ooxml_text(p: Path, warnings: list[str]) -> str:
    import zipfile

    names = _zip_names(p)
    xml_parts = [
        n for n in names
        if n.startswith("word/") and n.endswith(".xml")
        and (n == "word/document.xml" or n.startswith("word/header") or n.startswith("word/footer"))
    ]
    lines: list[str] = []
    try:
        with zipfile.ZipFile(p) as zf:
            for name in xml_parts:
                text = _xml_text(zf.read(name).decode("utf-8", errors="replace"))
                if text:
                    lines.extend(text.splitlines())
    except Exception:  # noqa: BLE001
        return ""
    text = "\n".join(line for line in lines if line.strip())
    return text


def _extract_docx(p: Path) -> ExtractResult:
    try:
        from docx import Document  # type: ignore

        doc = Document(str(p))
        parts = [para.text for para in doc.paragraphs if para.text]
        tables: list[ExtractedTable] = []
        warnings: list[str] = []

        _docx_collect_tables(doc, source="docx", tables=tables, parts=parts)
        for sidx, section in enumerate(doc.sections, start=1):
            for label in (
                "header",
                "footer",
                "first_page_header",
                "first_page_footer",
                "even_page_header",
                "even_page_footer",
            ):
                block = getattr(section, label, None)
                if block is None:
                    continue
                block_parts = [p.text for p in block.paragraphs if p.text]
                if block_parts:
                    parts.append(f"[docx section {sidx} {label}]")
                    parts.extend(block_parts)
                _docx_collect_tables(
                    block,
                    source=f"docx_{label}",
                    tables=tables,
                    parts=parts,
                )

        if not tables:
            ooxml_tables = _docx_ooxml_tables(p, warnings)
            if ooxml_tables:
                tables.extend(ooxml_tables)
                table_text = _tables_to_text(ooxml_tables)
                merged = _append_if_missing("\n".join(parts), table_text)
                parts = merged.splitlines() if merged else []

        parts.extend(_docx_ooxml_extras(p, warnings))
        ooxml_text = _docx_ooxml_text(p, warnings)
        if ooxml_text:
            current = "\n".join(parts)
            merged = _append_if_missing(current, ooxml_text)
            if merged != current:
                _warn_once(warnings, "docx_ooxml_text_extracted")
            parts = merged.splitlines() if merged else []
        return ExtractResult(
            text="\n".join(parts),
            method="parser",
            quality=0.97,
            tables=tables,
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(text="", method="parser", quality=0.0, error=str(exc))


def _excel_package_warnings(p: Path, warnings: list[str]) -> None:
    names = _zip_names(p)
    if any(n.startswith("xl/media/") for n in names):
        _warn_once(warnings, "excel_media_not_ocrd")
    if any(n.startswith("xl/charts/") for n in names):
        _warn_once(warnings, "excel_charts_not_extracted")
    if any(n.startswith("xl/drawings/") for n in names):
        _warn_once(warnings, "excel_drawings_text_may_be_missing")
    if any(n.startswith("xl/embeddings/") for n in names):
        _warn_once(warnings, "excel_embedded_objects_not_extracted")


def _excel_auxiliary_texts(path: str, warnings: list[str]) -> list[str]:
    parts: list[str] = []
    try:
        from openpyxl import load_workbook  # type: ignore

        wb = load_workbook(path, read_only=False, data_only=False)
    except Exception:  # noqa: BLE001
        return parts
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    coord = f"{ws.title}!{cell.coordinate}"
                    if cell.comment and cell.comment.text:
                        parts.append(f"[excel comment {coord}] {cell.comment.text.strip()}")
                        _warn_once(warnings, "excel_comments_extracted")
                    if cell.hyperlink and cell.hyperlink.target:
                        parts.append(f"[excel hyperlink {coord}] {cell.hyperlink.target}")
                        _warn_once(warnings, "excel_hyperlinks_extracted")
                    if isinstance(cell.value, str) and cell.value.startswith("="):
                        parts.append(f"[excel formula {coord}] {cell.value}")
                        _warn_once(warnings, "excel_formulas_extracted")
    except Exception:  # noqa: BLE001
        return parts
    finally:
        wb.close()
    return parts


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
      - 병합 셀(merged): read_only는 병합정보를 노출하지 않아, 세로로 병합된 분류/카테고리
        라벨이 첫 행에만 남고 나머지 행에서 끊긴다. _excel_merged_values로 별도 패스를 떠서
        각 병합 범위의 앵커 값을 '걸친 모든 행의 맨 왼쪽 열'에 복제한다(가로 병합은 앵커 한
        칸만 유지해 중복 노이즈 회피).
    """
    suffix = p.suffix.lower().lstrip(".")
    warnings: list[str] = []
    if suffix in ("xlsx", "xlsm"):
        _excel_package_warnings(p, warnings)
        try:
            from openpyxl import load_workbook  # type: ignore
        except ImportError as exc:
            return ExtractResult(text="", method="openpyxl", quality=0.0,
                                 error=f"openpyxl not installed: {exc}")
        try:
            wb = load_workbook(str(p), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001
            return ExtractResult(text="", method="openpyxl", quality=0.0, error=str(exc))
        merged = _excel_merged_values(str(p))
        parts: list[str] = []
        tables: list[ExtractedTable] = []
        # read_only 워크북도 hidden/veryHidden 시트를 worksheets에 노출하므로
        # 추가 필터 없이 전부 순회 — sheet_state는 헤더 표기로만 사용.
        for ws_idx, ws in enumerate(wb.worksheets, start=1):
            state = getattr(ws, "sheet_state", "visible")
            header = f"[sheet: {ws.title}]" if state == "visible" else f"[sheet: {ws.title} ({state})]"
            parts.append(header)
            mmap = merged.get(ws.title)
            table_rows: list[list[str]] = []
            if not mmap:
                # 병합 없는 시트는 기존 fast path(values_only) 유지.
                for row in ws.iter_rows(values_only=True):
                    cells = _trim_trailing_empty([_cell_text(c) for c in row])
                    if _row_has_text(cells):
                        table_rows.append(cells)
                        parts.append(_table_text_line(cells))
                if table_rows:
                    tables.append(
                        ExtractedTable(
                            source="excel",
                            name=ws.title,
                            rows=table_rows,
                            sheet=ws.title,
                            table_index=ws_idx,
                        )
                    )
                continue
            # 병합 있는 시트만 좌표를 추적해 None 셀에 전파값을 채운다.
            # read_only는 빈 칸을 EmptyCell(좌표 속성 없음)로 주므로, 행마다 첫
            # 실셀의 (row, column)에서 행번호·열 오프셋을 잡아 위치를 보정한다.
            for row in ws.iter_rows():
                row_no = col_base = None
                for idx, cell in enumerate(row):
                    if hasattr(cell, "column"):
                        row_no, col_base = cell.row, cell.column - idx
                        break
                cells = []
                for idx, cell in enumerate(row):
                    v = cell.value
                    if v is None and row_no is not None:
                        v = mmap.get((row_no, col_base + idx))
                    cells.append(_cell_text(v))
                cells = _trim_trailing_empty(cells)
                if _row_has_text(cells):
                    table_rows.append(cells)
                    parts.append(_table_text_line(cells))
            if table_rows:
                tables.append(
                    ExtractedTable(
                        source="excel",
                        name=ws.title,
                        rows=table_rows,
                        sheet=ws.title,
                        table_index=ws_idx,
                    )
                )
        wb.close()

        # 수식 캐시 부재(None) 가능성 감지 — data_only 모드에서 수식 셀이 None이면
        # 값 누락. read_only 워크북은 셀 데이터타입을 못 보므로 별도 워크북을
        # data_only=False로 재오픈해 수식 존재 여부만 가볍게 점검한다(실패는 무시).
        parts.extend(_excel_auxiliary_texts(str(p), warnings))
        parts.extend(_ocr_package_images(p, prefix="xl/media/", label="excel", warnings=warnings))
        warn = _excel_formula_cache_warning(str(p))
        if warn:
            _warn_once(warnings, "excel_formula_cached_values_missing")
        return ExtractResult(
            text="\n".join(parts),
            method="openpyxl",
            quality=0.95,
            error=warn,
            tables=tables,
            warnings=warnings,
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
    tables: list[ExtractedTable] = []
    for sidx, sh in enumerate(book.sheets(), start=1):
        parts.append(f"[sheet: {sh.name}]")
        table_rows: list[list[str]] = []
        for r in range(sh.nrows):
            cells = _trim_trailing_empty([_cell_text(sh.cell_value(r, c)) for c in range(sh.ncols)])
            if _row_has_text(cells):
                table_rows.append(cells)
                parts.append(_table_text_line(cells))
        if table_rows:
            tables.append(
                ExtractedTable(
                    source="excel",
                    name=sh.name,
                    rows=table_rows,
                    sheet=sh.name,
                    table_index=sidx,
                )
            )
    return ExtractResult(text="\n".join(parts), method="xlrd", quality=0.9, tables=tables)


# 병합 셀 세로 전파 상한 — 병리적으로 큰 병합(예: A1:A1048576)이 맵을 폭발시키지
# 않도록 범위당 전파 행 수에 상한. 초과 시 그 범위는 앵커(좌상단)만 자연 유지.
_MERGED_MAX_ROWS = 10000


def _excel_merged_values(path: str) -> dict[str, dict[tuple[int, int], object]]:
    """시트별 병합 셀의 '세로 라벨 전파' 맵 (best-effort).

    read_only 모드는 병합정보를 노출하지 않으므로 비-read_only로 한 번 더 열어
    각 병합 범위의 앵커(좌상단) 값을 범위가 걸친 모든 행의 '맨 왼쪽 열'에 복제한다.
    세로로 병합된 분류/카테고리 라벨이 각 행에 함께 남아 표 의미가 끊기지 않는다.
    가로 병합은 앵커 한 칸만 남겨 중복 노이즈를 피한다(원동작 유지).

    반환: {sheet_title: {(row, min_col): anchor_value}}. 라이브러리/파싱 실패는
    조용히 빈 맵 — 전파 없이도 본문 추출은 정상 동작.
    """
    out: dict[str, dict[tuple[int, int], object]] = {}
    try:
        from openpyxl import load_workbook  # type: ignore

        wb = load_workbook(path, data_only=True)  # 병합정보 필요 → read_only 불가
    except Exception:  # noqa: BLE001
        return out
    try:
        for ws in wb.worksheets:
            ranges = getattr(ws, "merged_cells", None)
            if not ranges:
                continue
            m: dict[tuple[int, int], object] = {}
            for rng in list(getattr(ranges, "ranges", [])):
                if rng.max_row - rng.min_row + 1 > _MERGED_MAX_ROWS:
                    continue
                anchor = ws.cell(row=rng.min_row, column=rng.min_col).value
                if anchor is None or not str(anchor).strip():
                    continue
                for r in range(rng.min_row, rng.max_row + 1):
                    m[(r, rng.min_col)] = anchor
            if m:
                out[ws.title] = m
    except Exception:  # noqa: BLE001
        return out
    finally:
        wb.close()
    return out


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
    tables: list[ExtractedTable] = []
    warnings: list[str] = []
    for i, slide in enumerate(prs.slides):
        parts.append(f"[slide {i + 1}]")
        for shape_idx, shape in enumerate(slide.shapes, start=1):
            if getattr(shape, "has_text_frame", False):
                for para in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in para.runs) or para.text
                    if t.strip():
                        parts.append(t)
            if getattr(shape, "has_table", False):
                table_rows: list[list[str]] = []
                for row in shape.table.rows:
                    cells = _trim_trailing_empty([_cell_text(c.text) for c in row.cells])
                    if _row_has_text(cells):
                        table_rows.append(cells)
                        parts.append(_table_text_line(cells))
                if table_rows:
                    tables.append(
                        ExtractedTable(
                            source="pptx",
                            name=f"slide {i + 1} table {shape_idx}",
                            rows=table_rows,
                            page=i + 1,
                            table_index=shape_idx,
                        )
                    )
            if getattr(shape, "has_chart", False):
                _warn_once(warnings, "pptx_charts_not_extracted")
            shape_type = str(getattr(shape, "shape_type", "")).upper()
            if "PICTURE" in shape_type:
                _warn_once(warnings, "pptx_media_not_ocrd")
            if "OLE" in shape_type or "EMBEDDED" in shape_type:
                _warn_once(warnings, "pptx_embedded_objects_not_extracted")
        if getattr(slide, "has_notes_slide", False):
            try:
                note_text = slide.notes_slide.notes_text_frame.text
                if note_text and note_text.strip():
                    parts.append(f"[slide {i + 1} notes]")
                    parts.append(note_text.strip())
                    _warn_once(warnings, "pptx_notes_extracted")
            except Exception:  # noqa: BLE001
                pass
    parts.extend(_ocr_package_images(p, prefix="ppt/media/", label="pptx", warnings=warnings))
    return ExtractResult(
        text="\n".join(parts),
        method="pptx",
        quality=0.95,
        tables=tables,
        warnings=warnings,
    )


def _pdf_tables_via_pdfplumber(p: Path) -> tuple[list[ExtractedTable], list[str]]:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return [], ["pdf_table_extractor_unavailable"]
    except Exception as exc:  # noqa: BLE001
        return [], [f"pdf_table_extractor_error:{type(exc).__name__}"]

    tables: list[ExtractedTable] = []
    try:
        with pdfplumber.open(str(p)) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                for raw in page.extract_tables() or []:
                    rows: list[list[str]] = []
                    for row in raw or []:
                        cells = _trim_trailing_empty([_cell_text(c) for c in (row or [])])
                        if _row_has_text(cells):
                            rows.append(cells)
                    if rows:
                        tables.append(
                            ExtractedTable(
                                source="pdf",
                                name=f"page {page_idx} table {len(tables) + 1}",
                                rows=rows,
                                page=page_idx,
                                table_index=len(tables) + 1,
                            )
                        )
    except Exception as exc:  # noqa: BLE001
        return [], [f"pdf_table_extractor_error:{type(exc).__name__}"]

    if tables:
        return tables, ["pdf_tables_structured"]
    return [], ["pdf_tables_not_detected"]


def _pdf_table_coverage(table_warnings: list[str]) -> str | None:
    if any(w.startswith("pdf_table_extractor_") for w in table_warnings):
        return "incomplete"
    return None


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
            tables, table_warnings = _pdf_tables_via_pdfplumber(p)
            text = _append_if_missing(text, _tables_to_text(tables))
            return ExtractResult(
                text=text,
                method="parser",
                quality=0.92,
                pages=pages,
                table_coverage=_pdf_table_coverage(table_warnings),
                tables=tables,
                warnings=table_warnings,
            )
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
            tables, table_warnings = _pdf_tables_via_pdfplumber(p)
            text = _append_if_missing(text, _tables_to_text(tables))
            return ExtractResult(
                text=text,
                method="parser",
                quality=0.95,
                pages=n_pages,
                table_coverage=_pdf_table_coverage(table_warnings),
                tables=tables,
                warnings=table_warnings,
            )
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
            warnings = ["pdf_ocr_text_may_be_noisy"]
            if truncated:
                warnings.append("pdf_ocr_truncated")
            return ExtractResult(
                text=text, method="ocr", quality=0.75,
                ocr_used=True, pages=len(images), error=note, warnings=warnings,
            )
    except Exception:  # noqa: BLE001
        pass
    return ExtractResult(
        text="", method="ocr", quality=0.0, ocr_used=False,
        pages=n_pages,
        error="OCR failed (no text layer, pdf2image/tesseract unavailable)",
        warnings=["pdf_ocr_unavailable"],
    )


def _extract_image_ocr(p: Path) -> ExtractResult:
    """이미지 파일(jpg/png/tiff 등) 직접 OCR."""
    try:
        from PIL import Image  # type: ignore

        img = Image.open(str(p))
        text = _tess_image(img)
        if text.strip():
            return ExtractResult(
                text=text,
                method="ocr",
                quality=0.75,
                ocr_used=True,
                warnings=["image_ocr_text_may_be_noisy"],
            )
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
