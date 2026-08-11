"""Render the real-document intake UI for local design review."""
from __future__ import annotations

from pathlib import Path

from lloydk.api.golden import _render_actual_document_intake_html


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "datasets" / "proxy_gold" / "single_document_candidates" / "actual_document_intake.preview.html"


def main() -> int:
    OUT.write_text(_render_actual_document_intake_html(), encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
