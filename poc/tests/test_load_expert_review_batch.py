"""검수 배치 적재 스크립트가 지재원행 번들과 같은 문서 집합을 적재하는가.

배경(2026-09-25): 번들 빌더는 품질 결함 20건을 빼고 싣는데 이 적재 스크립트만 1,731건을 전부 적재해, 두 경로로 채운
콘솔이 서로 달랐다. 같은 제외 목록(evidence/review_request_exclusions.jsonl)을 적용하도록 고쳤고, 이 시험이 잠근다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import load_expert_review_1731_to_console as loader  # noqa: E402


@pytest.fixture()
def world(tmp_path, monkeypatch):
    src, root = tmp_path / "src", tmp_path / "pool"
    src.mkdir()
    ids = ["MD-0001", "MD-0002", "MD-0003"]
    (src / "documents_all.jsonl").write_text(
        "\n".join(json.dumps({"review_id": i, "text": f"본문 {i}"}, ensure_ascii=False) for i in ids), encoding="utf-8")
    (src / "internal_manifest.jsonl").write_text(
        "\n".join(json.dumps({"review_id": i, "grade": "S1", "round": "R1"}) for i in ids), encoding="utf-8")
    monkeypatch.setattr(loader, "SRC", src)
    monkeypatch.setattr(loader, "ROOT", root)
    monkeypatch.setattr(loader, "load_exclusions", lambda: {"MD-0003"})
    return root


def test_the_excluded_document_is_not_loaded(world, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["load"])

    assert loader.main() == 0

    names = sorted(p.name for p in world.iterdir())
    assert names == ["MD-0001.metadata.json", "MD-0001_review.md", "MD-0002.metadata.json", "MD-0002_review.md"]


def test_dry_run_writes_nothing(world, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["load", "--dry-run"])
    assert loader.main() == 0
    assert not world.exists()


def test_select_ids_keeps_order_and_drops_only_the_excluded():
    assert loader.select_ids({"A": 1, "B": 2, "C": 3}, {"B"}) == ["A", "C"]
    assert loader.select_ids({"A": 1}, set()) == ["A"]
