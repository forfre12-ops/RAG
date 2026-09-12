"""Render the current single-document realism pilot as local HTML."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

from scripts.build_case_pilot_html import _render_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc-id", default="GOLD-CAND-TS-MFG-017")
    parser.add_argument(
        "--view-version",
        default="v1",
        help="HTML view version suffix (for example: v2).",
    )
    args = parser.parse_args()
    folder = _ROOT / "datasets/proxy_gold/single_document_candidates"
    metadata = json.loads((folder / f"{args.doc_id}.metadata.json").read_text(encoding="utf-8"))
    revision = str(metadata.get("content_revision_path") or "").strip()
    revision_path = (folder / revision).resolve() if revision else None
    if revision_path and revision_path.is_relative_to(folder.resolve()) and revision_path.is_file():
        source = revision_path
    else:
        matches = sorted(folder.glob(f"{args.doc_id}_*.md"))
        if len(matches) != 1:
            raise ValueError(f"expected one markdown candidate for {args.doc_id}")
        source = matches[0]
    out = folder / f"{args.doc_id}_view.{args.view_version}.html"
    if out.exists():
        raise FileExistsError(f"refusing to overwrite: {out}")
    content = _render_markdown(source.read_text(encoding="utf-8"))
    out.write_text(
        f"""<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{metadata['doc_id']}</title><style>body{{margin:0;background:#eef2f7;color:#172033;font:16px/1.75 system-ui,'Malgun Gothic',sans-serif}}main{{max-width:980px;margin:28px auto;background:#fff;border-radius:14px;padding:34px;box-shadow:0 1px 5px #123}}h1{{font-size:27px;border-bottom:2px solid #1d4ed8;padding-bottom:12px}}h2{{font-size:19px;color:#173f6b;margin-top:30px}}p{{margin:8px 0}}table{{width:100%;border-collapse:collapse;margin:14px 0}}th{{background:#eaf1fb}}th,td{{border:1px solid #cbd5e1;padding:9px;vertical-align:top;text-align:left}}</style><body><main>{content}</main></body></html>""",
        encoding="utf-8",
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
