"""Assemble the exact 1,000-record proxy-gold candidate set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC))
sys.path.insert(0, str(_POC / "src"))

from lloydk.hygiene import text_hash  # noqa: E402
from lloydk.proxy_corpus import (  # noqa: E402
    DEFAULT_TARGET_COUNTS,
    ORIGIN_EXPECTATION_PROFILES,
    PUBLIC_REAL,
    assemble_proxy_gold,
)
from scripts.build_proxy_scenarios import load_catalog  # noqa: E402


DEFAULT_ORIGIN_PROFILE = "public-s3-hybrid-v2"


class CorpusLoadError(ValueError):
    """A corpus cannot be used without weakening leakage protection."""


def _atomic_write_new(path: Path, payload: bytes) -> None:
    """Publish exact bytes once; never normalize newlines or overwrite a run."""
    if path.exists():
        raise CorpusLoadError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _json_loads_strict(value: str, *, location: str) -> Any:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"non-standard JSON constant {constant}")

    try:
        return json.loads(value, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        detail = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        raise CorpusLoadError(f"malformed JSON at {location}: {detail}") from exc


def _resolve_corpus_files(paths: list[Path], *, purpose: str) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if not path.exists():
            raise CorpusLoadError(f"{purpose} path does not exist: {path}")
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = sorted(
                candidate
                for candidate in path.iterdir()
                if candidate.is_file()
                and candidate.suffix.lower() in {".json", ".jsonl"}
            )
            if not candidates:
                raise CorpusLoadError(
                    f"{purpose} directory has no .json/.jsonl files: {path}"
                )
        else:
            raise CorpusLoadError(
                f"{purpose} path is not a regular file or directory: {path}"
            )

        for candidate in candidates:
            if candidate.suffix.lower() not in {".json", ".jsonl"}:
                raise CorpusLoadError(
                    f"unsupported {purpose} file extension (expected .json/.jsonl): {candidate}"
                )
            resolved = candidate.resolve()
            if resolved not in seen:
                files.append(candidate)
                seen.add(resolved)
    return files


def _read_corpus_file(path: Path, *, purpose: str) -> list[dict]:
    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CorpusLoadError(f"cannot read {purpose} file {path}: {exc}") from exc
    if not payload.strip():
        raise CorpusLoadError(f"empty {purpose} file: {path}")

    located_rows: list[tuple[str, object]] = []
    if path.suffix.lower() == ".jsonl":
        for line_no, line in enumerate(payload.splitlines(), 1):
            if line.strip():
                located_rows.append(
                    (
                        f"{path}:{line_no}",
                        _json_loads_strict(line, location=f"{path}:{line_no}"),
                    )
                )
    else:
        parsed = _json_loads_strict(payload, location=str(path))
        if isinstance(parsed, list):
            located_rows.extend(
                (f"{path}[{index}]", row) for index, row in enumerate(parsed)
            )
        else:
            located_rows.append((str(path), parsed))

    if not located_rows:
        raise CorpusLoadError(f"empty {purpose} corpus: {path}")

    rows: list[dict] = []
    for location, row in located_rows:
        if not isinstance(row, dict):
            raise CorpusLoadError(f"{purpose} record must be an object at {location}")
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            raise CorpusLoadError(f"missing required text at {location}")
        family_id = row.get("document_family_id")
        if not isinstance(family_id, str) or not family_id.strip():
            raise CorpusLoadError(f"missing required document_family_id at {location}")
        rows.append(row)
    return rows


def _load_corpus(
    paths: list[Path], *, purpose: str
) -> tuple[list[dict], dict[str, object]]:
    files = _resolve_corpus_files(paths, purpose=purpose)
    rows: list[dict] = []
    loaded_files: list[dict[str, object]] = []
    for path in files:
        file_rows = _read_corpus_file(path, purpose=purpose)
        rows.extend(file_rows)
        loaded_files.append({"path": str(path), "rows": len(file_rows)})

    families = {str(row["document_family_id"]).strip() for row in rows}
    text_hashes = {text_hash(str(row["text"])) for row in rows}
    stats: dict[str, object] = {
        "requested_paths": [str(path) for path in paths],
        "loaded_files": loaded_files,
        "file_count": len(loaded_files),
        "row_count": len(rows),
        "unique_family_ids": len(families),
        "unique_text_hashes": len(text_hashes),
    }
    return rows, stats


def _read_jsonl(paths: list[Path]) -> list[dict]:
    """Compatibility wrapper for strict candidate loading."""
    rows, _ = _load_corpus(paths, purpose="input")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble a leakage-safe 1,000-record proxy-gold candidate"
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="eligible candidate JSONL; repeatable",
    )
    parser.add_argument(
        "--blocked-corpus",
        action="append",
        default=[],
        help="train/eval path whose families must be excluded",
    )
    parser.add_argument(
        "--origin-profile",
        choices=sorted(ORIGIN_EXPECTATION_PROFILES),
        default=DEFAULT_ORIGIN_PROFILE,
        help=(
            "origin contract for the primary frozen set; default public-s3-hybrid-v2 "
            "uses licensed public originals only for S3"
        ),
    )
    parser.add_argument(
        "--catalog",
        default="datasets/proxy_gold/scenario_catalog.v1.json",
        help="factor-profile catalog that binds exact scenario quotas",
    )
    parser.add_argument(
        "--out", default="datasets/proxy_gold/proxy_gold_1000.candidate.jsonl"
    )
    parser.add_argument("--report", default="reports/proxy_gold_1000.assembly.json")
    args = parser.parse_args(argv)

    try:
        rows, input_stats = _load_corpus(
            [Path(value) for value in args.input], purpose="input"
        )
        blocked_rows, blocked_stats = _load_corpus(
            [Path(value) for value in args.blocked_corpus], purpose="blocked corpus"
        )
    except CorpusLoadError as exc:
        raise SystemExit(f"corpus loading failed: {exc}") from exc

    blocked = {str(row["document_family_id"]).strip() for row in blocked_rows}
    blocked_doc_ids = {
        str(row.get("doc_id") or "").strip()
        for row in blocked_rows
        if str(row.get("doc_id") or "").strip()
    }
    blocked_text_hashes = {text_hash(str(row["text"])) for row in blocked_rows}
    catalog_path = Path(args.catalog)
    try:
        catalog, catalog_scenarios = load_catalog(catalog_path)
    except (OSError, json.JSONDecodeError, SystemExit) as exc:
        raise SystemExit(f"catalog loading failed: {exc}") from exc
    scenario_targets = {
        str(row["scenario_id"]): int(row["target_count"])
        for row in catalog_scenarios
    }
    scenario_target_grades = {
        str(row["scenario_id"]): str(row["label"]) for row in catalog_scenarios
    }
    scenario_factor_profiles = {
        str(row["scenario_id"]): str(row["factor_profile_id"])
        for row in catalog_scenarios
    }
    use_scenario_contract = args.origin_profile in {
        "matched-synthetic-v1",
        "public-s3-hybrid-v2",
    }
    if args.origin_profile == "public-s3-hybrid-v2":
        scenario_targets = {
            scenario_id: target
            for scenario_id, target in scenario_targets.items()
            if scenario_target_grades[scenario_id] != "S3"
        }
        scenario_target_grades = {
            scenario_id: grade
            for scenario_id, grade in scenario_target_grades.items()
            if grade != "S3"
        }
        scenario_factor_profiles = {
            scenario_id: profile
            for scenario_id, profile in scenario_factor_profiles.items()
            if scenario_id in scenario_targets
        }
    result = assemble_proxy_gold(
        rows,
        targets=DEFAULT_TARGET_COUNTS,
        blocked_doc_ids=blocked_doc_ids,
        blocked_family_ids=blocked,
        blocked_text_hashes=blocked_text_hashes,
        require_catalog_usage_contract=True,
        required_synthetic_gate_version="proxy_semantic_quality_v2",
        intended_use="evaluation",
        expected_origins=ORIGIN_EXPECTATION_PROFILES[args.origin_profile],
        scenario_targets=scenario_targets if use_scenario_contract else None,
        scenario_target_grades=(
            scenario_target_grades if use_scenario_contract else None
        ),
        scenario_factor_profiles=(
            scenario_factor_profiles if use_scenario_contract else None
        ),
    )

    report = result.to_dict()
    report["evaluation_architecture"] = {
        "set_role": "primary_frozen_proxy",
        "origin_profile": args.origin_profile,
        "public_real_s3_included": (
            ORIGIN_EXPECTATION_PROFILES[args.origin_profile].get("S3") == PUBLIC_REAL
        ),
        "claim_scope": "proxy regression and calibration only; not customer-real accuracy",
        "scenario_quota_contract": use_scenario_contract,
        "synthetic_high_grade_scenario_quota_contract": (
            args.origin_profile == "public-s3-hybrid-v2"
        ),
        "catalog": {
            "path": str(catalog_path),
            "version": str(catalog.get("version") or "unknown"),
            "factor_profile_schema_id": str(
                catalog.get("factor_profile_schema_id") or "legacy"
            ),
            "sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        },
    }
    report["input_corpus"] = input_stats
    report["blocked_corpus"] = blocked_stats
    output = Path(args.out)
    report_path = Path(args.report)
    publish_targets = [report_path, *([output] if result.ready else [])]
    existing = [str(path) for path in publish_targets if path.exists()]
    if existing:
        raise SystemExit(f"refusing to overwrite artifact: {', '.join(existing)}")

    if result.ready:
        payload = b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
            for row in result.selected
        )
        report["artifact"] = {
            "path": str(output),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "records": len(result.selected),
        }
        _atomic_write_new(output, payload)
    report_payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    _atomic_write_new(report_path, report_payload)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if result.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
