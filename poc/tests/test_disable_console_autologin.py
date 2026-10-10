from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "disable_console_autologin.py"
_SPEC = importlib.util.spec_from_file_location("disable_console_autologin", SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_sanitize_env_removes_prefill_without_copying_token_and_updates_batch(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "AUTH_MODE=both\n"
        "CONSOLE_LOGIN_PREFILL_TOKEN=do-not-copy-this-secret\n"
        "CONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE=1\n"
        "GOLDEN_DEFAULT_REVIEW_BATCH=old_batch\n",
        encoding="utf-8",
    )

    result = _MODULE.sanitize_env(env, default_review_batch="review_20261010")

    assert result["changed"] is True
    assert result["removed_autologin_settings"] == 2
    text = env.read_text(encoding="utf-8")
    assert "CONSOLE_LOGIN_PREFILL" not in text
    assert "do-not-copy-this-secret" not in text
    assert "GOLDEN_DEFAULT_REVIEW_BATCH=review_20261010" in text


def test_sanitize_env_dry_run_preserves_file(tmp_path: Path):
    env = tmp_path / ".env"
    original = "CONSOLE_LOGIN_PREFILL_TOKEN=a.b.c\nCONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE=1\n"
    env.write_text(original, encoding="utf-8")

    result = _MODULE.sanitize_env(env, dry_run=True)

    assert result["changed"] is True
    assert env.read_text(encoding="utf-8") == original
