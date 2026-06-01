"""Create a focused P1 boundary report from eval_p1_model_gold JSON output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_md(payload: dict, out: Path) -> None:
    metrics = payload.get("metrics", {})
    priority = payload.get("priority_errors", {})
    patterns = payload.get("pattern_counts", {})
    lines = [
        "# P1 Boundary Report",
        "",
        f"- Source report: `{payload.get('source_report', '')}`",
        f"- Model: `{payload.get('model_dir', '')}`",
        f"- Mode: `{payload.get('mode', '')}`",
        f"- n: {metrics.get('n', 0)}",
        f"- F1 macro: {metrics.get('f1_macro', 0):.3f}",
        f"- High-risk -> S3: {metrics.get('high_risk_to_s3', 0)}",
        "",
        "## Error Patterns",
        "",
        "| Pattern | Count |",
        "|---|---:|",
    ]
    for pattern, count in patterns.items():
        lines.append(f"| {pattern} | {count} |")

    for section, title in [
        ("high_risk_to_s3", "High-Risk Downgraded To S3"),
        ("s3_overclassified", "S3 Overclassified As High-Risk"),
    ]:
        rows = priority.get(section, [])
        lines += ["", f"## {title}", ""]
        if not rows:
            lines.append("_No sampled cases._")
            continue
        lines += ["| Doc | True | Pred | Source | Preview |", "|---|---:|---:|---|---|"]
        for row in rows:
            preview = (row.get("text_preview") or "").replace("\n", " ")[:160]
            lines.append(
                f"| `{row.get('doc_id', '')}` | {row.get('true', '')} | {row.get('pred', '')} | "
                f"{row.get('label_source', '')} | {preview} |"
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="reports/p1_v3_llm_gold_direct.json")
    ap.add_argument("--out", default="reports/p1_boundary_report.md")
    args = ap.parse_args()

    source = Path(args.report)
    payload = _load(source)
    if not payload:
        print(f"[p1-boundary] missing report: {source}")
        return 1
    payload["source_report"] = str(source)
    out = Path(args.out)
    _write_md(payload, out)
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[p1-boundary] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
