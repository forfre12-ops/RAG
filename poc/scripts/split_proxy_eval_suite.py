"""Publish a development-200 / final-800 split from a frozen Proxy corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Sequence

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC / "src"))

from lloydk.proxy_eval_split import FrozenEvalSplitError, split_frozen_proxy_eval


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise FrozenEvalSplitError(f"input must be a JSONL file: {path}")
    rows: list[dict[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FrozenEvalSplitError(f"invalid JSON at {path}:{number}") from exc
        if not isinstance(row, dict):
            raise FrozenEvalSplitError(f"row must be an object at {path}:{number}")
        rows.append(row)
    return rows


def _write(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _jsonl(rows: Sequence[dict[str, object]]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for row in rows
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="quality-gated frozen Proxy 1,000 JSONL")
    parser.add_argument("--out-dir", required=True, help="new immutable artifact directory")
    args = parser.parse_args(argv)
    try:
        source = Path(args.input)
        out_dir = Path(args.out_dir)
        if out_dir.exists():
            raise FrozenEvalSplitError(f"output directory already exists: {out_dir}")
        result = split_frozen_proxy_eval(_read_jsonl(source))
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{out_dir.name}.", dir=out_dir.parent) as temporary_root:
            staging = Path(temporary_root) / out_dir.name
            staging.mkdir()
            development = _jsonl(result.development)
            final = _jsonl(result.final)
            manifest = {
                **result.audit,
                "source_path": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "development_sha256": hashlib.sha256(development).hexdigest(),
                "final_sha256": hashlib.sha256(final).hexdigest(),
                "claim_scope": "Proxy-only model selection and regression; not customer-real accuracy evidence or Locked Gold.",
            }
            _write(staging / "development_200.jsonl", development)
            _write(staging / "final_800.locked.jsonl", final)
            _write(
                staging / "manifest.json",
                (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
            )
            _write(staging / "COMPLETE.json", b'{"schema":"frozen-proxy-eval-split-complete-v1"}\n')
            os.replace(staging, out_dir)
    except FrozenEvalSplitError as exc:
        raise SystemExit(f"split failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
