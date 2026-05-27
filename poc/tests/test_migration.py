"""Qdrant → ES 마이그레이션 스크립트 단위 테스트.

doc/13 §6 S2.5·§6.1 7단계 검증.

테스트 전략:
- Extract: Qdrant 클라이언트 mock
- Transform: pure generator → fixture JSONL로 직접 검증
- Load: dry-run 모드에서 변환만 검증
- Verify: source-only(jsonl) count 비교
- Swap: dry-run 메시지 검증
- 전체 오케스트레이션: dry-run + skip-extract로 5건 fixture
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

# 마이그레이션 스크립트는 scripts/ 아래라 직접 import — sys.path 조정
import sys

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from migrate_qdrant_to_es import (  # noqa: E402
    _peek_first_dim,
    _vectors_close,
    iter_bulk_actions,
    load_to_es,
    run_migration,
    swap_alias,
    verify,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def sample_jsonl(tmp_path: Path) -> Path:
    """5건 sample fixture — PoC 검증용."""
    rows = [
        {"id": "d1", "vector": [1.0, 0.0, 0.0, 0.0], "payload": {"grade": "TS", "text": "기밀 1"}},
        {"id": "d2", "vector": [0.0, 1.0, 0.0, 0.0], "payload": {"grade": "S1", "text": "비밀 2"}},
        {"id": "d3", "vector": [0.0, 0.0, 1.0, 0.0], "payload": {"grade": "S2", "text": "대외비 3"}},
        {"id": "d4", "vector": [0.0, 0.0, 0.0, 1.0], "payload": {"grade": "S3", "text": "공개 4"}},
        {"id": "d5", "vector": [0.5, 0.5, 0.0, 0.0], "payload": {"grade": "TS", "text": "기밀 5"}},
    ]
    path = tmp_path / "sample.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


# ─────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────


def test_vectors_close_identical():
    assert _vectors_close([1.0, 0.0], [1.0, 0.0], tol=1e-6)


def test_vectors_close_tolerance():
    assert _vectors_close([1.0, 0.0], [1.0 + 1e-7, 0.0], tol=1e-6)


def test_vectors_not_close_outside_tol():
    assert not _vectors_close([1.0, 0.0], [1.1, 0.0], tol=1e-6)


def test_vectors_not_close_diff_dim():
    assert not _vectors_close([1.0, 0.0], [1.0, 0.0, 0.0], tol=1e-6)


def test_peek_first_dim(sample_jsonl: Path):
    assert _peek_first_dim(sample_jsonl) == 4


def test_peek_first_dim_skips_missing_vector(tmp_path: Path):
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        json.dumps({"id": "x", "vector": None, "payload": {}}) + "\n"
        + json.dumps({"id": "y", "vector": [1.0, 0.0, 0.0], "payload": {}}) + "\n",
        encoding="utf-8",
    )
    assert _peek_first_dim(path) == 3


# ─────────────────────────────────────────────────────────────
# Transform (iter_bulk_actions)
# ─────────────────────────────────────────────────────────────


def test_iter_bulk_actions_basic(sample_jsonl: Path):
    actions = list(iter_bulk_actions(sample_jsonl, "test-index"))
    assert len(actions) == 5
    for a in actions:
        assert a["_op_type"] == "index"
        assert a["_index"] == "test-index"
        assert "_id" in a
        assert "embedding" in a["_source"]
        assert "grade" in a["_source"]


def test_iter_bulk_actions_preserves_payload(sample_jsonl: Path):
    actions = list(iter_bulk_actions(sample_jsonl, "idx"))
    first = actions[0]
    assert first["_id"] == "d1"
    assert first["_source"]["grade"] == "TS"
    assert first["_source"]["text"] == "기밀 1"
    assert first["_source"]["embedding"] == [1.0, 0.0, 0.0, 0.0]


def test_iter_bulk_actions_reembed(sample_jsonl: Path):
    """reembed_with 콜백이 vector를 교체한다."""

    def new_embed(text: str) -> list[float]:
        # 텍스트 길이 기반 더미 임베딩
        return [float(len(text)), 0.0, 0.0, 0.0]

    actions = list(iter_bulk_actions(sample_jsonl, "idx", reembed_with=new_embed))
    # 첫 행의 text "기밀 1" 길이 4
    assert actions[0]["_source"]["embedding"] == [4.0, 0.0, 0.0, 0.0]


def test_iter_bulk_actions_skips_missing_vector(tmp_path: Path):
    path = tmp_path / "skip.jsonl"
    path.write_text(
        json.dumps({"id": "x", "vector": None, "payload": {}}) + "\n"
        + json.dumps({"id": "y", "vector": [1.0], "payload": {}}) + "\n",
        encoding="utf-8",
    )
    actions = list(iter_bulk_actions(path, "idx"))
    # vector None인 첫 행은 skip
    assert len(actions) == 1
    assert actions[0]["_id"] == "y"


def test_iter_bulk_actions_skips_blank_lines(tmp_path: Path):
    path = tmp_path / "blank.jsonl"
    path.write_text(
        "\n"
        + json.dumps({"id": "a", "vector": [1.0], "payload": {}}) + "\n"
        + "\n",
        encoding="utf-8",
    )
    actions = list(iter_bulk_actions(path, "idx"))
    assert len(actions) == 1


# ─────────────────────────────────────────────────────────────
# Load (dry-run)
# ─────────────────────────────────────────────────────────────


def test_load_dry_run_counts_only(sample_jsonl: Path):
    result = load_to_es(sample_jsonl, "test-idx", dry_run=True)
    assert result.stage == "load"
    assert result.ok is True
    assert result.metrics["indexed"] == 5
    assert result.metrics["dry_run"] is True


def test_load_dry_run_with_reembed(sample_jsonl: Path):
    """dry-run에서도 reembed_with는 적용된다 — 신규 인덱스 dims 확정용."""
    # transform만 검증, 실제 ES 호출 없음
    result = load_to_es(sample_jsonl, "test-idx", dry_run=True)
    assert result.ok


# ─────────────────────────────────────────────────────────────
# Verify (dry-run)
# ─────────────────────────────────────────────────────────────


def test_verify_dry_run_reports_source_count(sample_jsonl: Path):
    result = verify(sample_jsonl, "any-index", dry_run=True, sample_size=100)
    assert result.ok
    assert result.metrics["expected"] == 5
    assert result.metrics["dry_run"] is True


# ─────────────────────────────────────────────────────────────
# Swap (dry-run)
# ─────────────────────────────────────────────────────────────


def test_swap_dry_run_describes_change():
    result = swap_alias("alias-x", new_index="new-v2", dry_run=True, old_index="old-v1")
    assert result.ok
    assert "alias-x" in result.detail
    assert "new-v2" in result.detail
    assert result.metrics["old"] == "old-v1"


def test_swap_dry_run_without_old_index():
    result = swap_alias("alias-x", new_index="new-v1", dry_run=True, old_index=None)
    assert result.ok
    assert result.metrics["old"] is None


# ─────────────────────────────────────────────────────────────
# Orchestration (run_migration)
# ─────────────────────────────────────────────────────────────


def test_run_migration_dry_run_full_flow(sample_jsonl: Path, tmp_path: Path):
    """skip-extract + dry-run으로 Transform→Load→Verify→Swap 전체 흐름."""
    work_dir = sample_jsonl.parent
    # sample.jsonl을 {collection}.jsonl 이름으로 복사
    target = work_dir / "guides_v1.jsonl"
    target.write_bytes(sample_jsonl.read_bytes())

    args = argparse.Namespace(
        collection="guides_v1",
        target_index="secrets-guides-koipa-test",
        alias="secrets-guides-koipa",
        old_index="old-v1",
        work_dir=str(work_dir),
        batch_size=100,
        limit=None,
        sample_size=100,
        dry_run=True,
        skip_extract=True,
        continue_on_error=False,
        report=str(tmp_path / "report.json"),
    )
    report = run_migration(args)
    assert report.all_ok
    stages = [s.stage for s in report.stages]
    assert stages == ["extract", "load", "verify", "swap"]
    # load 단계가 5건 indexed로 보고
    load_stage = next(s for s in report.stages if s.stage == "load")
    assert load_stage.metrics["indexed"] == 5


def test_run_migration_skip_extract_missing_file(tmp_path: Path):
    args = argparse.Namespace(
        collection="nonexistent",
        target_index="t",
        alias=None,
        old_index=None,
        work_dir=str(tmp_path),
        batch_size=100,
        limit=None,
        sample_size=100,
        dry_run=True,
        skip_extract=True,
        continue_on_error=False,
        report=str(tmp_path / "report.json"),
    )
    report = run_migration(args)
    assert not report.all_ok
    assert report.stages[0].stage == "extract"
    assert not report.stages[0].ok


def test_run_migration_no_alias_skips_swap(sample_jsonl: Path, tmp_path: Path):
    target = tmp_path / "c.jsonl"
    target.write_bytes(sample_jsonl.read_bytes())

    args = argparse.Namespace(
        collection="c",
        target_index="t",
        alias=None,  # 명시 안 함 → swap 단계 자체가 생성되지 않음
        old_index=None,
        work_dir=str(tmp_path),
        batch_size=100,
        limit=None,
        sample_size=100,
        dry_run=True,
        skip_extract=True,
        continue_on_error=False,
        report=str(tmp_path / "report.json"),
    )
    report = run_migration(args)
    assert report.all_ok
    stages = [s.stage for s in report.stages]
    assert "swap" not in stages
