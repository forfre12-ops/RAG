"""Run the Bash release gate with the shell that preserves Windows env vars.

On Windows, ``bash`` can resolve to WSL.  WSL does not reliably pass variables
such as DATABASE_URL to the Windows Python executable in ``.venv/Scripts``.
The release gate then silently falls back to the developer database instead of
its disposable PostgreSQL instance.  Prefer Git Bash, which is the shell the
gate was written for; POSIX hosts retain their normal ``bash`` lookup.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


def _bash() -> str:
    if os.name != "nt":
        shell = shutil.which("bash")
        if shell:
            return shell
        raise RuntimeError("bash is required for the full verification gate")

    configured = os.environ.get("KOIPA_GIT_BASH", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "Git" / "usr" / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(
        "Git Bash is required on Windows for the full verification gate; "
        "install it or set KOIPA_GIT_BASH to its bash.exe path"
    )


def main() -> int:
    poc_root = Path(__file__).resolve().parents[1]
    return subprocess.run([_bash(), "scripts/full_verification_gate.sh"], cwd=poc_root).returncode


if __name__ == "__main__":
    raise SystemExit(main())
