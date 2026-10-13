"""Current-review-batch marker tool regression tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _metadata(path: Path, doc_id: str, batch: str, active: bool = False) -> Path:
    target = path / f"{doc_id}.metadata.json"
    target.write_text(
        json.dumps(
            {"doc_id": doc_id, "review_batch": batch, "review_batch_active": active},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return target


def test_sets_exactly_one_review_batch_active_without_removing_candidates(tmp_path: Path):
    first = _metadata(tmp_path, "A-1", "old", True)
    second = _metadata(tmp_path, "B-1", "current", False)
    script = Path(__file__).resolve().parents[1] / "scripts" / "set_active_review_batch.py"

    done = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--batch", "current", "--expected-count", "1"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )

    assert "완료" in done.stdout
    assert first.exists() and second.exists()
    assert json.loads(first.read_text(encoding="utf-8"))["review_batch_active"] is False
    assert json.loads(second.read_text(encoding="utf-8"))["review_batch_active"] is True


def test_refuses_unexpected_batch_size_before_writing(tmp_path: Path):
    target = _metadata(tmp_path, "A-1", "current", False)
    script = Path(__file__).resolve().parents[1] / "scripts" / "set_active_review_batch.py"

    done = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--batch", "current", "--expected-count", "2"],
        capture_output=True, text=True, encoding="utf-8",
    )

    assert done.returncode == 2
    assert json.loads(target.read_text(encoding="utf-8"))["review_batch_active"] is False
