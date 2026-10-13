from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import build_p1_retrain_dataset, make_llm_judge_gold, train_eval_synth_silver


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_train_eval_synth_uses_silver_train_and_excludes_locked(tmp_path):
    run = tmp_path / "run"
    work = tmp_path / "work"
    gold = tmp_path / "classification_gold.jsonl"
    _write_jsonl(
        run / "silver_train.jsonl",
        [
            {"doc_id": "locked-1", "text": "locked text", "label": "TS"},
            {"doc_id": "silver-1", "text": "silver text", "label": "S2"},
        ],
    )
    _write_jsonl(
        gold,
        [
            {
                "doc_id": "locked-1",
                "text": "locked text",
                "label": "TS",
                "label_source": "human_review",
                "reviewer_id": "admin",
            }
        ],
    )

    stats = train_eval_synth_silver.convert_and_split(run, work, gold_path=gold)
    rows = _read_jsonl(work / "train.jsonl") + _read_jsonl(work / "val.jsonl")
    assert stats["train_n"] + stats["val_n"] == 1
    assert rows == [{"text": "silver text", "label": "S2"}]


def test_train_eval_synth_requires_split_file_by_default(tmp_path):
    with pytest.raises(FileNotFoundError):
        train_eval_synth_silver.convert_and_split(tmp_path / "run", tmp_path / "work")


def test_build_p1_retrain_dataset_excludes_locked_gold(tmp_path):
    gold = tmp_path / "gold.jsonl"
    _write_jsonl(
        gold,
        [
            {"text": "locked", "label": "TS", "label_source": "human_review", "reviewer_id": "admin"},
            {"text": "candidate", "label": "S1", "label_source": "rule_llm_agreement"},
        ],
    )
    rows = build_p1_retrain_dataset._gold_rows(gold, repeat=1)
    assert rows == [{"text": "candidate", "label": "S1", "source": "rule_llm_agreement", "doc_id": None}]


def test_make_llm_judge_gold_refuses_canonical_output(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "make_llm_judge_gold.py",
            "--out",
            "datasets/gold_real/classification_gold.jsonl",
            "--dry-run",
        ],
    )
    assert make_llm_judge_gold.main() == 2
