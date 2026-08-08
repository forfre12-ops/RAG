"""Fail-closed input tests for the proxy-gold assembly command."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from scripts import assemble_proxy_gold as cli
from lloydk import proxy_corpus as core
from lloydk.proxy_corpus import ProxyRecordCheck


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_exact_scenario_quota_blocks_profile_loss_even_when_grade_total_is_enough(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        core,
        "validate_proxy_record",
        lambda record, *, stage, intended_use: ProxyRecordCheck(
            doc_id=str(record.get("doc_id") or ""), errors=()
        ),
    )

    def row(doc_id: str, scenario: str, profile: str) -> dict:
        return {
            "doc_id": doc_id,
            "text": f"unique {doc_id}",
            "label": "S2",
            "document_origin": "synthetic",
            "document_family_id": f"family-{doc_id}",
            "scenario_id": scenario,
            "factor_profile_id": profile,
        }

    contract = {
        "targets": {"S2": 2},
        "expected_origins": {"S2": "synthetic"},
        "scenario_targets": {"scenario-a": 1, "scenario-b": 1},
        "scenario_target_grades": {"scenario-a": "S2", "scenario-b": "S2"},
        "scenario_factor_profiles": {
            "scenario-a": "s2-s1-v1-m1",
            "scenario-b": "s2-s1-v1-m2",
        },
        "min_families": {"S2": 1},
        "max_family_share": 1.0,
        "require_shortcut_gate": False,
    }
    complete = core.assemble_proxy_gold(
        [
            row("a", "scenario-a", "s2-s1-v1-m1"),
            row("b", "scenario-b", "s2-s1-v1-m2"),
        ],
        **contract,
    )
    assert complete.ready is True
    assert complete.stats["selected_by_factor_profile"] == {
        "s2-s1-v1-m1": 1,
        "s2-s1-v1-m2": 1,
    }

    missing = core.assemble_proxy_gold(
        [
            row("a1", "scenario-a", "s2-s1-v1-m1"),
            row("a2", "scenario-a", "s2-s1-v1-m1"),
        ],
        **contract,
    )
    assert missing.ready is False
    assert missing.stats["available_by_grade"] == {"S2": 2}
    assert missing.stats["missing_by_scenario"] == {"scenario-b": 1}
    assert missing.stats["missing_by_factor_profile"] == {"s2-s1-v1-m2": 1}


def test_load_fails_when_a_declared_path_is_missing(tmp_path: Path):
    with pytest.raises(cli.CorpusLoadError, match="does not exist"):
        cli._load_corpus([tmp_path / "missing.jsonl"], purpose="blocked corpus")


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("broken.jsonl", '{"text":"ok"}\n{"broken":'),
        ("broken.json", '[{"text":"ok"},'),
        ("constant.json", '[{"text": NaN}]'),
    ],
)
def test_load_fails_on_malformed_json_or_jsonl(tmp_path: Path, name: str, payload: str):
    path = tmp_path / name
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(cli.CorpusLoadError, match="malformed JSON"):
        cli._load_corpus([path], purpose="blocked corpus")


@pytest.mark.parametrize(
    ("name", "payload"), [("empty.jsonl", "\n"), ("empty.json", "[]")]
)
def test_load_fails_on_empty_file_or_empty_json_array(
    tmp_path: Path, name: str, payload: str
):
    path = tmp_path / name
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(cli.CorpusLoadError, match="empty"):
        cli._load_corpus([path], purpose="blocked corpus")


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"document_family_id": "family-a"}, "missing required text"),
        ({"document_family_id": "family-a", "text": "   "}, "missing required text"),
        ({"text": "usable text"}, "missing required document_family_id"),
        (
            {"document_family_id": " ", "text": "usable text"},
            "missing required document_family_id",
        ),
    ],
)
def test_load_fails_when_required_leakage_keys_are_missing(
    tmp_path: Path, row: dict, message: str
):
    path = tmp_path / "corpus.jsonl"
    _write_jsonl(path, [row])

    with pytest.raises(cli.CorpusLoadError, match=message):
        cli._load_corpus([path], purpose="blocked corpus")


def test_directory_with_no_corpus_files_fails(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("not a corpus", encoding="utf-8")

    with pytest.raises(cli.CorpusLoadError, match="has no .json/.jsonl files"):
        cli._load_corpus([tmp_path], purpose="blocked corpus")


def test_atomic_writer_preserves_exact_bytes_and_refuses_overwrite(tmp_path: Path):
    target = tmp_path / "artifact.jsonl"
    payload = "첫째 줄\n둘째 줄\n".encode("utf-8")
    cli._atomic_write_new(target, payload)
    assert target.read_bytes() == payload
    assert (
        hashlib.sha256(target.read_bytes()).hexdigest()
        == hashlib.sha256(payload).hexdigest()
    )
    with pytest.raises(cli.CorpusLoadError, match="refusing to overwrite"):
        cli._atomic_write_new(target, b"replacement")


def test_report_records_blocked_file_row_and_unique_counts(tmp_path: Path):
    candidate = tmp_path / "candidate.jsonl"
    blocked = tmp_path / "blocked.json"
    report = tmp_path / "assembly.json"
    output = tmp_path / "assembled.jsonl"
    _write_jsonl(
        candidate,
        [{"document_family_id": "candidate-family", "text": "candidate text"}],
    )
    blocked.write_text(
        json.dumps(
            [
                {"document_family_id": "family-a", "text": "first text"},
                {"document_family_id": "family-a", "text": "second text"},
                {"document_family_id": "family-b", "text": "first text"},
            ]
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "--input",
            str(candidate),
            "--blocked-corpus",
            str(blocked),
            "--out",
            str(output),
            "--report",
            str(report),
        ]
    )

    assert exit_code == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["blocked_corpus"] == {
        "requested_paths": [str(blocked)],
        "loaded_files": [{"path": str(blocked), "rows": 3}],
        "file_count": 1,
        "row_count": 3,
        "unique_family_ids": 2,
        "unique_text_hashes": 2,
    }
    assert payload["input_corpus"]["row_count"] == 1
    architecture = payload["evaluation_architecture"]
    assert architecture["set_role"] == "primary_frozen_proxy"
    assert architecture["origin_profile"] == "public-s3-hybrid-v2"
    assert architecture["public_real_s3_included"] is True
    assert architecture["scenario_quota_contract"] is True
    assert architecture["synthetic_high_grade_scenario_quota_contract"] is True
    assert architecture["catalog"]["factor_profile_schema_id"] == (
        "svm-boundary-profile-v1"
    )
    assert payload["stats"]["expected_origin_by_grade"] == {
        "TS": "synthetic",
        "S1": "synthetic",
        "S2": "synthetic",
        "S3": "public_real",
    }
    assert not output.exists()


def test_cli_passes_blocked_doc_ids_as_a_leakage_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    candidate = tmp_path / "candidate.jsonl"
    blocked = tmp_path / "blocked.jsonl"
    report = tmp_path / "report.json"
    _write_jsonl(
        candidate,
        [
            {
                "doc_id": "candidate",
                "document_family_id": "candidate-family",
                "text": "candidate",
            }
        ],
    )
    _write_jsonl(
        blocked,
        [
            {
                "doc_id": "blocked-doc",
                "document_family_id": "blocked-family",
                "text": "blocked",
            }
        ],
    )
    captured: dict[str, object] = {}

    class Result:
        ready = False

        @staticmethod
        def to_dict() -> dict:
            return {"ready": False, "stats": {}}

    def fake_assemble(records, **kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(cli, "assemble_proxy_gold", fake_assemble)

    assert (
        cli.main(
            [
                "--input",
                str(candidate),
                "--blocked-corpus",
                str(blocked),
                "--report",
                str(report),
                "--out",
                str(tmp_path / "unused.jsonl"),
            ]
        )
        == 2
    )
    assert captured["blocked_doc_ids"] == {"blocked-doc"}
    assert captured["require_catalog_usage_contract"] is True
    assert captured["required_synthetic_gate_version"] == ("proxy_semantic_quality_v2")
