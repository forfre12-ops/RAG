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


def _gate_env(shell: str) -> dict[str, str]:
    """Give Git Bash its POSIX utilities even when launched from a bare shell.

    The desktop runner can start Python with a minimal Windows ``PATH``.  Git
    Bash finds ``bash.exe`` in that case but cannot subsequently find its own
    ``dirname`` or ``date`` utilities, so the verification gate fails before
    it has tested anything.  Prepending Git's ``usr/bin`` keeps the caller's
    environment intact while making the Bash script self-contained.
    """
    env = os.environ.copy()
    if os.name != "nt":
        return env

    executable = Path(shell).resolve()
    utility_dirs = [executable.parent]
    for parent in executable.parents:
        candidate = parent / "usr" / "bin"
        if candidate.is_dir():
            utility_dirs.append(candidate)
            break

    existing = env.get("PATH", "").split(os.pathsep)
    prefixes = [str(directory) for directory in utility_dirs if directory.is_dir()]
    unique: list[str] = []
    seen: set[str] = set()
    for entry in [*prefixes, *existing]:
        if entry and entry.casefold() not in seen:
            unique.append(entry)
            seen.add(entry.casefold())
    env["PATH"] = os.pathsep.join(unique)
    return env


def main() -> int:
    poc_root = Path(__file__).resolve().parents[1]
    shell = _bash()
    return subprocess.run(
        [shell, "scripts/full_verification_gate.sh"],
        cwd=poc_root,
        env=_gate_env(shell),
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
