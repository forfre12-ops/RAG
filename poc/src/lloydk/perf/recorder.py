"""perf JSON 산출 + 최근 N회 추세 로딩."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_report(report_dict: dict[str, Any], *, out_dir: Path | str = "reports/perf") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = report_dict.get("ts") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sha = (report_dict.get("env", {}) or {}).get("git_sha") or "unknown"
    mode = report_dict.get("mode") or "dryrun"
    safe_ts = ts.replace(":", "").replace("-", "")
    fname = f"perf_{safe_ts}_{mode}_{sha}.json"
    path = out / fname
    path.write_text(json.dumps(report_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = out / f"perf_latest_{mode}.json"
    latest.write_text(json.dumps(report_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_history(out_dir: Path | str = "reports/perf", *, mode: str = "dryrun", limit: int = 5) -> list[dict[str, Any]]:
    out = Path(out_dir)
    if not out.exists():
        return []
    files = sorted(
        [p for p in out.glob(f"perf_*_{mode}_*.json")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    history: list[dict[str, Any]] = []
    for f in files:
        try:
            history.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return history
