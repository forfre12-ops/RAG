"""Contracts for the fast/release verification split."""
from __future__ import annotations

import importlib.util
from pathlib import Path


_POC = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _POC / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREFLIGHT = _load("check_acceptance_parser_preflight", "check_acceptance_parser_preflight.py")
FAST = _load("verify_fast", "verify_fast.py")


def test_parser_preflight_rejects_empty_or_numeric_loss():
    doc = {"expected_numeric_tokens": ["1,000", "2500"]}

    reasons = PREFLIGHT.validate_document(doc, text="", quality=0.0, error="rhwp missing")

    assert "extract_error=rhwp missing" in reasons
    assert "empty_or_zero_quality" in reasons
    assert "missing_numeric_tokens=1,000,2500" in reasons


def test_parser_preflight_accepts_complete_legacy_extract():
    doc = {"expected_numeric_tokens": ["1,000", "2500"]}

    assert PREFLIGHT.validate_document(doc, text="value 1,000 and 2500", quality=0.95, error=None) == []


def test_fast_profiles_keep_release_only_steps_outside_their_commands():
    commands = FAST.commands("classify")
    flattened = " ".join(piece for command in commands for piece in command)

    assert "uv lock --locked" in flattened
    assert "test_policy_engine.py" in flattened
    assert "docker" not in flattened
    assert "run_acceptance.py" not in flattened
