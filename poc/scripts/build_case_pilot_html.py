"""Render one authored case bundle as a readable local HTML file."""

from __future__ import annotations

import html
import json
from pathlib import Path


def _render_markdown(markdown: str) -> str:
    lines = markdown.splitlines()
    rendered: list[str] = []
    table: list[list[str]] = []

    def flush_table() -> None:
        nonlocal table
        if not table:
            return
        header, *body = table
        rendered.append("<table><thead><tr>" + "".join(f"<th>{cell}</th>" for cell in header) + "</tr></thead><tbody>")
        rendered.extend("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in body)
        rendered.append("</tbody></table>")
        table = []

    for line in lines:
        if line.startswith("|") and line.endswith("|"):
            cells = [html.escape(cell.strip()) for cell in line.strip("|").split("|")]
            if not all(set(cell) <= {"-", ":"} for cell in cells):
                table.append(cells)
            continue
        flush_table()
        if line.startswith("# "):
            rendered.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            rendered.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            rendered.append(f"<p class='bullet'>• {html.escape(line[2:])}</p>")
        elif line.strip():
            rendered.append(f"<p>{html.escape(line)}</p>")
    flush_table()
    return "\n".join(rendered)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    folder = root / "datasets/proxy_gold/case_pilot_v1/TS-MFG-017"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    docs = [(name, _render_markdown((folder / name).read_text(encoding="utf-8"))) for name in manifest["documents"]]
    out = folder / "case_view.v2.html"
    if out.exists():
        raise FileExistsError(f"refusing to overwrite: {out}")
    buttons = "".join(f"<button data-tab='{index}'>{html.escape(name.split('_', 1)[1].removesuffix('.md'))}</button>" for index, (name, _) in enumerate(docs))
    panels = "".join(f"<article class='doc' data-panel='{index}'>{content}</article>" for index, (_, content) in enumerate(docs))
    out.write_text(f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>TS-MFG-017 사건 문서 묶음</title><style>
body{{margin:0;background:#eef2f7;color:#172033;font:16px/1.75 system-ui,'Malgun Gothic',sans-serif}}main{{max-width:1050px;margin:auto;padding:28px}}.hero{{background:#102a43;color:#fff;padding:24px 28px;border-radius:14px;margin-bottom:16px}}.hero p{{color:#d9e6f2;margin:6px 0 0}}nav{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}}button{{border:1px solid #cbd5e1;background:white;border-radius:8px;padding:9px 13px;cursor:pointer;font:inherit}}button.active{{background:#1d4ed8;color:#fff;border-color:#1d4ed8}}.doc{{display:none;background:white;border-radius:14px;padding:28px;box-shadow:0 1px 5px #1232;min-height:600px}}.doc.active{{display:block}}h1{{font-size:26px;margin-top:0;border-bottom:2px solid #1d4ed8;padding-bottom:12px}}h2{{font-size:19px;margin-top:28px;color:#173f6b}}p{{margin:8px 0}}.bullet{{padding-left:10px}}table{{width:100%;border-collapse:collapse;margin:14px 0;font-size:14px}}th{{background:#eaf1fb}}th,td{{border:1px solid #cbd5e1;padding:9px;vertical-align:top;text-align:left}}.notice{{font-size:13px;color:#526075;margin-top:18px}}</style></head><body><main><section class='hero'><strong>가상 내부 문서 현실성 파일럿</strong><h2>TS-MFG-017 · HBM-E 적층 전 세정 순서 변경</h2><p>문서 간 번호·검증 결과·회의 결정·승인 조건이 연결된 하나의 사건 묶음입니다.</p></section><nav>{buttons}</nav>{panels}<p class='notice'>가상 사례입니다. 실제 고객사 문서가 아니며, 검수 전 골든셋에는 편입되지 않습니다.</p></main><script>const b=[...document.querySelectorAll('button')],p=[...document.querySelectorAll('.doc')];function show(i){{b.forEach((x,n)=>x.classList.toggle('active',n==i));p.forEach((x,n)=>x.classList.toggle('active',n==i))}}b.forEach((x,i)=>x.onclick=()=>show(i));show(0);</script></body></html>""", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
