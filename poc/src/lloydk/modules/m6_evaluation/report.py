"""평가 리포트 — HTML (jinja2) + Confusion Matrix PNG (matplotlib).

PDF는 폐쇄망 GTK 의존성 까다로워 본 PoC는 HTML만. 브라우저 인쇄로 PDF 대체.
"""

from __future__ import annotations

import base64
import io
from typing import Optional

from lloydk.modules.m6_evaluation.confusion_matrix import ConfusionMatrixResult
from lloydk.modules.m6_evaluation.metrics import MetricsResult


# ------------------------------------------------------------
# Confusion matrix PNG
# ------------------------------------------------------------


def render_confusion_matrix_png(cm: ConfusionMatrixResult, *, dpi: int = 100) -> bytes:
    """CM을 PNG bytes로 렌더링. matplotlib 비대화형 백엔드 사용."""
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    labels = cm.labels
    matrix = np.array(cm.matrix) if cm.matrix else np.zeros((len(labels), len(labels)), dtype=int)

    fig, ax = plt.subplots(figsize=(5, 4.5), dpi=dpi)
    im = ax.imshow(matrix, cmap="Greys")
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_yticklabels(labels)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title("Confusion Matrix")

    # cell annotations
    if matrix.size > 0:
        vmax = matrix.max() or 1
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                color = "white" if matrix[i, j] > vmax * 0.6 else "black"
                ax.text(j, i, int(matrix[i, j]), ha="center", va="center", color=color, fontsize=10)

    fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ------------------------------------------------------------
# HTML report
# ------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>모델 평가 리포트 — {{ model_version }}</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Pretendard", "Noto Sans KR", sans-serif;
         max-width: 900px; margin: 32px auto; padding: 0 24px; color: #0a0a0a; line-height: 1.6; }
  h1 { font-size: 28px; letter-spacing: -0.02em; margin-bottom: 4px; }
  .sub { color: #525252; margin-bottom: 32px; }
  table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13.5px; }
  th, td { padding: 8px 12px; border-bottom: 1px solid rgba(0,0,0,0.08); text-align: left; }
  th { background: #fafafa; color: #525252; font-weight: 500; font-size: 11px;
       text-transform: uppercase; letter-spacing: 0.06em; }
  .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0; }
  .kpi { border: 1px solid rgba(0,0,0,0.08); border-radius: 8px; padding: 14px 16px; }
  .kpi .v { font-size: 22px; font-weight: 700; letter-spacing: -0.01em; }
  .kpi .l { font-size: 11px; color: #525252; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 4px; }
  .alarm { padding: 10px 14px; margin: 6px 0; border-radius: 6px; font-size: 13.5px; }
  .alarm.critical { background: rgba(220,38,38,0.08); border-left: 3px solid #dc2626; }
  .alarm.high     { background: rgba(217,119,6,0.08); border-left: 3px solid #d97706; }
  .alarm.medium   { background: rgba(0,112,243,0.06); border-left: 3px solid #0070f3; }
  img.cm { display: block; margin: 12px 0; max-width: 480px; border: 1px solid rgba(0,0,0,0.08); border-radius: 8px; }
  code { background: #fafafa; padding: 1px 6px; border-radius: 4px; font-family: ui-monospace, monospace; font-size: 0.9em; }
</style>
</head>
<body>
<h1>모델 평가 리포트</h1>
<div class="sub">model_version: <code>{{ model_version }}</code> · 측정 시점: {{ measured_at }} · 샘플 수: {{ sample_count }}</div>

<h2>1. 핵심 KPI</h2>
<div class="kpi-grid">
  <div class="kpi"><div class="v">{{ "%.4f"|format(accuracy) }}</div><div class="l">Accuracy</div></div>
  <div class="kpi"><div class="v">{{ "%.4f"|format(f1_macro) }}</div><div class="l">F1 macro</div></div>
  <div class="kpi"><div class="v">{{ "%.4f"|format(fnr_overall) }}</div><div class="l">FNR overall</div></div>
  <div class="kpi"><div class="v">{{ sample_count }}</div><div class="l">Samples</div></div>
</div>

<h2>2. 등급별 지표</h2>
<table>
<thead><tr><th>Grade</th><th>Precision</th><th>Recall</th><th>F1</th><th>FNR</th><th>Support</th></tr></thead>
<tbody>
{% for lbl in labels %}
  {% set pc = per_class.get(lbl, {}) %}
  <tr>
    <td><b>{{ lbl }}</b></td>
    <td>{{ "%.4f"|format(pc.get('precision', 0.0)) }}</td>
    <td>{{ "%.4f"|format(pc.get('recall', 0.0)) }}</td>
    <td>{{ "%.4f"|format(pc.get('f1', 0.0)) }}</td>
    <td>{{ "%.4f"|format(pc.get('fnr', 0.0)) }}</td>
    <td>{{ pc.get('support', 0) }}</td>
  </tr>
{% endfor %}
</tbody>
</table>

<h2>3. Confusion Matrix</h2>
{% if cm_png_b64 %}
<img class="cm" src="data:image/png;base64,{{ cm_png_b64 }}" alt="Confusion Matrix"/>
{% else %}
<p><i>CM 시각화 사용 불가 (matplotlib 미설치 또는 샘플 0건).</i></p>
{% endif %}

<h2>4. 미탐(Underclass) 알람</h2>
{% if alarms %}
{% for a in alarms %}
<div class="alarm {{ a.severity }}">
  <b>[{{ a.severity|upper }}]</b> 정답 <code>{{ a.truth }}</code> → 예측 <code>{{ a.pred }}</code>
  · {{ a.count }} 건 ({{ "%.1f"|format(a.rate * 100) }}%)
</div>
{% endfor %}
{% else %}
<p>알람 없음 — 미탐 셀이 임계 이하입니다.</p>
{% endif %}

<h2>5. 캘리브레이션 (Confidence ↔ 정확도)</h2>
{% if calibration %}
<div class="kpi-grid">
  <div class="kpi"><div class="v">{{ "%.4f"|format(calibration.ece) }}</div><div class="l">ECE</div></div>
  <div class="kpi"><div class="v">{{ "%.4f"|format(calibration.brier) }}</div><div class="l">Brier</div></div>
  <div class="kpi"><div class="v">{{ calibration.sample_count }}</div><div class="l">Samples</div></div>
</div>
<table>
<thead><tr><th>신뢰도 구간</th><th>건수</th><th>평균신뢰도</th><th>정확도</th><th>gap</th></tr></thead>
<tbody>
{% for bn in calibration.bins %}
  <tr><td>{{ "%.1f"|format(bn.lo) }}–{{ "%.1f"|format(bn.hi) }}</td><td>{{ bn.count }}</td>
      <td>{{ "%.4f"|format(bn.avg_confidence) }}</td><td>{{ "%.4f"|format(bn.accuracy) }}</td>
      <td>{{ "%.4f"|format(bn.gap) }}</td></tr>
{% endfor %}
</tbody>
</table>
<p style="font-size:12px;color:#525252">ECE가 높으면 confidence가 실제 정확도와 어긋남 —
0.7 게이트의 의미가 약해진다. temperature 재보정(calibrate_classifier.py) 또는 drift 점검 필요.</p>
{% else %}
<p><i>캘리브레이션 데이터 없음 (분류·교정 표본 부족).</i></p>
{% endif %}

</body>
</html>
"""


def render_html_report(
    metrics: MetricsResult,
    cm_result: ConfusionMatrixResult,
    *,
    measured_at: Optional[str] = None,
    include_cm_image: bool = True,
    calibration: object | None = None,
) -> str:
    """평가 결과를 단일 HTML 문자열로 렌더링.

    jinja2 미설치 시 ImportError — pyproject 기본 의존성에 포함됨 (mlflow 의존).
    """
    from jinja2 import Template  # noqa: PLC0415

    cm_png_b64 = ""
    if include_cm_image and cm_result.matrix:
        try:
            png = render_confusion_matrix_png(cm_result)
            cm_png_b64 = base64.b64encode(png).decode("ascii")
        except Exception:  # noqa: BLE001
            cm_png_b64 = ""

    tmpl = Template(_HTML_TEMPLATE)
    return tmpl.render(
        model_version=metrics.model_version,
        measured_at=measured_at or "n/a",
        sample_count=metrics.sample_count,
        accuracy=metrics.accuracy,
        f1_macro=metrics.f1_macro,
        fnr_overall=metrics.fnr_overall,
        labels=metrics.labels,
        per_class=metrics.per_class,
        cm_png_b64=cm_png_b64,
        alarms=[a.to_dict() for a in cm_result.alarms],
        calibration=(calibration.to_dict() if hasattr(calibration, "to_dict") else calibration),
    )
