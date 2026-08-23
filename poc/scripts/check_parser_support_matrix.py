"""Validate that the documented parser support matrix matches the router.

This is a requirement gate, not an extraction-quality test. It prevents adding
or removing parser routes without updating the commercial support policy.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from koipa.modules.m2_preprocess.extractor import (  # noqa: E402
    SUPPORTED_FORMAT_GROUPS,
    SUPPORTED_PARSE_EXTENSIONS,
)


def _fail(message: str) -> int:
    print(f"[parser-support] FAIL: {message}")
    return 1


def _load_matrix(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _optional_extras(pyproject: Path) -> set[str]:
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return set(payload.get("project", {}).get("optional-dependencies", {}))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default="docs/parser_support_matrix.json")
    ap.add_argument("--pyproject", default="pyproject.toml")
    args = ap.parse_args()

    matrix_path = Path(args.matrix)
    pyproject_path = Path(args.pyproject)
    matrix = _load_matrix(matrix_path)
    formats = matrix.get("formats", [])
    if not formats:
        return _fail(f"no formats listed in {matrix_path}")

    documented_groups: dict[str, tuple[str, ...]] = {}
    documented_extensions: list[str] = []
    extras_used: set[str] = set()

    for item in formats:
        group = item.get("group")
        extensions = item.get("extensions")
        if not isinstance(group, str) or not isinstance(extensions, list):
            return _fail(f"invalid format entry: {item!r}")
        if group in documented_groups:
            return _fail(f"duplicate group: {group}")
        documented_groups[group] = tuple(str(ext) for ext in extensions)
        documented_extensions.extend(documented_groups[group])

        if not isinstance(item.get("commercial_default"), bool):
            return _fail(f"{group}: commercial_default must be boolean")
        if not isinstance(item.get("review_on_partial_table_loss"), bool):
            return _fail(f"{group}: review_on_partial_table_loss must be boolean")
        for extra in item.get("optional_extras", []):
            extras_used.add(str(extra))

    if documented_groups != SUPPORTED_FORMAT_GROUPS:
        return _fail(
            "docs/parser_support_matrix.json differs from "
            "extractor.SUPPORTED_FORMAT_GROUPS"
        )

    if tuple(documented_extensions) != SUPPORTED_PARSE_EXTENSIONS:
        return _fail("flattened extension order differs from SUPPORTED_PARSE_EXTENSIONS")

    unknown_extras = sorted(extras_used - _optional_extras(pyproject_path))
    if unknown_extras:
        return _fail(f"optional extras missing from pyproject.toml: {unknown_extras}")

    print(
        "[parser-support] PASS: "
        f"{len(documented_extensions)} extensions across {len(documented_groups)} groups"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
