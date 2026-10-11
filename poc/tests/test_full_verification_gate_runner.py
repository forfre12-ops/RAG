"""Windows launcher contracts for the Bash verification gate."""

import importlib.util
import os
from pathlib import Path

import pytest


RUNNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_full_verification_gate.py"
SPEC = importlib.util.spec_from_file_location("run_full_verification_gate", RUNNER_PATH)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


@pytest.mark.skipif(os.name != "nt", reason="Git Bash launcher is Windows-only")
def test_gate_runner_prepends_git_posix_utilities(monkeypatch):
    git_bash = Path(os.environ["ProgramFiles"]) / "Git" / "usr" / "bin" / "bash.exe"
    assert git_bash.is_file()
    monkeypatch.setenv("PATH", r"C:\Windows\System32")

    environment = RUNNER._gate_env(str(git_bash))

    assert environment["PATH"].split(os.pathsep)[0].casefold() == str(git_bash.parent).casefold()
