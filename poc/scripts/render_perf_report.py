"""HTML 렌더러 — doc/20_시나리오_성능_보고서.html 단일 파일 산출.

- 외부 의존 0 (CSS·JS 인라인, SVG 자체 sparkline)
- 폐쇄망 호환 (CDN·웹폰트 사용 안 함, 시스템 폰트 폴백)
- doc/17 NovaX 톤 재활용
- mode=external 시 env·raw measurements 톤다운
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from koipa.perf.kpis import core_kpis  # type: ignore
from koipa.perf.recorder import load_history  # type: ignore
from koipa.perf.regression import (  # type: ignore
    detect_regression_trend,
    summarize_skip_reasons,
)


# ----------------------------------------------------------------
# CSS — NovaX 톤다운, 시스템 폰트
# ----------------------------------------------------------------

CSS = """
:root {
  --bg:#ffffff; --bg-surface:#fafafa; --bg-elevated:#ffffff;
  --text:#0a0a0a; --text-soft:#525252; --text-dim:#737373; --text-faint:#a3a3a3;
  --border:rgba(0,0,0,0.08); --border-strong:rgba(0,0,0,0.16); --border-soft:rgba(0,0,0,0.05);
  --accent:#0a0a0a; --accent-soft:#f4f4f4;
  --c-pass:#16a34a; --c-fail:#dc2626; --c-skip:#737373; --c-warn:#d97706;
  --radius:8px; --radius-sm:6px; --radius-pill:999px;
  --font-sans:-apple-system,BlinkMacSystemFont,"Pretendard","Apple SD Gothic Neo","Noto Sans KR",sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
* {box-sizing:border-box;}
html {scroll-behavior:smooth;}
body {
  margin:0; background:var(--bg); color:var(--text);
  font-family:var(--font-sans); font-size:14.5px; line-height:1.65; letter-spacing:-0.011em;
  -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale;
}
.wrap {max-width:1280px; margin:0 auto; padding:24px 32px 96px;}
header.top {
  padding:24px 0 16px; margin-bottom:24px;
  border-bottom:1px solid var(--border-strong);
}
header.top h1 {margin:0 0 8px; font-size:28px; font-weight:700; letter-spacing:-0.02em;}
header.top .meta {color:var(--text-soft); font-size:13px; font-family:var(--font-mono);}
header.top .meta span {display:inline-block; margin-right:16px;}
.badge {
  display:inline-flex; align-items:center; gap:4px;
  padding:2px 8px; border:1px solid var(--border-strong); border-radius:var(--radius-pill);
  font-size:11.5px; font-family:var(--font-mono); color:var(--text);
  background:var(--bg-surface);
}
.badge.pass {color:var(--c-pass); border-color:var(--c-pass);}
.badge.fail {color:var(--c-fail); border-color:var(--c-fail);}
.badge.skip {color:var(--c-skip); border-color:var(--border-strong);}
.badge.warn {color:var(--c-warn); border-color:var(--c-warn);}

section {margin:32px 0;}
section h2 {
  font-size:18px; font-weight:600; margin:0 0 12px;
  padding-bottom:8px; border-bottom:1px solid var(--border);
  display:flex; align-items:center; gap:8px;
}
section h2 .num {color:var(--text-faint); font-family:var(--font-mono); font-weight:500;}

/* widgets */
.widgets {display:grid; grid-template-columns:repeat(5,1fr); gap:12px;}
@media (max-width:960px) {.widgets {grid-template-columns:repeat(2,1fr);}}
.widget {
  border:1px solid var(--border-strong); border-radius:var(--radius); padding:14px 16px;
  background:var(--bg-elevated);
}
.widget .lbl {font-size:11.5px; color:var(--text-dim); font-family:var(--font-mono); margin-bottom:6px;}
.widget .val {font-size:24px; font-weight:700; letter-spacing:-0.02em; line-height:1.1;}
.widget .sub {font-size:11.5px; color:var(--text-soft); font-family:var(--font-mono); margin-top:4px;}
.widget.pass .val {color:var(--c-pass);}
.widget.fail .val {color:var(--c-fail);}
.widget.skip .val {color:var(--c-skip);}

/* matrix table */
table {width:100%; border-collapse:collapse; font-size:13px;}
table th, table td {
  text-align:left; padding:8px 10px; border-bottom:1px solid var(--border);
  vertical-align:top;
}
table th {
  background:var(--bg-surface); font-weight:600; color:var(--text-soft);
  font-size:12px; text-transform:uppercase; letter-spacing:0.04em;
  border-bottom:1px solid var(--border-strong);
  position:sticky; top:0;
}
table tr:hover td {background:var(--bg-surface);}
.cell-mono {font-family:var(--font-mono); font-size:12.5px;}
.status-pill {
  display:inline-block; padding:1px 8px; border-radius:var(--radius-pill);
  font-size:11px; font-family:var(--font-mono); font-weight:600;
  border:1px solid currentColor;
}
.status-pill.PASS {color:var(--c-pass);}
.status-pill.FAIL {color:var(--c-fail);}
.status-pill.SKIP {color:var(--c-skip);}
.status-pill.ERROR {color:var(--c-fail); background:rgba(220,38,38,0.05);}
.delta {font-family:var(--font-mono); font-size:11.5px;}
.delta.up {color:var(--c-fail);}
.delta.down {color:var(--c-pass);}
.delta.flat {color:var(--text-faint);}

details.scenario {
  border:1px solid var(--border); border-radius:var(--radius);
  margin:8px 0; background:var(--bg-elevated);
}
details.scenario > summary {
  cursor:pointer; padding:12px 16px; list-style:none;
  display:flex; align-items:center; gap:12px;
  font-weight:500;
}
details.scenario > summary::-webkit-details-marker {display:none;}
details.scenario > summary::after {
  content:"▸"; color:var(--text-faint); margin-left:auto;
  transition:transform .15s ease;
}
details.scenario[open] > summary::after {transform:rotate(90deg);}
details.scenario .body {padding:4px 16px 14px; border-top:1px solid var(--border-soft);}

.spark {display:inline-block; vertical-align:middle;}
.kv {
  display:grid; grid-template-columns:max-content 1fr; gap:4px 14px;
  font-size:12.5px; font-family:var(--font-mono);
}
.kv dt {color:var(--text-dim);}
.kv dd {margin:0;}
pre.cmd {
  background:var(--bg-surface); border:1px solid var(--border); border-radius:var(--radius-sm);
  padding:10px 12px; font-family:var(--font-mono); font-size:12.5px; overflow-x:auto;
  margin:6px 0;
}
.foot {color:var(--text-faint); font-size:11.5px; margin-top:48px; padding-top:16px; border-top:1px solid var(--border);}
"""


# ----------------------------------------------------------------
# 인라인 SVG sparkline
# ----------------------------------------------------------------

def sparkline_svg(values: list[float], *, width: int = 80, height: int = 22, stroke: str = "#0a0a0a") -> str:
    if not values:
        return ""
    if len(values) == 1:
        values = values * 2
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(values):
        x = i * (width - 2) / (len(values) - 1) + 1
        y = height - 1 - ((v - lo) / rng) * (height - 2)
        pts.append(f"{x:.1f},{y:.1f}")
    path = "M " + " L ".join(pts)
    last_x, last_y = pts[-1].split(",")
    return (
        f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<path d="{path}" fill="none" stroke="{stroke}" stroke-width="1.2"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="1.8" fill="{stroke}"/>'
        f"</svg>"
    )


def _value_passes(v: float, compare: str, threshold: float | None) -> bool:
    if threshold is None:
        return True
    if compare == "le":
        return v <= threshold
    if compare == "ge":
        return v >= threshold
    if compare == "gt":
        return v > threshold
    if compare == "lt":
        return v < threshold
    return True


def sparkline_with_threshold(
    values: list[float],
    *,
    threshold: float | bool | None = None,
    compare: str = "le",
    width: int = 160,
    height: int = 48,
) -> str:
    """임계선이 표시된 sparkline.

    - 측정값(검은 선) + 점별 PASS/FAIL 색(초록/빨강) + 임계선(점선, 주황) 한 차트에
    - 임계 미달 회차를 즉시 식별 가능
    - 임계가 None이면 일반 sparkline과 동일 동작
    """
    if not values:
        return ""
    if len(values) == 1:
        values = values * 2

    if isinstance(threshold, bool):
        threshold = 1.0 if threshold else 0.0

    candidates = list(values)
    if threshold is not None:
        candidates.append(float(threshold))
    lo, hi = min(candidates), max(candidates)
    span = (hi - lo) or 1.0
    lo -= span * 0.05
    hi += span * 0.05
    rng = (hi - lo) or 1.0

    def _y(v: float) -> float:
        return height - 1 - ((v - lo) / rng) * (height - 2)

    pts = []
    for i, v in enumerate(values):
        x = i * (width - 2) / (len(values) - 1) + 1
        pts.append((x, _y(v), v))

    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
    circles = []
    for x, y, v in pts:
        ok = _value_passes(v, compare, threshold) if threshold is not None else True
        color = "#16a34a" if ok else "#dc2626"
        circles.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{color}"/>')

    thr_line = ""
    if threshold is not None:
        ty = _y(float(threshold))
        thr_line = (
            f'<line x1="0" x2="{width}" y1="{ty:.1f}" y2="{ty:.1f}" '
            f'stroke="#d97706" stroke-width="1" stroke-dasharray="3,3"/>'
        )

    return (
        f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f"{thr_line}"
        f'<path d="{path_d}" fill="none" stroke="#525252" stroke-width="1.2"/>'
        f"{''.join(circles)}"
        f"</svg>"
    )


# ----------------------------------------------------------------
# 보조 함수
# ----------------------------------------------------------------

def _fmt_value(v: float, unit: str) -> str:
    if unit == "bool":
        return "True" if v >= 1.0 else "False"
    if unit == "ratio":
        return f"{v*100:.1f}%"
    if unit == "ms":
        return f"{v:.0f}ms"
    if unit in ("docs/s", "chunks/s"):
        return f"{v:.1f}"
    if unit == "USD":
        return f"${v:.4f}"
    if unit == "count":
        return f"{int(v)}"
    return f"{v:.3f}"


def _fmt_threshold(t, compare: str, unit: str) -> str:
    if unit == "bool":
        return "True"
    if isinstance(t, bool):
        t = 1.0 if t else 0.0
    sym = {"le": "≤", "ge": "≥", "eq": "=", "gt": ">", "lt": "<"}.get(compare, compare)
    return f"{sym} {_fmt_value(float(t), unit)}"


def _delta(curr: float, prev: float | None, unit: str) -> str:
    if prev is None:
        return '<span class="delta flat">—</span>'
    diff = curr - prev
    if abs(diff) < 1e-9:
        return '<span class="delta flat">0</span>'
    arrow = "▲" if diff > 0 else "▼"
    cls = "up" if diff > 0 else "down"
    # 정확도/recall은 위로 가는 게 좋고 latency는 아래로 가는 게 좋음 — 단순 표시만, 색 해석은 합격선 미달 여부로
    return f'<span class="delta {cls}">{arrow} {_fmt_value(abs(diff), unit)}</span>'


# ----------------------------------------------------------------
# 메인 렌더
# ----------------------------------------------------------------

def render_html(report: dict[str, Any], *, out_path: Path, history_dir: Path, mode: str) -> Path:
    history = load_history(history_dir, mode=mode, limit=5)
    # history[0]이 현재 보고서일 가능성 — 분리
    prev_runs = [h for h in history if h.get("ts") != report.get("ts")][:4]

    env = report.get("env", {}) or {}
    summ = report.get("summary", {}) or {}
    scenarios = report.get("scenarios", []) or []

    # core KPI 위젯 5개 (PASS 비율 + 4개 핵심 KPI)
    core_widgets = _build_core_widgets(scenarios, summ)

    # 시나리오 표
    rows = _build_matrix_rows(scenarios, prev_runs)

    # 추세 (핵심 KPI 4종 + 전체 KPI 표)
    full_history_chrono = list(reversed(prev_runs)) + [report]
    trend_charts = _build_trends(full_history_chrono)
    trend_full = _build_full_kpi_trends(full_history_chrono)

    # F1: 회귀 시계열 트렌드 (최근 5회 인접 비교 누적)
    skip_block = _build_skip_classification(report)

    # F4: SKIP 사유별 분류 카드
    history_for_trend = [report] + prev_runs  # 최신순
    regression_trend_block = _build_regression_trend(history_for_trend)

    # 미달 KPI
    misses = _build_misses(scenarios)

    # 환경
    env_block = _build_env_block(env)

    html_out = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>KOIPA AI 시나리오 성능 보고서 — Koipa AI</title>
<meta name="generator" content="koipa.perf PSH v1.0" />
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <h1>시나리오 성능 보고서</h1>
  <div class="meta">
    <span>mode <strong>{html.escape(mode)}</strong></span>
    <span>git <strong>{html.escape(env.get("git_sha", "?"))}</strong></span>
    <span>branch <strong>{html.escape(env.get("git_branch", "?"))}</strong></span>
    <span>ts <strong>{html.escape(report.get("ts", "?"))}</strong></span>
    <span>duration <strong>{report.get("duration_sec", 0):.2f}s</strong></span>
  </div>
</header>

<section id="summary">
  <h2><span class="num">§0</span> 요약</h2>
  <div class="widgets">{core_widgets}</div>
</section>

<section id="matrix">
  <h2><span class="num">§1</span> 시나리오 × KPI 매트릭스</h2>
  <table>
    <thead>
      <tr>
        <th style="width:6%">KPI</th>
        <th style="width:8%">시나리오</th>
        <th>이름</th>
        <th style="width:10%">측정</th>
        <th style="width:10%">합격선</th>
        <th style="width:8%">판정</th>
        <th style="width:8%">N</th>
        <th style="width:12%">직전 대비</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</section>

<section id="trends">
  <h2><span class="num">§2</span> 핵심 KPI 추세 (최근 {len(prev_runs)+1}회)</h2>
  {trend_charts}
</section>

<section id="trends-full">
  <h2><span class="num">§2.5</span> 전체 KPI 추세</h2>
  <p style="color:var(--text-dim); font-size:12.5px; margin:0 0 8px;">
    각 KPI의 최근 {len(prev_runs)+1}회 측정 추이. 점선=임계, 초록 점=PASS, 빨강 점=FAIL.
    SKIP된 KPI는 측정값 0으로 표시되니 컬러 무시.
  </p>
  {trend_full}
</section>

<section id="skip-classification">
  <h2><span class="num">§2.6</span> SKIP 사유별 분류</h2>
  {skip_block}
</section>

<section id="regression-trend">
  <h2><span class="num">§2.7</span> 회귀 시계열 트렌드 (최근 {len(history_for_trend)}회)</h2>
  {regression_trend_block}
</section>

<section id="misses">
  <h2><span class="num">§3</span> 미달 KPI · 원인 가설</h2>
  {misses}
</section>

<section id="env">
  <h2><span class="num">§4</span> 환경 캡처</h2>
  {env_block}
</section>

<section id="repro">
  <h2><span class="num">§5</span> 재현 명령</h2>
  <pre class="cmd">make perf-scenarios                # dryrun
make perf-scenarios MODE=full      # 실측 (LLM·GPU·ES·PG 필요)
make perf-scenarios FAIL_ON_MISS=1 # KPI 미달 시 exit 1 (CI용)</pre>
  <p style="color:var(--text-dim); font-size:12.5px;">
    상세 명세: <code>doc/20a_시나리오_성능_KPI매트릭스.md</code> · 시나리오 명세: <code>doc/19_KL_통합_8시나리오.md</code>
  </p>
</section>

<div class="foot">
  생성: koipa.perf PSH v1.0 · 외부 의존 0 (CSS·JS 인라인) · 폐쇄망 호환
</div>

</div>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_out, encoding="utf-8")
    return out_path


def _build_core_widgets(scenarios: list[dict], summ: dict) -> str:
    total = summ.get("total_kpis", 0)
    n_pass = summ.get("pass", 0)
    pass_rate = (n_pass / total * 100) if total else 0
    pass_class = "pass" if pass_rate >= 90 else ("warn" if pass_rate >= 75 else "fail")

    widgets: list[str] = []
    widgets.append(
        f'<div class="widget {pass_class}">'
        f'<div class="lbl">PASS 비율</div>'
        f'<div class="val">{n_pass} / {total}</div>'
        f'<div class="sub">{pass_rate:.1f}%</div></div>'
    )

    # core KPI 4개
    kpi_by_id: dict[str, dict] = {}
    for s in scenarios:
        for k in s.get("kpis", []):
            kpi_by_id[k.get("kpi_id")] = k

    for ck in core_kpis():
        k = kpi_by_id.get(ck.id)
        if not k:
            widgets.append(
                f'<div class="widget skip"><div class="lbl">{html.escape(ck.id)} {html.escape(ck.name)}</div>'
                f'<div class="val">—</div><div class="sub">N/A</div></div>'
            )
            continue
        status = k.get("status", "SKIP")
        cls = {"PASS": "pass", "FAIL": "fail", "SKIP": "skip"}.get(status, "skip")
        widgets.append(
            f'<div class="widget {cls}">'
            f'<div class="lbl">{html.escape(ck.id)} {html.escape(ck.name)}</div>'
            f'<div class="val">{_fmt_value(float(k.get("measured", 0)), ck.unit)}</div>'
            f'<div class="sub">{_fmt_threshold(k.get("threshold"), k.get("compare", "le"), ck.unit)} · {status}</div>'
            f'</div>'
        )
    return "".join(widgets)


def _build_matrix_rows(scenarios: list[dict], prev_runs: list[dict]) -> str:
    # 직전 회차 KPI 인덱싱
    prev_map: dict[str, float] = {}
    if prev_runs:
        prev = prev_runs[0]
        for s in prev.get("scenarios", []):
            for k in s.get("kpis", []):
                prev_map[k.get("kpi_id", "")] = float(k.get("measured", 0))

    rows: list[str] = []
    for s in scenarios:
        title = f"{s.get('scenario')} {s.get('title', '')}"
        for k in s.get("kpis", []):
            kid = k.get("kpi_id", "")
            measured = float(k.get("measured", 0))
            prev = prev_map.get(kid)
            rows.append(
                f"<tr>"
                f'<td class="cell-mono">{html.escape(kid)}</td>'
                f"<td>{html.escape(title)}</td>"
                f"<td>{html.escape(k.get('name', ''))}</td>"
                f'<td class="cell-mono">{_fmt_value(measured, k.get("unit", ""))}</td>'
                f'<td class="cell-mono">{_fmt_threshold(k.get("threshold"), k.get("compare", "le"), k.get("unit", ""))}</td>'
                f'<td><span class="status-pill {k.get("status", "SKIP")}">{k.get("status", "SKIP")}</span></td>'
                f'<td class="cell-mono">{k.get("n_samples", 0)}</td>'
                f"<td>{_delta(measured, prev, k.get('unit', ''))}</td>"
                f"</tr>"
            )
    return "".join(rows)


def _build_trends(history: list[dict]) -> str:
    """핵심 KPI 4종의 추세 sparkline + 임계선."""
    if len(history) <= 1:
        return '<p style="color:var(--text-dim); font-size:13px;">측정 누적 중 (N=1) — 다음 회차부터 추세 표시.</p>'

    # KPI별 측정값 + 임계·compare 추출 (가장 최근 run에서)
    series: dict[str, list[float]] = {ck.id: [] for ck in core_kpis()}
    latest_kpi_meta: dict[str, dict] = {}
    for run in history:
        idx = {}
        for s in run.get("scenarios", []):
            for k in s.get("kpis", []):
                kid = k.get("kpi_id", "")
                idx[kid] = float(k.get("measured", 0))
                # latest meta (마지막 처리가 가장 최근)
                latest_kpi_meta[kid] = k
        for ck in core_kpis():
            series[ck.id].append(idx.get(ck.id, 0.0))

    cards: list[str] = []
    for ck in core_kpis():
        vals = series[ck.id]
        meta = latest_kpi_meta.get(ck.id, {})
        threshold = meta.get("threshold")
        compare = meta.get("compare", ck.compare if hasattr(ck, "compare") else "le")
        chart = sparkline_with_threshold(
            vals, threshold=threshold, compare=compare, width=180, height=44,
        )
        # 최근 값이 임계 미달인지로 카드 컬러
        latest_ok = _value_passes(vals[-1], compare, threshold) if threshold is not None else True
        cls = "pass" if latest_ok else "fail"
        cards.append(
            f'<div class="widget {cls}">'
            f'<div class="lbl">{html.escape(ck.id)} {html.escape(ck.name)}</div>'
            f'<div class="val" style="font-size:14px;">{chart}</div>'
            f'<div class="sub">{_fmt_value(vals[-1], ck.unit)} · '
            f'{_fmt_threshold(threshold, compare, ck.unit) if threshold is not None else "—"} · N={len(vals)}</div>'
            f'</div>'
        )
    return f'<div class="widgets">{"".join(cards)}</div>'


def _build_full_kpi_trends(history: list[dict]) -> str:
    """전체 KPI의 추세 — 카테고리(시나리오)별 그룹화 표."""
    if len(history) <= 1:
        return ""

    # KPI별 측정값 시계열 + 최근 메타
    series: dict[str, list[float]] = {}
    latest_meta: dict[str, dict] = {}
    scenario_of: dict[str, str] = {}
    for run in history:
        run_kpis: dict[str, dict] = {}
        for s in run.get("scenarios", []):
            for k in s.get("kpis", []):
                kid = k.get("kpi_id", "")
                if not kid:
                    continue
                run_kpis[kid] = k
                latest_meta[kid] = k
                scenario_of[kid] = s.get("scenario", "")
        # 모든 알려진 KPI에 대해 측정값 (없으면 0) 추가
        all_ids = set(series.keys()) | set(run_kpis.keys())
        for kid in all_ids:
            if kid not in series:
                series[kid] = []
            v = run_kpis.get(kid, {}).get("measured", 0)
            series[kid].append(float(v))

    # 시나리오별 그룹 정렬
    grouped: dict[str, list[str]] = {}
    for kid in sorted(series.keys()):
        sc = scenario_of.get(kid, "?")
        grouped.setdefault(sc, []).append(kid)

    rows: list[str] = []
    for sc in sorted(grouped.keys()):
        for kid in grouped[sc]:
            meta = latest_meta.get(kid, {})
            vals = series[kid]
            threshold = meta.get("threshold")
            compare = meta.get("compare", "le")
            unit = meta.get("unit", "")
            chart = sparkline_with_threshold(
                vals, threshold=threshold, compare=compare, width=140, height=32,
            )
            status = meta.get("status", "SKIP")
            rows.append(
                f"<tr>"
                f'<td class="cell-mono">{html.escape(kid)}</td>'
                f"<td>{html.escape(meta.get('name', ''))}</td>"
                f'<td>{chart}</td>'
                f'<td class="cell-mono">{_fmt_value(vals[-1], unit)}</td>'
                f'<td><span class="status-pill {status}">{status}</span></td>'
                f"</tr>"
            )
    return (
        f'<table style="margin-top:12px;"><thead><tr>'
        f'<th style="width:8%">KPI</th>'
        f'<th style="width:30%">이름</th>'
        f'<th style="width:32%">추이 (임계선=주황, PASS=초록, FAIL=빨강)</th>'
        f'<th style="width:14%">최근값</th>'
        f'<th style="width:10%">최근 판정</th>'
        f"</tr></thead><tbody>"
        f"{''.join(rows)}"
        f"</tbody></table>"
    )


def _build_skip_classification(report: dict) -> str:
    """F4 — SKIP 사유별·시나리오별 분류 카드."""
    summ = summarize_skip_reasons(report)
    total = summ["total_skip"]
    if total == 0:
        return '<p style="color:var(--c-pass); font-size:13.5px;">✓ SKIP 0건.</p>'

    reason_labels = {
        "trained_model": ("학습 모델 미존재", "GPU·학습 사이클 후 자동 활성"),
        "no_measurement": ("측정값 없음", "시나리오 함수가 해당 회차 record 생략"),
        "pg": ("PostgreSQL 미가용", "docker compose up postgres"),
        "es": ("Elasticsearch 미가용", "docker compose up es"),
        "redis": ("Redis 미가용", "docker compose up redis"),
        "minio": ("MinIO 미가용", "docker compose up minio"),
        "llm": ("LLM 미설정", "LLM_PROVIDER + API 키 설정"),
        "gpu": ("GPU 미가용", "발주처 GPU 확보 후 활성"),
        "no_reason": ("사유 미기재", "시나리오 함수 점검 필요"),
        "other": ("기타", "skip_reason 확인"),
    }
    cards = []
    for cat, n in sorted(summ["by_reason"].items(), key=lambda x: -x[1]):
        label, advice = reason_labels.get(cat, (cat, ""))
        cards.append(
            f'<div class="card">'
            f'<div class="num" style="font-size:28px; font-weight:700; color:var(--c-skip);">{n}</div>'
            f'<div style="font-size:13px; font-weight:600; margin-top:4px;">{html.escape(label)}</div>'
            f'<div style="font-size:11.5px; color:var(--text-dim); margin-top:2px;">{html.escape(advice)}</div>'
            f"</div>"
        )
    cards_html = (
        '<div class="widgets" style="grid-template-columns:repeat(auto-fit, minmax(160px,1fr));">'
        + "".join(cards)
        + "</div>"
    )
    scen_rows = "".join(
        f"<tr><td>{html.escape(sid)}</td><td>{n}</td></tr>"
        for sid, n in sorted(summ["by_scenario"].items(), key=lambda x: -x[1])
    )
    scen_table = (
        f'<table style="margin-top:12px; max-width:480px;">'
        f"<thead><tr><th>시나리오</th><th>SKIP 수</th></tr></thead>"
        f"<tbody>{scen_rows}</tbody></table>"
    )
    return (
        f'<p style="font-size:13.5px; margin:0 0 12px;">총 <strong>{total}</strong>건의 SKIP — 사유별 분류:</p>'
        + cards_html
        + scen_table
    )


def _build_regression_trend(history: list[dict]) -> str:
    """F1 — 최근 N회차 회귀 시계열 트렌드."""
    if len(history) < 2:
        return (
            '<p style="color:var(--text-dim); font-size:13px;">'
            f"측정 누적 중 (N={len(history)}). 2회차 이상부터 트렌드 분석 활성."
            "</p>"
        )
    trends = detect_regression_trend(history, threshold_pct=30.0)
    if not trends:
        return (
            '<p style="color:var(--c-pass); font-size:13.5px;">'
            f"✓ 최근 {len(history)}회차 시계열에 회귀(≥30%) 없음."
            "</p>"
        )
    trend_color = {
        "degrading": "var(--c-fail)",
        "noisy": "var(--c-warn)",
        "flat": "var(--text-dim)",
        "improving": "var(--c-pass)",
    }
    rows = []
    for t in trends[:15]:
        col = trend_color.get(t["trend"], "var(--text-dim)")
        latest = t.get("latest_delta_pct")
        latest_str = f"{latest:+.1f}%" if latest is not None else "—"
        rows.append(
            f"<tr>"
            f"<td><code>{html.escape(t['kpi_id'])}</code></td>"
            f"<td>{html.escape(t['name'])}</td>"
            f'<td style="text-align:center;">{t["n_regressions"]}</td>'
            f'<td style="text-align:center;">{t["consecutive"]}</td>'
            f'<td style="text-align:right; font-family:var(--font-mono);">{latest_str}</td>'
            f'<td><span style="color:{col}; font-weight:600;">{html.escape(t["trend"])}</span></td>'
            f"</tr>"
        )
    return (
        f'<p style="font-size:13px; color:var(--text-dim); margin:0 0 8px;">'
        f"최근 {len(history)}회차 인접 비교(임계 30%) 누적. consecutive≥2는 즉시 점검 필요."
        "</p>"
        f'<table><thead><tr>'
        f'<th style="width:8%">KPI</th>'
        f'<th>이름</th>'
        f'<th style="width:10%; text-align:center;">회귀 횟수</th>'
        f'<th style="width:10%; text-align:center;">연속</th>'
        f'<th style="width:12%; text-align:right;">최근 Δ%</th>'
        f'<th style="width:14%">트렌드</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _build_misses(scenarios: list[dict]) -> str:
    misses = []
    for s in scenarios:
        for k in s.get("kpis", []):
            if k.get("status") == "FAIL":
                misses.append((s.get("scenario"), k))
    if not misses:
        return '<p style="color:var(--c-pass); font-size:13.5px;">✓ 모든 KPI가 합격선 통과 또는 SKIP(외부 자원 의존).</p>'
    items = []
    for sc_id, k in misses:
        items.append(
            f'<details class="scenario" open>'
            f'<summary><span class="status-pill FAIL">FAIL</span>'
            f' <span class="cell-mono">{html.escape(k.get("kpi_id"))}</span>'
            f' {html.escape(k.get("name"))} ({html.escape(sc_id)})</summary>'
            f'<div class="body">'
            f'<dl class="kv">'
            f'<dt>측정</dt><dd>{_fmt_value(float(k.get("measured",0)), k.get("unit",""))}</dd>'
            f'<dt>합격선</dt><dd>{_fmt_threshold(k.get("threshold"), k.get("compare","le"), k.get("unit",""))}</dd>'
            f'<dt>표본수</dt><dd>{k.get("n_samples",0)}</dd>'
            f'</dl>'
            f'<p style="color:var(--text-soft); font-size:12.5px; margin-top:8px;">'
            f'원인 가설: 표본 부족 / 외부 자원 미가용 / 코드 회귀 — full-mode 재실행 또는 직전 commit과 비교 검토 필요.'
            f'</p>'
            f'</div></details>'
        )
    return "".join(items)


def _build_env_block(env: dict) -> str:
    svc = env.get("services", {}) or {}
    pyt = env.get("pytest_collected")
    pyt_str = str(pyt) if pyt is not None else "—"

    def _svc_badge(name: str, status: str) -> str:
        cls = {"UP": "pass", "DOWN": "fail"}.get(status, "skip")
        return f'<span class="badge {cls}">{html.escape(name)}: {html.escape(status)}</span>'

    return (
        f'<dl class="kv">'
        f'<dt>Python</dt><dd>{html.escape(env.get("python","?"))}</dd>'
        f'<dt>Platform</dt><dd>{html.escape(env.get("platform","?"))}</dd>'
        f'<dt>CPU</dt><dd>{env.get("cpu_count","?")} cores</dd>'
        f'<dt>RAM</dt><dd>{env.get("ram_gb","?")} GB</dd>'
        f'<dt>GPU</dt><dd>{html.escape(env.get("gpu","N/A"))}</dd>'
        f'<dt>git</dt><dd>{html.escape(env.get("git_sha","?"))} ({html.escape(env.get("git_branch","?"))})</dd>'
        f'<dt>pytest</dt><dd>{html.escape(pyt_str)} collected</dd>'
        f'<dt>LLM</dt><dd>{html.escape(env.get("llm_provider","noop"))}</dd>'
        f'<dt>임베딩</dt><dd>{html.escape(env.get("embedding_provider","hash"))}</dd>'
        f'<dt>벡터</dt><dd>{html.escape(env.get("vector_backend","inmemory"))}</dd>'
        f'<dt>서비스</dt><dd>'
        f'{_svc_badge("PG", svc.get("postgres","?"))} '
        f'{_svc_badge("ES", svc.get("elasticsearch","?"))} '
        f'{_svc_badge("Redis", svc.get("redis","?"))} '
        f'{_svc_badge("MinIO", svc.get("minio","?"))}'
        f'</dd>'
        f'</dl>'
    )


def _main_cli() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="perf JSON path")
    ap.add_argument("--out", required=True, help="HTML out path")
    ap.add_argument("--mode", default="dryrun")
    args = ap.parse_args()

    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    out = render_html(
        report,
        out_path=Path(args.out),
        history_dir=Path(args.input).parent,
        mode=args.mode,
    )
    print(f"[render] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_cli())
