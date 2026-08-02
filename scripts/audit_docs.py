from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml") from exc


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "doc"
REGISTRY = DOC / "docs_registry.yaml"

ACTIVE_DIRS = (
    DOC / "result" / "open",
    DOC / "internal",
)

ALLOWED_ACTIVE_DIR_NAMES = {"assets", "real"}
BAD_DIR_NAMES = {"bak", "backup", "_superseded", "old", "tmp", "temp"}
BAD_FILE_PATTERNS = (
    re.compile(r"^~\$"),
    re.compile(r"\.bak$", re.IGNORECASE),
    re.compile(r"(^|[_\-.])bak($|[_\-.])", re.IGNORECASE),
    re.compile(r"(^|[_\-.])(copy|old|tmp|temp)($|[_\-.])", re.IGNORECASE),
    re.compile(r"(복사|임시)"),
)
DATE_DIR = re.compile(r"^20\d{6}$")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_registry() -> dict[str, Any]:
    if not REGISTRY.exists():
        raise SystemExit(f"missing registry: {rel(REGISTRY)}")
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}


def iter_active_files() -> list[Path]:
    files: list[Path] = []
    for base in ACTIVE_DIRS:
        if base.exists():
            files.extend(p for p in base.rglob("*") if p.is_file())
    return files


def audit(*, strict_unregistered: bool = False) -> int:
    data = load_registry()
    docs = data.get("documents") or []
    errors: list[str] = []
    warnings: list[str] = []

    ids: set[str] = set()
    registered_paths: set[str] = set()
    for item in docs:
        doc_id = str(item.get("id", "")).strip()
        path_raw = str(item.get("path", "")).strip()
        status = str(item.get("status", "")).strip()

        if not doc_id:
            errors.append("registry item without id")
        elif doc_id in ids:
            errors.append(f"duplicate document id: {doc_id}")
        ids.add(doc_id)

        if not path_raw:
            errors.append(f"{doc_id}: missing path")
            continue

        registered_paths.add(path_raw.replace("\\", "/"))
        path = ROOT / path_raw
        if status in {"current", "released"} and not path.exists():
            errors.append(f"{doc_id}: registered {status} path missing: {path_raw}")

    archive = DOC / "archive"
    for base in ACTIVE_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if archive in p.parents:
                continue
            if p.is_dir():
                name = p.name.lower()
                if name in ALLOWED_ACTIVE_DIR_NAMES:
                    continue
                if name in BAD_DIR_NAMES or DATE_DIR.match(p.name):
                    errors.append(f"backup/date directory under active docs: {rel(p)}")
            elif p.is_file():
                for pat in BAD_FILE_PATTERNS:
                    if pat.search(p.name):
                        errors.append(f"temporary/backup-like file under active docs: {rel(p)}")
                        break

    for p in iter_active_files():
        if p.suffix.lower() not in {".html", ".yaml", ".yml", ".md", ".xlsx", ".docx", ".pdf", ".sql"}:
            continue
        rp = rel(p)
        if rp.startswith("doc/result/감리정본/assets/"):
            continue
        if rp not in registered_paths:
            msg = f"unregistered active document: {rp}"
            if strict_unregistered:
                errors.append(msg)
            else:
                warnings.append(msg)

    print("Document audit")
    print(f"- registered documents: {len(docs)}")
    print(f"- active files scanned: {len(iter_active_files())}")
    print(f"- warnings: {len(warnings)}")
    print(f"- errors: {len(errors)}")
    for w in warnings[:50]:
        print(f"WARNING: {w}")
    if len(warnings) > 50:
        print(f"WARNING: ... {len(warnings) - 50} more")
    for e in errors:
        print(f"ERROR: {e}")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict-unregistered",
        action="store_true",
        help="fail on active docs that are not listed in doc/docs_registry.yaml",
    )
    args = parser.parse_args()
    return audit(strict_unregistered=args.strict_unregistered)


if __name__ == "__main__":
    sys.exit(main())
