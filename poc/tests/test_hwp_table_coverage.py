"""HWP/HWPX 표 셀 미추출(조용한 표 흘림) → 검수 라우팅 안전장치.

배경: rhwp가 본문 단락은 뽑지만 표·글상자 셀을 거의 전부 흘린다(실측 noori.hwp 29/31 누락).
영업비밀 문서는 등급·원가·임원보상이 표에 들어가는 일이 많아, 본문만 분류기에 들어가면
표 속 비밀이 '조용히' 미탐(FNR)된다. 메서드 고정 quality(0.95)로는 안 잡히는 사각이라
별도 table_coverage 신호로 검수 라우팅한다(등급 불변 — FNR-safe).

이 파일은 감지 헬퍼(원문 대조)와 검수 판정 배선을 결정적으로 검증한다. 실물 .hwp 픽스처가
없어 rhwp 실동작 대신, 감지 로직(원문 XML 직접 읽기)과 review 배선을 단위/통합 검증한다.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from lloydk.modules.m2_preprocess.extractor import (
    ExtractResult,
    _hwp_via_pyhwp,
    _hwp_table_coverage,
    _hwpx_bytes_has_table,
    _hwpx_tables,
    _hwpx_uncaptured_table_cells,
)
from lloydk.services.document_ingestion_service import extraction_review_decision


def _make_hwpx(section_xml: str) -> bytes:
    """최소 HWPX(zip) 바이트 — section0.xml만 채운다(감지는 원문 XML 직접 읽기)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", section_xml)
    return buf.getvalue()


# 본문 단락 + 표(셀에 고유 비밀 문자열). 실 Hancom OWPML의 hp: 스키마 형태.
_SECTION_WITH_TABLE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hs:sec xmlns:hs="urn:sec" xmlns:hp="urn:para">'
    "<hp:p><hp:run><hp:t>BODY_VISIBLE_본문단락</hp:t></hp:run></hp:p>"
    "<hp:tbl><hp:tr><hp:tc><hp:subList>"
    "<hp:p><hp:run><hp:t>SECRET_CELL_원가구조_99</hp:t></hp:run></hp:p>"
    "</hp:subList></hp:tc></hp:tr></hp:tbl>"
    "</hs:sec>"
)
_SECTION_NO_TABLE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hs:sec xmlns:hp="urn:para">'
    "<hp:p><hp:run><hp:t>BODY_VISIBLE_본문단락</hp:t></hp:run></hp:p>"
    "</hs:sec>"
)


class TestUncapturedTableCells:
    """.hwpx 원문 표 셀 vs 추출본 대조(정밀 감지)."""

    def test_cell_missing_from_text_flags_true(self):
        data = _make_hwpx(_SECTION_WITH_TABLE)
        # 추출본은 본문만(표 셀 SECRET_CELL_원가구조_99 빠짐) — 조용한 표 미추출 상황
        assert _hwpx_uncaptured_table_cells(data, "BODY_VISIBLE_본문단락") is True

    def test_hwpx_table_rows_are_structured(self):
        data = _make_hwpx(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="urn:sec" xmlns:hp="urn:para">'
            "<hp:tbl><hp:tr>"
            "<hp:tc><hp:p><hp:run><hp:t>item</hp:t></hp:run></hp:p></hp:tc>"
            "<hp:tc><hp:p><hp:run><hp:t>amount</hp:t></hp:run></hp:p></hp:tc>"
            "</hp:tr><hp:tr>"
            "<hp:tc><hp:p><hp:run><hp:t>secret</hp:t></hp:run></hp:p></hp:tc>"
            "<hp:tc><hp:p><hp:run><hp:t>12000</hp:t></hp:run></hp:p></hp:tc>"
            "</hp:tr></hp:tbl></hs:sec>"
        )
        tables = _hwpx_tables(data)
        assert tables
        assert tables[0].rows == [["item", "amount"], ["secret", "12000"]]

    def test_cell_present_in_text_no_flag(self):
        data = _make_hwpx(_SECTION_WITH_TABLE)
        # 셀 텍스트가 추출본에 이미 있으면(회수됨) flag 안 함 — 오탐 방지
        text = "BODY_VISIBLE_본문단락\nSECRET_CELL_원가구조_99"
        assert _hwpx_uncaptured_table_cells(data, text) is False

    def test_cell_present_ignoring_whitespace(self):
        """공백 차이는 무시 — 셀이 공백만 다르게 회수돼도 오탐 안 함."""
        data = _make_hwpx(_SECTION_WITH_TABLE)
        text = "BODY_VISIBLE_본문단락 SECRET_CELL_ 원가구조_99"  # 공백 삽입
        assert _hwpx_uncaptured_table_cells(data, text) is False

    def test_no_table_no_flag(self):
        data = _make_hwpx(_SECTION_NO_TABLE)
        assert _hwpx_uncaptured_table_cells(data, "BODY_VISIBLE_본문단락") is False

    def test_garbage_bytes_graceful_false(self):
        assert _hwpx_uncaptured_table_cells(b"not a zip", "anything") is False

    def test_body_text_outside_table_not_treated_as_cell(self):
        """표 밖 <hp:t>(본문)은 셀로 오인하지 않는다 — 본문이 빠져도 여기선 flag 안 함."""
        data = _make_hwpx(_SECTION_NO_TABLE)
        # 본문 텍스트를 일부러 빈 추출본과 대조해도, 표가 없으니 셀 후보가 없어 False
        assert _hwpx_uncaptured_table_cells(data, "") is False


class TestHwpxHasTable:
    def test_has_table_true(self):
        assert _hwpx_bytes_has_table(_make_hwpx(_SECTION_WITH_TABLE)) is True

    def test_has_table_false(self):
        assert _hwpx_bytes_has_table(_make_hwpx(_SECTION_NO_TABLE)) is False

    def test_garbage_bytes_false(self):
        assert _hwpx_bytes_has_table(b"xxx") is False


class TestHwpTableCoverageRouting:
    """_hwp_table_coverage: .hwpx는 원문 대조, .hwp는 표 구조 존재(rhwp 변환)로 판정."""

    def test_hwpx_incomplete_when_cell_dropped(self, tmp_path: Path):
        p = tmp_path / "secret.hwpx"
        p.write_bytes(_make_hwpx(_SECTION_WITH_TABLE))
        # doc은 .hwpx 경로에서 안 쓰이므로 None 전달, 추출본은 본문만
        assert _hwp_table_coverage(p, None, "BODY_VISIBLE_본문단락") == "incomplete"

    def test_hwpx_ok_when_cell_captured(self, tmp_path: Path):
        p = tmp_path / "recovered.hwpx"
        p.write_bytes(_make_hwpx(_SECTION_WITH_TABLE))
        text = "BODY_VISIBLE_본문단락 SECRET_CELL_원가구조_99"
        assert _hwp_table_coverage(p, None, text) is None

    def test_hwpx_none_when_no_table(self, tmp_path: Path):
        p = tmp_path / "plain.hwpx"
        p.write_bytes(_make_hwpx(_SECTION_NO_TABLE))
        assert _hwp_table_coverage(p, None, "BODY_VISIBLE_본문단락") is None

    def test_hwp_binary_incomplete_when_export_has_table(self, tmp_path: Path):
        """.hwp: 셀 텍스트는 변환에도 빠지므로 표 '구조' 존재만으로 incomplete(보수적)."""
        p = tmp_path / "secret.hwp"
        p.write_bytes(b"\xd0\xcf\x11\xe0dummy-ole")  # 내용 무관 — doc.to_hwpx_bytes 사용

        class _FakeDoc:
            def to_hwpx_bytes(self):
                return _make_hwpx(_SECTION_WITH_TABLE)

        assert _hwp_table_coverage(p, _FakeDoc(), "본문만") == "incomplete"

    def test_hwp_binary_none_when_no_table(self, tmp_path: Path):
        p = tmp_path / "plain.hwp"
        p.write_bytes(b"\xd0\xcf\x11\xe0dummy-ole")

        class _FakeDoc:
            def to_hwpx_bytes(self):
                return _make_hwpx(_SECTION_NO_TABLE)

        assert _hwp_table_coverage(p, _FakeDoc(), "본문") is None

    def test_hwp_binary_graceful_when_export_unavailable(self, tmp_path: Path):
        """to_hwpx_bytes가 없거나 예외면 None(과라우팅 회피)."""
        p = tmp_path / "x.hwp"
        p.write_bytes(b"\xd0\xcf\x11\xe0")

        class _BrokenDoc:
            def to_hwpx_bytes(self):
                raise RuntimeError("no export")

        assert _hwp_table_coverage(p, _BrokenDoc(), "본문") is None


class TestExtractionReviewWiring:
    """table_coverage="incomplete" → requires_review + reason 'table_incomplete'."""

    def _base(self, **kw):
        args = dict(
            quality=0.95, ocr_used=False, error=None,
            min_quality=0.6, ocr_requires_review=True, content_quality=1.0,
        )
        args.update(kw)
        return extraction_review_decision(**args)

    def test_incomplete_routes_to_review(self):
        d = self._base(table_coverage="incomplete")
        assert d.requires_review is True
        assert "table_incomplete" in d.reasons

    def test_clean_extraction_not_routed(self):
        d = self._base(table_coverage=None)
        assert d.requires_review is False
        assert "table_incomplete" not in d.reasons

    def test_grade_unchanged_signal_is_review_only(self):
        """표 미추출은 등급을 바꾸지 않고 검수로만 보낸다(FNR-safe) — reason만 추가."""
        d = self._base(table_coverage="incomplete", quality=0.95)
        # 표 신호 외 다른 저하 사유가 없으면 reason은 table_incomplete 단독
        assert d.reasons == ["table_incomplete"]

    def test_combines_with_other_reasons(self):
        d = self._base(table_coverage="incomplete", ocr_used=True, error="boom")
        assert set(d.reasons) >= {"extract_error", "ocr", "table_incomplete"}


class TestExtractResultField:
    def test_default_table_coverage_none(self):
        r = ExtractResult(text="x", method="plain", quality=1.0)
        assert r.table_coverage is None


class TestPyhwpTableParsing:
    def test_hwp5html_xhtml_tables_are_structured(self, tmp_path: Path, monkeypatch):
        import subprocess
        from types import SimpleNamespace

        import lloydk.modules.m2_preprocess.extractor as ex

        p = tmp_path / "sample.hwp"
        p.write_bytes(b"\xd0\xcf\x11\xe0")

        def fake_run(args, **kwargs):
            out_dir = Path(args[args.index("--output") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "index.xhtml").write_text(
                "<html><body><p>body</p><table>"
                "<tr><td>item</td><td>amount</td></tr>"
                "<tr><td>secret</td><td>12000</td></tr>"
                "</table></body></html>",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(ex, "_hwp5html_cmd", lambda: "hwp5html")
        monkeypatch.setattr(subprocess, "run", fake_run)

        text, tables = _hwp_via_pyhwp(p)
        assert "secret | 12000" in text
        assert tables
        assert tables[0].rows == [["item", "amount"], ["secret", "12000"]]
