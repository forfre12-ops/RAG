"""Build-SHA scoped LLM token/cost summary from the append-only usage JSONL.

Usage records without a build SHA are intentionally excluded: historical test or
development calls must not be presented as a deployed revision's operating cost.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _number(row: dict[str, Any], key: str, kind: type[int] | type[float]) -> int | float:
    value = row.get(key, 0)
    try:
        return kind(value or 0)
    except (TypeError, ValueError):
        return kind()


def summarize(path: Path, build_sha: str) -> dict[str, Any]:
    if not build_sha or build_sha.lower() == "unknown":
        raise ValueError("--build-sha must be a concrete deployed build SHA")

    groups: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "successful_calls": 0, "failed_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
    )
    called_at: list[str] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if row.get("build_sha") != build_sha:
            continue
        key = tuple(str(row.get(field) or "unknown") for field in ("provider", "model", "purpose", "billing_phase"))
        group = groups[key]
        group["calls"] += 1
        group["input_tokens"] += _number(row, "input_tokens", int)
        group["output_tokens"] += _number(row, "output_tokens", int)
        group["cost_usd"] += _number(row, "cost_usd", float)
        if row.get("success", True):
            group["successful_calls"] += 1
        else:
            group["failed_calls"] += 1
        if isinstance(row.get("called_at"), str):
            called_at.append(row["called_at"])

    rows = [
        {
            "provider": provider,
            "model": model,
            "purpose": purpose,
            "billing_phase": phase,
            **values,
            "cost_usd": round(values["cost_usd"], 6),
        }
        for (provider, model, purpose, phase), values in sorted(groups.items())
    ]
    totals = {
        key: sum(row[key] for row in rows)
        for key in ("calls", "successful_calls", "failed_calls", "input_tokens", "output_tokens", "cost_usd")
    }
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return {
        "kind": "koipa-llm-usage-summary",
        "schema_version": "1.0",
        "build_sha": build_sha,
        "source": str(path),
        "records": totals["calls"],
        "malformed_source_lines": malformed,
        "time_range": {"from": min(called_at) if called_at else None, "to": max(called_at) if called_at else None},
        "totals": totals,
        "by_provider_model_purpose": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=_PROJECT_ROOT / "reports" / "llm_usage.jsonl")
    parser.add_argument("--build-sha", required=True)
    parser.add_argument("--output", type=Path, default=_PROJECT_ROOT / "reports" / "llm_usage_summary.json")
    args = parser.parse_args()
    report = summarize(args.input, args.build_sha)
    if not report["records"]:
        raise SystemExit(f"no usage records for build SHA {args.build_sha}; report was not written")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
