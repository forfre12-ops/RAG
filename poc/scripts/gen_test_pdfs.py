"""Phase 4: test_set_v2 → PDF 파일 변환 (reportlab, 한글 지원).

출력: datasets/test_docs_pdf/{domain}_{grade}_{idx}.pdf
"""
import json
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

TEST_DIR = Path("datasets/test_set_v2")
PDF_DIR  = Path("datasets/test_docs_pdf")
PDF_DIR.mkdir(parents=True, exist_ok=True)

# 한글 폰트 등록 — Windows 기본 폰트 사용
FONT_PATHS = [
    Path("C:/Windows/Fonts/malgun.ttf"),       # 맑은 고딕
    Path("C:/Windows/Fonts/NanumGothic.ttf"),  # 나눔고딕
]
FONT_NAME = "Korean"
for fp in FONT_PATHS:
    if fp.exists():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(fp)))
        print(f"폰트 등록: {fp.name}")
        break
else:
    print("[WARN] 한글 폰트 없음 — 기본 폰트 사용 (한글 깨질 수 있음)")
    FONT_NAME = "Helvetica"

GRADE_LABELS = {"TS": "【특급기밀】", "S1": "【1급 비밀】", "S2": "【대외비】", "S3": "【공개】"}


def make_pdf(record: dict, out_path: Path):
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=25*mm, rightMargin=25*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("KTitle", fontName=FONT_NAME, fontSize=14,
                                  spaceAfter=6, leading=20, alignment=1)
    meta_style  = ParagraphStyle("KMeta",  fontName=FONT_NAME, fontSize=9,
                                  textColor=(0.4,0.4,0.4), spaceAfter=12)
    body_style  = ParagraphStyle("KBody",  fontName=FONT_NAME, fontSize=10,
                                  leading=16, spaceAfter=8)

    grade  = record.get("target_grade", "")
    label  = GRADE_LABELS.get(grade, grade)
    title  = record.get("title", "무제")
    domain = record.get("domain", "")
    body   = record.get("body", "")

    story = [
        Paragraph(label, meta_style),
        Paragraph(title, title_style),
        Spacer(1, 4*mm),
        Paragraph(f"도메인: {domain}  |  등급: {grade}", meta_style),
        Spacer(1, 4*mm),
    ]
    for para in body.split("\n"):
        para = para.strip()
        if para:
            story.append(Paragraph(para.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"), body_style))

    doc.build(story)


files = sorted(TEST_DIR.glob("*.json"))
if not files:
    print(f"[ERROR] {TEST_DIR} 에 파일 없음 — Phase 2 완료 후 실행하세요.")
    sys.exit(1)

ok = fail = 0
for f in files:
    try:
        record   = json.loads(f.read_text("utf-8"))
        out_path = PDF_DIR / f.with_suffix(".pdf").name
        make_pdf(record, out_path)
        ok += 1
        if ok % 20 == 0:
            print(f"  {ok}/{len(files)} PDF 생성됨...")
    except Exception as e:  # noqa: BLE001
        fail += 1
        print(f"  FAIL {f.name}: {e}")

print(f"\n완료: {ok}건 PDF 생성 / 실패: {fail}건 → {PDF_DIR}")
