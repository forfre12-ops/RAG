"""Build a local, read-only HTML catalog for proxy-gold candidates."""
from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
OUT = DIR / "candidate_catalog.v1.html"


def main() -> int:
    rows = []
    for meta_path in sorted(DIR.glob("*.metadata.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        doc_id = str(meta["doc_id"])
        revision = str(meta.get("content_revision_path") or "").strip()
        revision_path = (DIR / revision).resolve() if revision else None
        docs = [revision_path] if revision_path and revision_path.is_relative_to(DIR.resolve()) and revision_path.is_file() else list(DIR.glob(f"{doc_id}_*.md"))
        views = sorted(DIR.glob(f"{doc_id}_view.*.html"))
        if len(docs) != 1 or not views:
            continue
        text = docs[0].read_text(encoding="utf-8")
        rows.append({
            "id": doc_id,
            "title": str(meta.get("document_type") or docs[0].stem),
            "grade": str(meta.get("intended_label") or "-"),
            "origin": str(meta.get("document_origin") or "unknown"),
            "status": str(meta.get("candidate_status") or "proposed"),
            "chars": len(text),
            "href": quote(views[-1].name),
        })
    rows.sort(key=lambda r: r["id"])
    counts = Counter(r["grade"] for r in rows)
    body = "\n".join(
        "<tr data-grade='{grade}' data-status='{status}'><td><a href='{href}' target='_blank' "
        "rel='noopener'>{id}</a></td><td>{title}</td><td>{grade}</td><td>{origin}</td><td>{status}</td><td>{chars:,}</td></tr>".format(
            **{k: html.escape(str(v), quote=True) if k not in {"chars"} else v for k, v in row.items()}
        )
        for row in rows
    )
    chips = " ".join(f"<span>{html.escape(g)} {n}건</span>" for g, n in sorted(counts.items()))
    OUT.write_text(f"""<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Proxy Gold 후보 카탈로그</title><style>
body{{font:14px/1.5 system-ui,sans-serif;margin:0;background:#f4f7fb;color:#172033}}main{{max-width:1200px;margin:auto;padding:28px}}h1{{margin:0 0 6px}}.note{{background:#fff8df;border:1px solid #f1d686;border-radius:8px;padding:12px}}.chips span{{display:inline-block;background:#e8eef9;border-radius:999px;padding:5px 9px;margin:12px 6px 12px 0}}.filters{{display:flex;gap:12px;margin:8px 0 14px}}select{{padding:7px}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:9px;border-bottom:1px solid #e8ecf1;text-align:left}}a{{color:#155eef;font-weight:650}}.muted{{color:#667085}}</style>
<main><h1>Proxy Gold 후보 카탈로그</h1><p class=muted>생성일: 2026-08-08 · 로컬 읽기 전용 열람본</p>
<div class=note><b>중요:</b> 모든 항목은 합성 문서 후보이며, 사람 검수 전입니다. 이 카탈로그의 제안 등급은 실문서 골든·locked 평가정답 또는 실운영 정확도 근거가 아닙니다.</div>
<div class=chips>총 {len(rows)}건 · {chips}</div><div class=filters><label>등급 <select id=grade><option value=''>전체</option><option>TS</option><option>S1</option><option>S2</option><option>S3</option></select></label><label>상태 <select id=status><option value=''>전체</option><option>proposed</option><option>approved_proxy</option><option>deferred</option><option>rejected</option></select></label></div>
<table><thead><tr><th>후보 ID</th><th>문서명</th><th>제안 등급</th><th>출처</th><th>상태</th><th>글자 수</th></tr></thead><tbody>{body}</tbody></table></main>
<script>const f=()=>{{let g=document.querySelector('#grade').value,s=document.querySelector('#status').value;document.querySelectorAll('tbody tr').forEach(r=>r.hidden=(g&&r.dataset.grade!==g)||(s&&r.dataset.status!==s))}};document.querySelector('#grade').onchange=f;document.querySelector('#status').onchange=f;</script></html>""", encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
