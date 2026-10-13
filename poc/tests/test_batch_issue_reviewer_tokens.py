"""검수자 토큰 일괄발급 래퍼 — 이름 목록 파싱만 검증한다.

실제 서명·파일 쓰기는 setup_console_test_login.py 를 그대로 다시 부르는 것이고
그 쪽은 tests/test_console_token_issuance.py 가 이미 덮는다. 이 파일은 이 래퍼가
새로 추가한 부분(이름 목록을 모으고 중복을 떼는 것)만 본다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "batch_issue_reviewer_tokens.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("_batch_issue_reviewer_tokens", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_read_names_splits_comma_list(script):
    assert script.read_names("김변호사, 이교수 ,박포렌식", "") == ["김변호사", "이교수", "박포렌식"]


def test_read_names_reads_file(script, tmp_path):
    f = tmp_path / "experts.txt"
    f.write_text("김변호사\n이교수\n\n박포렌식\n", encoding="utf-8")
    assert script.read_names("", str(f)) == ["김변호사", "이교수", "박포렌식"]


def test_read_names_merges_and_dedupes_preserving_order(script, tmp_path):
    f = tmp_path / "experts.txt"
    f.write_text("이교수\n박포렌식\n", encoding="utf-8")
    assert script.read_names("김변호사,이교수", str(f)) == ["김변호사", "이교수", "박포렌식"]


def test_read_names_empty_input_gives_empty_list(script):
    assert script.read_names("", "") == []
