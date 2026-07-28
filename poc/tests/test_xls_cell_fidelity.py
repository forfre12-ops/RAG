"""구형 .xls(BIFF) 셀 타입 인지 변환 _xls_cell_text 의 숫자·날짜·코드 충실도 잠금.

xlrd 는 모든 수치/날짜를 float 로 돌려주므로 str() 만 하면 정수 1234→'1234.0',
엑셀 날짜 serial 45306→'45306.0'(의미 소실), 큰 금액이 지수표기로 열화될 수 있다.
_xls_cell_text 가 이를 교정한다(openpyxl 경로는 int/datetime 네이티브 보존이라 별도).
이 테스트가 없으면 xls 경로의 숫자 회귀(예: 511dbfb 류 포맷별 무음 열화)가 CI green 으로 통과.
"""

from __future__ import annotations

import re

import pytest

xlrd = pytest.importorskip("xlrd")  # 구형 .xls 는 opt-in [xls] extra

from lloydk.modules.m2_preprocess.extractor import _xls_cell_text  # noqa: E402


def _num(v):
    return _xls_cell_text(v, xlrd.XL_CELL_NUMBER, 0)


def test_integer_has_no_trailing_dot_zero():
    # 1234.0 → '1234' (xlrd float 강제변환 교정) — '1234.0' 이면 숫자 토큰 손상.
    assert _num(1234.0) == "1234"


def test_large_amount_no_scientific_notation():
    # 큰 영업비밀 금액(8.735억)이 '8.735e+08' 로 열화되면 안 됨.
    out = _num(873500000.0)
    assert out == "873500000"
    assert "e" not in out.lower()


def test_non_integer_float_preserved():
    assert _num(12.5) == "12.5"


def test_zero_and_negative():
    assert _num(0.0) == "0"
    assert _num(-42.0) == "-42"


def test_date_serial_becomes_iso_not_serial():
    # 엑셀 날짜 serial 45306 → ISO 날짜. 'serial' 그대로면 의미(날짜) 소실.
    out = _xls_cell_text(45306.0, xlrd.XL_CELL_DATE, 0)
    assert "45306" not in out
    assert re.match(r"^\d{4}-\d{2}-\d{2}", out), out


def test_text_code_leading_zeros_preserved():
    # '007' 같은 코드(계좌/부품번호)는 숫자로 캐스팅되지 않고 문자 그대로 보존.
    assert _xls_cell_text("007", xlrd.XL_CELL_TEXT, 0) == "007"


def test_empty_and_blank_cells():
    assert _xls_cell_text("", xlrd.XL_CELL_EMPTY, 0) == ""
    assert _xls_cell_text(None, xlrd.XL_CELL_BLANK, 0) == ""
