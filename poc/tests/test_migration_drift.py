"""check_migration_drift.compare_heads 단위 테스트 (lite, DB 불필요)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

# 스크립트는 패키지가 아니므로 파일 경로로 로드.
_SPEC = importlib.util.spec_from_file_location(
    "check_migration_drift",
    Path(__file__).resolve().parents[1] / "scripts" / "check_migration_drift.py",
)
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)
compare_heads = _mod.compare_heads


def test_no_drift_when_equal():
    r = compare_heads(["b1c2d3e4f5a6"], ["b1c2d3e4f5a6"])
    assert r["drift"] is False
    assert r["missing"] == [] and r["unknown"] == []


def test_missing_when_db_behind_head():
    # DB가 이전 리비전에 멈춰 있고 head가 더 위 → missing(=upgrade 필요).
    r = compare_heads(["a1f2c3d4e5f6"], ["b1c2d3e4f5a6"])
    assert r["drift"] is True
    assert r["missing"] == ["b1c2d3e4f5a6"]


def test_unknown_when_db_ahead():
    # DB에 스크립트에 없는 리비전(다운그레이드/리비전 삭제) → unknown.
    r = compare_heads(["zz_unknown"], ["b1c2d3e4f5a6"])
    assert r["drift"] is True
    assert r["unknown"] == ["zz_unknown"]


def test_empty_db_is_drift():
    r = compare_heads([], ["b1c2d3e4f5a6"])
    assert r["drift"] is True
    assert r["missing"] == ["b1c2d3e4f5a6"]
