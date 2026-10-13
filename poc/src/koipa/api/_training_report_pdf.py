"""학습 모델 성능 리포트의 서버측 PDF 렌더러.

DB에 이미 저장된 지표만 그린다. 손실 이력이 없는 과거 모델에는 곡선을 보간하거나
추정하지 않고 ``미기록``이라고 명시한다. 이 PDF는 모델 활성화 승인이 아니라,
검토자가 저장된 평가 결과를 확인하기 위한 읽기 전용 산출물이다.
"""

from __future__ import annotations

import io
from typing import Any
from xml.sax.saxutils import escape


def _display_number(value: Any, *, percent: bool = False) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "미기록"
    return f"{float(value) * 100:.2f}%" if percent else f"{float(value):.4f}"


def _font_name() -> str:
    """ReportLab에 내장된 CID 한글 폰트를 한 번만 등록한다."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    name = "HYSMyeongJo-Medium"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    return name


def _curve_drawing(points: list[Any], *, font_name: str, title: str, series_spec: tuple[tuple[str, str, Any], ...]):
    """저장된 값이 있을 때만 단순 벡터 선 그래프를 만든다.

    [2026-10-11] loss 전용이던 것을 series_spec으로 일반화 — 같은 그리기 코드로
    Accuracy 곡선도 그린다(KL 요구사항 추적표 6절 "Epoch별 Loss 및 Accuracy 변화
    그래프"). loss와 accuracy는 값의 범위가 달라(loss는 0 이상 임의값, accuracy는
    0~1) 같은 좌표축에 같이 그리면 왜곡되므로 호출부에서 별도 차트로 나눠 부른다.
    """
    from reportlab.graphics.shapes import Drawing, Line, PolyLine, String
    from reportlab.lib import colors

    series: list[tuple[str, list[float], Any]] = []
    for label, attr, color in series_spec:
        values = [getattr(point, attr, None) for point in points]
        if any(value is not None for value in values):
            series.append((label, [float(value) if value is not None else float("nan") for value in values], color))
    if not series:
        return None

    width, height = 480, 190
    left, bottom, right, top = 44, 30, 10, 22
    plot_width, plot_height = width - left - right, height - bottom - top
    values = [value for _, row, _ in series for value in row if value == value]
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    drawing = Drawing(width, height)
    drawing.add(Line(left, bottom, left, height - top, strokeColor=colors.HexColor("#64748b")))
    drawing.add(Line(left, bottom, width - right, bottom, strokeColor=colors.HexColor("#64748b")))
    drawing.add(String(left, height - 14, title, fontName=font_name, fontSize=10, fillColor=colors.HexColor("#0f172a")))
    drawing.add(String(2, height - top - 5, f"{hi:.3f}", fontName=font_name, fontSize=7, fillColor=colors.HexColor("#64748b")))
    drawing.add(String(2, bottom - 2, f"{lo:.3f}", fontName=font_name, fontSize=7, fillColor=colors.HexColor("#64748b")))

    count = max(len(points) - 1, 1)
    legend_offset = 112
    for label, row, color in series:
        previous: list[float] = []
        for index, value in enumerate(row):
            if value != value:  # NaN: loss type가 없는 로그 사이에서 선을 잇지 않는다.
                if len(previous) >= 4:
                    drawing.add(PolyLine(previous, strokeColor=color, strokeWidth=1.7))
                previous = []
                continue
            x = left + (plot_width * index / count)
            y = bottom + ((value - lo) / (hi - lo) * plot_height)
            previous.extend((x, y))
        if len(previous) >= 4:
            drawing.add(PolyLine(previous, strokeColor=color, strokeWidth=1.7))
        legend_x = left + legend_offset
        drawing.add(Line(legend_x, height - 14, legend_x + 13, height - 14, strokeColor=color, strokeWidth=2))
        drawing.add(String(legend_x + 17, height - 18, label, fontName=font_name, fontSize=7, fillColor=color))
        legend_offset += len(label) * 15 + 42
    return drawing


def render_training_report_pdf(report: Any) -> bytes:
    """``TrainReportResponse`` 호환 객체를 1~2쪽 PDF 바이트로 렌더한다."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_name = _font_name()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"KOIPA model report {getattr(report, 'version_label', '')}",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("koipa-title", parent=styles["Title"], fontName=font_name, fontSize=18, leading=25, textColor=colors.HexColor("#0f172a"))
    heading = ParagraphStyle("koipa-heading", parent=styles["Heading2"], fontName=font_name, fontSize=12, leading=18, textColor=colors.HexColor("#0f172a"), spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("koipa-body", parent=styles["BodyText"], fontName=font_name, fontSize=9, leading=14, textColor=colors.HexColor("#334155"))
    small = ParagraphStyle("koipa-small", parent=body, fontSize=8, leading=12, textColor=colors.HexColor("#64748b"))

    story = [
        Paragraph("AI 영업비밀관리시스템 - 모델 성능 리포트", title),
        Spacer(1, 4 * mm),
        Paragraph(
            f"버전 <b>{escape(str(getattr(report, 'version_label', '미상')))}</b> · "
            f"기반 모델 {escape(str(getattr(report, 'base_model', None) or '미기록'))} · "
            f"학습 시각 {escape(str(getattr(report, 'trained_at', None) or '미기록'))}",
            body,
        ),
        Paragraph("이 문서는 저장된 평가 지표를 읽기 전용으로 정리한 것이며, 모델 활성화나 사람의 최종 판정을 대신하지 않습니다.", small),
        Paragraph("핵심 성능", heading),
    ]
    rows = [["지표", "값"],
            ["정확도", _display_number(getattr(report, "accuracy", None), percent=True)],
            ["정밀도(Macro)", _display_number(getattr(report, "precision_macro", None), percent=True)],
            ["재현율(Macro)", _display_number(getattr(report, "recall_macro", None), percent=True)],
            ["F1(Macro)", _display_number(getattr(report, "f1_macro", None), percent=True)],
            ["전체 미탐율", _display_number(getattr(report, "fnr_overall", None), percent=True)],
            ["고등급 미탐율", _display_number(getattr(report, "fnr_high", None), percent=True)]]
    metrics = Table(rows, colWidths=[70 * mm, 80 * mm])
    metrics.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(metrics)

    story.append(Paragraph("학습 손실", heading))
    points = list(getattr(report, "learning_curve", None) or [])
    loss_series = (
        ("학습 손실", "train_loss", colors.HexColor("#2563eb")),
        ("검증 손실", "eval_loss", colors.HexColor("#d97706")),
    )
    chart = _curve_drawing(points, font_name=font_name, title="에폭별 손실 추이", series_spec=loss_series)
    if chart is None:
        story.append(Paragraph("이 모델에는 에폭별 손실 이력이 저장되어 있지 않습니다. 값을 추정해 그리지 않습니다.", body))
    else:
        story.append(chart)
        story.append(Paragraph(f"저장된 손실 로그 {len(points)}건", small))

    # [2026-10-11] KL 요구사항 추적표 6절 — Epoch별 Accuracy 변화 그래프.
    # loss와 축 범위가 달라 같은 차트에 안 섞고 별도로 그린다. 이 리포트를 만들기 전
    # 모델은 에폭별 accuracy를 안 남겨 이 값이 전부 비어 있다 — 그 경우도 추정해
    # 그리지 않고 명시적으로 "미기록"이라 말한다.
    story.append(Paragraph("에폭별 정확도", heading))
    accuracy_series = (("정확도", "accuracy", colors.HexColor("#16a34a")),)
    accuracy_chart = _curve_drawing(points, font_name=font_name, title="에폭별 정확도 추이", series_spec=accuracy_series)
    if accuracy_chart is None:
        story.append(Paragraph("이 모델에는 에폭별 정확도 이력이 저장되어 있지 않습니다. 값을 추정해 그리지 않습니다.", body))
    else:
        story.append(accuracy_chart)
        story.append(Paragraph(f"저장된 정확도 로그 {sum(1 for p in points if getattr(p, 'accuracy', None) is not None)}건", small))

    comparison = getattr(report, "compared_to_active", None)
    if comparison is not None:
        story.append(Paragraph("현재 활성 모델 대비", heading))
        compare_rows = [["비교 대상", escape(str(getattr(comparison, "version_label", "미상")))],
                        ["정확도 차이", _display_number(getattr(comparison, "accuracy_delta", None), percent=True)],
                        ["F1 차이", _display_number(getattr(comparison, "f1_macro_delta", None), percent=True)],
                        ["전체 미탐율 차이", _display_number(getattr(comparison, "fnr_overall_delta", None), percent=True)]]
        table = Table(compare_rows, colWidths=[70 * mm, 80 * mm])
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(table)

    story.append(Paragraph("등급별 미탐율", heading))
    fnr_rows = [["등급", "미탐율"]]
    fnr = getattr(report, "fnr_by_grade", None) or {}
    for grade in ("TS", "S1", "S2", "S3"):
        fnr_rows.append([grade, _display_number(fnr.get(grade), percent=True)])
    fnr_table = Table(fnr_rows, colWidths=[70 * mm, 80 * mm])
    fnr_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef8")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(fnr_table)
    doc.build(story)
    return buffer.getvalue()
