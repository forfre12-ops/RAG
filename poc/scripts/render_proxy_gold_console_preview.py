"""Render the Golden Set management console shell for local visual review."""
from __future__ import annotations

from pathlib import Path
import json

from lloydk.api.golden import _render_specledger_gold_console_html
from lloydk.services.proxy_gold_candidate_service import ProxyGoldCandidateService


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "datasets" / "proxy_gold" / "single_document_candidates" / "golden_set_console.preview.html"


def main() -> int:
    service = ProxyGoldCandidateService()
    records = service._candidates()  # local preview intentionally embeds only local candidate data
    payload = {
        "list": {"total": len(records), "summary": service.summary(), "candidates": records},
        "by_id": {record["doc_id"]: {**record, "decision_history": []} for record in records},
    }
    serialized = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = _render_specledger_gold_console_html().replace(
        "<script>", f"<script>window.__GOLDEN_PREVIEW__={serialized};</script><script>", 1,
    )
    OUT.write_text(page, encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
