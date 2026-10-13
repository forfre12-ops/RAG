from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_expert_review_readiness.py"
SPEC = importlib.util.spec_from_file_location("check_expert_review_readiness", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_candidate(root: Path, name: str, **fields: object) -> None:
    payload = {
        "review_batch": "expert-review-20261010",
        "review_batch_active": True,
        "intended_label": "S2",
        "doc_format": "매뉴얼",
        **fields,
    }
    (root / f"{name}.metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _write_credentials(path: Path, count: int) -> None:
    payload = {
        f"reviewer-{index:02d}": {"roles": ["reviewer"], "password_hash": "redacted"}
        for index in range(1, count + 1)
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_exact_active_batch_with_required_controls_is_ready(tmp_path: Path) -> None:
    root = tmp_path / "candidates"
    root.mkdir()
    for index in range(4):
        _write_candidate(root, str(index), doc_format=("매뉴얼" if index else "고용계약서"))
    credentials = tmp_path / "credentials.json"
    _write_credentials(credentials, 2)

    report = MODULE.evaluate(
        candidate_root=root,
        credentials_path=credentials,
        expected_documents=4,
        expected_reviewers=2,
        required_formats=["매뉴얼", "고용계약서"],
        require_enforcement=True,
        environ={
            "GOLDEN_REVIEWER_ASSIGNMENT_ENFORCED": "1",
            "GOLDEN_REVIEW_BLIND_ENFORCED": "true",
        },
    )

    assert report["ready"] is True
    assert report["active_batches"] == {"expert-review-20261010": 4}
    assert report["reviewer_account_count"] == 2


def test_wrong_cardinality_missing_format_and_disabled_controls_fail(tmp_path: Path) -> None:
    root = tmp_path / "candidates"
    root.mkdir()
    _write_candidate(root, "only", intended_label="")
    credentials = tmp_path / "credentials.json"
    _write_credentials(credentials, 1)

    report = MODULE.evaluate(
        candidate_root=root,
        credentials_path=credentials,
        expected_documents=2,
        expected_reviewers=2,
        required_formats=["응시자 지원서"],
        require_enforcement=True,
        environ={},
    )

    assert report["ready"] is False
    assert any("expected 2" in error for error in report["errors"])
    assert any("missing/invalid provisional grades" in error for error in report["errors"])
    assert any("required document formats missing" in error for error in report["errors"])
    assert any("reviewer account count" in error for error in report["errors"])
    assert any("reviewer controls disabled" in error for error in report["errors"])
