"""Render a pilot scorecard HTML from acceptance/readiness JSON artifacts."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {"_missing": True, "_path": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _pct(num: int, den: int) -> str:
    return "0.0%" if den <= 0 else f"{num / den * 100:.1f}%"


def _acceptance_cards(acc: dict) -> str:
    n = int(acc.get("n") or 0)
    items = [
        ("Verdict", acc.get("verdict", "MISSING")),
        ("Documents", n),
        ("Veto", f"{acc.get('veto_count', 0)} ({_pct(int(acc.get('veto_count') or 0), n)})"),
        ("Parse Fail", f"{acc.get('parse_fail', 0)} ({_pct(int(acc.get('parse_fail') or 0), n)})"),
        ("Under Classified", acc.get("under_classified", 0)),
        ("Needs Review", acc.get("needs_review", 0)),
    ]
    return "".join(
        f"<section class='metric'><span>{_esc(k)}</span><strong>{_esc(v)}</strong></section>"
        for k, v in items
    )


def _format_rows(acc: dict) -> str:
    stats = acc.get("parser_by_format") or {}
    if not stats:
        return "<tr><td colspan='5'>No parser format data</td></tr>"
    rows = []
    for fmt, s in sorted(stats.items()):
        total = int(s.get("total") or 0)
        rows.append(
            "<tr>"
            f"<td>{_esc(fmt)}</td>"
            f"<td>{total}</td>"
            f"<td>{_esc(s.get('parse_fail', 0))}</td>"
            f"<td>{_esc(s.get('needs_review', 0))}</td>"
            f"<td>{_esc(s.get('veto', 0))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _readiness_rows(readiness: dict) -> str:
    gates = readiness.get("gates") or []
    if not gates:
        return "<tr><td colspan='3'>No readiness gates</td></tr>"
    return "".join(
        "<tr>"
        f"<td>{_esc(g.get('name'))}</td>"
        f"<td><span class='pill {str(g.get('status', '')).lower()}'>{_esc(g.get('status'))}</span></td>"
        f"<td>{_esc(g.get('detail'))}</td>"
        "</tr>"
        for g in gates
    )


def _result_rows(acc: dict) -> str:
    results = acc.get("results") or []
    if not results:
        return "<tr><td colspan='8'>No detailed acceptance results</td></tr>"
    rows = []
    for r in results:
        rows.append(
            "<tr>"
            f"<td>{_esc(r.get('doc_id'))}</td>"
            f"<td>{_esc(r.get('format'))}</td>"
            f"<td>{_esc(r.get('expected'))}</td>"
            f"<td>{_esc(r.get('pred'))}</td>"
            f"<td>{'OK' if r.get('parse_ok') else 'FAIL'}</td>"
            f"<td>{_esc(r.get('numeric_recall'))}</td>"
            f"<td>{_esc(r.get('status'))}</td>"
            f"<td>{_esc(', '.join(r.get('veto_reasons') or []))}</td>"
            "</tr>"
        )
    return "".join(rows)


def render(acceptance: dict, readiness: dict) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    verdict = acceptance.get("verdict", "MISSING")
    readiness_verdict = readiness.get("verdict", "MISSING")
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Lloydk Pilot Scorecard</title>
<style>
:root{{--bg:#f7f8fb;--panel:#fff;--line:#d8dde8;--text:#172033;--muted:#697386;--ok:#137333;--bad:#b3261e;--warn:#a15c00;}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Arial,'Malgun Gothic',sans-serif;}}
main{{max-width:1180px;margin:0 auto;padding:32px 24px 48px;}}
h1{{font-size:28px;margin:0 0 4px;}} h2{{font-size:18px;margin:28px 0 10px;}}
.sub{{color:var(--muted);margin-bottom:22px;}}
.metrics{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;}}
.metric{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;}}
.metric span{{display:block;color:var(--muted);font-size:12px;}} .metric strong{{font-size:20px;}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);}}
th,td{{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;font-size:13px;vertical-align:top;}}
th{{background:#edf1f7;}} .pill{{display:inline-block;padding:2px 8px;border-radius:999px;font-weight:700;font-size:12px;}}
.pass,.ok{{color:var(--ok);background:#e6f4ea;}} .fail{{color:var(--bad);background:#fce8e6;}}
.blocked,.conditionally_ready{{color:var(--warn);background:#fff4df;}}
@media(max-width:900px){{.metrics{{grid-template-columns:repeat(2,minmax(0,1fr));}}}}
</style>
</head>
<body><main>
<h1>파일럿 스코어카드</h1>
<div class="sub">Generated {generated} · Acceptance: <b>{_esc(verdict)}</b> · Readiness: <b>{_esc(readiness_verdict)}</b></div>
<div class="metrics">{_acceptance_cards(acceptance)}</div>
<h2>파서 포맷별 결과</h2>
<table><thead><tr><th>Format</th><th>Total</th><th>Parse Fail</th><th>Needs Review</th><th>Veto</th></tr></thead><tbody>{_format_rows(acceptance)}</tbody></table>
<h2>운영 Readiness Gates</h2>
<table><thead><tr><th>Gate</th><th>Status</th><th>Detail</th></tr></thead><tbody>{_readiness_rows(readiness)}</tbody></table>
<h2>문서별 Acceptance 상세</h2>
<table><thead><tr><th>Doc</th><th>Fmt</th><th>Expected</th><th>Pred</th><th>Parse</th><th>Numeric</th><th>Status</th><th>Veto</th></tr></thead><tbody>{_result_rows(acceptance)}</tbody></table>
</main></body></html>
"""


def _pilot_ok(acceptance: dict, readiness: dict) -> bool:
    if acceptance.get("_missing") or readiness.get("_missing"):
        return False
    return acceptance.get("verdict") == "PASS" and readiness.get("verdict") in {
        "PASS",
        "CONDITIONALLY_READY",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--acceptance", default="reports/acceptance_report.json")
    ap.add_argument("--readiness", default="reports/operational_readiness.json")
    ap.add_argument("--out", default="reports/pilot_scorecard.html")
    ap.add_argument(
        "--fail-on-bad",
        action="store_true",
        help="return non-zero after rendering when acceptance/readiness is not pilot-ok",
    )
    args = ap.parse_args()
    acceptance = _load_json(Path(args.acceptance))
    readiness = _load_json(Path(args.readiness))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(acceptance, readiness), encoding="utf-8")
    print(out)
    if args.fail_on_bad and not _pilot_ok(acceptance, readiness):
        print("[pilot-scorecard] FAIL: acceptance/readiness is not pilot-ok")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
