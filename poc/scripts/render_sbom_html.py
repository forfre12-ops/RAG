"""B6 — CycloneDX SBOM → 단일 HTML 시각화.

용도:
- 운영 전환 시 발주처 검수 첨부용 단일 HTML
- 라이선스 분포 + 위험 등급(Strong Copyleft/Weak/Permissive) + 의존성 트리
- 외부 의존 0 (CSS·SVG 인라인, doc/20·doc/17과 동일 NovaX 톤다운)

사용:
  python scripts/render_sbom_html.py
  python scripts/render_sbom_html.py --in licenses/sbom.cyclonedx.json --out doc/21_OSS_SBOM_보고서.html
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
POC_ROOT = HERE.parent
REPO_ROOT = POC_ROOT.parent
DEFAULT_IN = POC_ROOT / "licenses" / "sbom.cyclonedx.json"
DEFAULT_OUT = REPO_ROOT / "doc" / "21_OSS_SBOM_보고서.html"

# 라이선스 위험 등급 — doc/14 § 3 정책 정합
STRONG_COPYLEFT = {"AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later", "GPL-3.0", "GPL-2.0", "SSPL-1.0"}
WEAK_COPYLEFT = {"LGPL-2.1", "LGPL-3.0", "LGPL-3.0-only", "MPL-2.0", "EPL-2.0"}
PERMISSIVE = {"MIT", "MIT License", "BSD-3-Clause", "BSD-2-Clause", "BSD", "BSD License", "Apache-2.0", "Apache Software License", "ISC", "PSF-2.0", "Python Software Foundation License"}


def _classify_license(lic_id: str) -> str:
    if not lic_id:
        return "Unknown"
    if lic_id in STRONG_COPYLEFT or any(s in lic_id for s in ("AGPL", "GPL-3", "GPL-2", "SSPL")):
        return "StrongCopyleft"
    if lic_id in WEAK_COPYLEFT or any(s in lic_id for s in ("LGPL", "MPL", "EPL")):
        return "WeakCopyleft"
    if lic_id in PERMISSIVE or any(s in lic_id for s in ("MIT", "BSD", "Apache", "ISC", "PSF")):
        return "Permissive"
    return "Unknown"


CSS = """
:root {
  --bg:#ffffff; --bg-surface:#fafafa; --bg-elevated:#ffffff;
  --text:#0a0a0a; --text-soft:#525252; --text-dim:#737373; --text-faint:#a3a3a3;
  --border:rgba(0,0,0,0.08); --border-strong:rgba(0,0,0,0.16);
  --c-perm:#16a34a; --c-weak:#d97706; --c-strong:#dc2626; --c-unknown:#737373;
  --radius:8px; --radius-pill:999px;
  --font-sans:-apple-system,BlinkMacSystemFont,"Pretendard","Apple SD Gothic Neo","Noto Sans KR",sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
* {box-sizing:border-box;}
body {margin:0; background:var(--bg); color:var(--text); font-family:var(--font-sans); font-size:14.5px; line-height:1.65;}
.wrap {max-width:1280px; margin:0 auto; padding:24px 32px 96px;}
header.top {padding:24px 0 16px; margin-bottom:24px; border-bottom:1px solid var(--border-strong);}
header.top h1 {margin:0 0 8px; font-size:28px; font-weight:700; letter-spacing:-0.02em;}
header.top .meta {color:var(--text-soft); font-size:13px; font-family:var(--font-mono);}
header.top .meta span {display:inline-block; margin-right:16px;}
section {margin:32px 0;}
section h2 {font-size:18px; font-weight:600; margin:0 0 12px; padding-bottom:8px; border-bottom:1px solid var(--border);}
.widgets {display:grid; grid-template-columns:repeat(4,1fr); gap:12px;}
.widget {border:1px solid var(--border-strong); border-radius:var(--radius); padding:14px 16px; background:var(--bg-elevated);}
.widget .lbl {font-size:11.5px; color:var(--text-dim); font-family:var(--font-mono); margin-bottom:6px;}
.widget .val {font-size:24px; font-weight:700; letter-spacing:-0.02em;}
.widget.perm .val {color:var(--c-perm);}
.widget.weak .val {color:var(--c-weak);}
.widget.strong .val {color:var(--c-strong);}
.widget.unknown .val {color:var(--c-unknown);}
table {width:100%; border-collapse:collapse; font-size:13px;}
table th, table td {text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); vertical-align:top;}
table th {background:var(--bg-surface); font-weight:600; color:var(--text-soft); font-size:12px; text-transform:uppercase; letter-spacing:0.04em;}
table tr:hover td {background:var(--bg-surface);}
.cell-mono {font-family:var(--font-mono); font-size:12.5px;}
.badge {display:inline-block; padding:1px 8px; border-radius:var(--radius-pill); font-size:11px; font-family:var(--font-mono); font-weight:600; border:1px solid currentColor;}
.badge.Permissive {color:var(--c-perm);}
.badge.WeakCopyleft {color:var(--c-weak);}
.badge.StrongCopyleft {color:var(--c-strong); background:rgba(220,38,38,0.05);}
.badge.Unknown {color:var(--c-unknown);}
.bar-row {display:flex; align-items:center; gap:8px; margin:4px 0;}
.bar-row .name {min-width:200px; font-family:var(--font-mono); font-size:12px;}
.bar-row .count {min-width:40px; text-align:right; font-family:var(--font-mono); font-size:12px;}
.bar-row .bar {flex:1; height:14px; background:var(--bg-surface); border-radius:4px; overflow:hidden;}
.bar-row .bar-fill {height:100%; background:var(--text);}
.foot {color:var(--text-faint); font-size:11.5px; margin-top:48px; padding-top:16px; border-top:1px solid var(--border);}
"""


def render(in_path: Path, out_path: Path) -> Path:
    sbom = json.loads(in_path.read_text(encoding="utf-8"))
    components = sbom.get("components", [])

    # 컴포넌트별 라이선스 추출 + 위험 등급 분류
    rows = []
    risk_count = Counter()
    lic_count = Counter()
    for c in components:
        name = c.get("name", "?")
        version = c.get("version", "?")
        purl = c.get("purl", "")
        lic_id = "?"
        for lic in (c.get("licenses") or []):
            lic_obj = lic.get("license") or {}
            lic_id = lic_obj.get("id") or lic_obj.get("name") or "?"
            break  # 첫 라이선스만 표시
        risk = _classify_license(lic_id)
        risk_count[risk] += 1
        lic_count[lic_id] += 1
        rows.append({"name": name, "version": version, "license": lic_id, "risk": risk, "purl": purl})

    total = len(rows)
    n_perm = risk_count["Permissive"]
    n_weak = risk_count["WeakCopyleft"]
    n_strong = risk_count["StrongCopyleft"]
    n_unknown = risk_count["Unknown"]

    # 라이선스 분포 막대
    max_lic_count = max(lic_count.values()) if lic_count else 1
    bars = []
    for lic, n in lic_count.most_common(15):
        pct = n / max_lic_count
        risk_cls = _classify_license(lic)
        bars.append(
            f'<div class="bar-row">'
            f'<span class="name">{html.escape(str(lic))}</span>'
            f'<span class="count">{n}</span>'
            f'<div class="bar"><div class="bar-fill" style="width:{pct*100:.1f}%;background:'
            f'{{Permissive:"#16a34a",WeakCopyleft:"#d97706",StrongCopyleft:"#dc2626",Unknown:"#737373"}}["{risk_cls}"]"></div></div>'
            f'</div>'
        )
    # background 문자열 인라인 JS 대신 직접 색상 결정
    bars = []
    risk_color = {"Permissive": "#16a34a", "WeakCopyleft": "#d97706", "StrongCopyleft": "#dc2626", "Unknown": "#737373"}
    for lic, n in lic_count.most_common(15):
        pct = n / max_lic_count
        risk_cls = _classify_license(lic)
        color = risk_color.get(risk_cls, "#0a0a0a")
        bars.append(
            f'<div class="bar-row">'
            f'<span class="name">{html.escape(str(lic))}</span>'
            f'<span class="count">{n}</span>'
            f'<div class="bar"><div class="bar-fill" style="width:{pct*100:.1f}%;background:{color};"></div></div>'
            f'</div>'
        )

    # 위험 컴포넌트만 별도 표
    risk_rows = [r for r in rows if r["risk"] in {"StrongCopyleft", "WeakCopyleft"}]
    risk_rows.sort(key=lambda x: (x["risk"], x["name"]))
    risk_table_rows = "".join(
        f'<tr><td class="cell-mono">{html.escape(r["name"])}</td>'
        f'<td class="cell-mono">{html.escape(r["version"])}</td>'
        f'<td class="cell-mono">{html.escape(r["license"])}</td>'
        f'<td><span class="badge {r["risk"]}">{r["risk"]}</span></td></tr>'
        for r in risk_rows
    )

    # 전체 표 — 처음 50개만 (Permissive는 너무 많아서)
    all_rows_sorted = sorted(rows, key=lambda x: (
        {"StrongCopyleft": 0, "WeakCopyleft": 1, "Unknown": 2, "Permissive": 3}.get(x["risk"], 4),
        x["name"],
    ))
    all_table_rows = "".join(
        f'<tr><td class="cell-mono">{html.escape(r["name"])}</td>'
        f'<td class="cell-mono">{html.escape(r["version"])}</td>'
        f'<td class="cell-mono">{html.escape(r["license"])}</td>'
        f'<td><span class="badge {r["risk"]}">{r["risk"]}</span></td></tr>'
        for r in all_rows_sorted
    )

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    spec_version = sbom.get("specVersion", "?")
    bom_format = sbom.get("bomFormat", "?")

    html_out = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>KOIPA AI — OSS SBOM 보고서 | Koipa AI</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <h1>OSS SBOM 보고서</h1>
  <div class="meta">
    <span>format <strong>{html.escape(bom_format)} {html.escape(spec_version)}</strong></span>
    <span>components <strong>{total}</strong></span>
    <span>generated <strong>{html.escape(ts)}</strong></span>
    <span>출처 <strong>{html.escape(str(in_path.name))}</strong></span>
  </div>
</header>

<section>
  <h2>§0 위험 등급 분포</h2>
  <div class="widgets">
    <div class="widget perm"><div class="lbl">Permissive (MIT·BSD·Apache)</div><div class="val">{n_perm}</div></div>
    <div class="widget weak"><div class="lbl">Weak Copyleft (LGPL·MPL)</div><div class="val">{n_weak}</div></div>
    <div class="widget strong"><div class="lbl">Strong Copyleft (AGPL·GPL)</div><div class="val">{n_strong}</div></div>
    <div class="widget unknown"><div class="lbl">Unknown</div><div class="val">{n_unknown}</div></div>
  </div>
</section>

<section>
  <h2>§1 라이선스 분포 (Top 15)</h2>
  {''.join(bars)}
</section>

{'<section><h2>§2 위험 컴포넌트 (Weak/Strong Copyleft)</h2><table><thead><tr><th>이름</th><th>버전</th><th>라이선스</th><th>등급</th></tr></thead><tbody>' + risk_table_rows + '</tbody></table></section>' if risk_rows else '<section><h2>§2 위험 컴포넌트</h2><p style="color:var(--c-perm);">✓ 위험 컴포넌트 없음 — 전부 Permissive 또는 Unknown.</p></section>'}

<section>
  <h2>§3 전체 컴포넌트 ({total})</h2>
  <table><thead><tr><th>이름</th><th>버전</th><th>라이선스</th><th>등급</th></tr></thead>
  <tbody>{all_table_rows}</tbody></table>
</section>

<div class="foot">
  생성: scripts/render_sbom_html.py · 출처: CycloneDX {spec_version} · 외부 의존 0 (CSS 인라인) · 폐쇄망 호환<br/>
  정책: AGPL/GPL-3 = Strong Copyleft, LGPL/MPL = Weak, MIT/BSD/Apache = Permissive (doc/14 §3 정합)
</div>

</div>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_out, encoding="utf-8")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    ap.add_argument("--out", dest="out_path", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    in_p = Path(args.in_path)
    if not in_p.exists():
        print(f"  ERROR: SBOM 입력 파일 부재: {in_p}")
        print(f"  먼저 실행: make licenses 또는 python scripts/dump_licenses.py --format=cyclonedx -o {in_p}")
        return 1

    out = render(in_p, Path(args.out_path))
    print(f"  SBOM HTML → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
