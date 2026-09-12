"""Phase 4: test_set_v2 → PDF 변환 + /documents 업로드 E2E 검증."""
import json
import os
import sys
import time
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(".env")

import httpx
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

TEST_DIR = Path("datasets/test_set_v2")
PDF_DIR  = Path("datasets/test_docs_pdf")
PDF_DIR.mkdir(parents=True, exist_ok=True)

API_URL = "http://localhost:18030"
API_KEY = "devkey"
GRADE_LABELS = {"TS": "【특급기밀】", "S1": "【1급 비밀】", "S2": "【대외비】", "S3": "【공개】"}

# 한글 폰트 등록
FONT_NAME = "Helvetica"
for fp in [Path("C:/Windows/Fonts/malgun.ttf"), Path("C:/Windows/Fonts/NanumGothic.ttf")]:
    if fp.exists():
        pdfmetrics.registerFont(TTFont("Korean", str(fp)))
        FONT_NAME = "Korean"
        print(f"폰트: {fp.name}")
        break


def make_pdf(record: dict, out_path: Path):
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=25*mm, rightMargin=25*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    title_s = ParagraphStyle("T", fontName=FONT_NAME, fontSize=14, spaceAfter=6, leading=20, alignment=1)
    meta_s  = ParagraphStyle("M", fontName=FONT_NAME, fontSize=9, textColor=(0.4,0.4,0.4), spaceAfter=12)
    body_s  = ParagraphStyle("B", fontName=FONT_NAME, fontSize=10, leading=16, spaceAfter=8)

    def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    grade = record.get("target_grade", "")
    story = [
        Paragraph(GRADE_LABELS.get(grade, grade), meta_s),
        Paragraph(esc(record.get("title", "무제")), title_s),
        Spacer(1, 4*mm),
        Paragraph(f"도메인: {record.get('domain','')}  |  등급: {grade}  |  유형: {record.get('document_type','')}", meta_s),
        Spacer(1, 4*mm),
    ]
    for para in record.get("body", "").split("\n"):
        para = para.strip()
        if para:
            story.append(Paragraph(esc(para), body_s))
    doc.build(story)


# ── 1. PDF 변환 ──────────────────────────────────────────────
print("=== Phase 4-A: PDF 변환 ===")
files = sorted(TEST_DIR.glob("*.json"))
if not files:
    print(f"[ERROR] {TEST_DIR} 비어있음")
    sys.exit(1)

pdf_ok = pdf_fail = 0
for f in files:
    try:
        rec = json.loads(f.read_text("utf-8"))
        out = PDF_DIR / f.with_suffix(".pdf").name
        make_pdf(rec, out)
        pdf_ok += 1
    except Exception as e:
        pdf_fail += 1
        print(f"  FAIL {f.name}: {e}")
    if (pdf_ok + pdf_fail) % 20 == 0:
        print(f"  {pdf_ok+pdf_fail}/{len(files)} 변환됨...")

print(f"PDF 변환: {pdf_ok}건 성공 / {pdf_fail}건 실패")

# ── 2. 업로드 E2E 검증 (등급별 1건씩) ─────────────────────────
print("\n=== Phase 4-B: PDF 업로드 E2E 검증 ===")
ACTOR = json.dumps({"tenant_id": "poc", "user_id": "test-runner", "role": "system"})

results = []
tested = set()
with httpx.Client(timeout=120.0) as cli:
    for f in sorted(PDF_DIR.glob("*.pdf")):
        parts = f.stem.split("_")
        if len(parts) < 2:
            continue
        grade = parts[1]
        domain = parts[0]
        key = f"{domain}_{grade}"
        if key in tested:
            continue
        tested.add(key)

        pdf_bytes = f.read_bytes()
        t0 = time.perf_counter()
        try:
            r = cli.post(
                f"{API_URL}/api/v1/documents",
                headers={"X-API-Key": API_KEY},
                data={"actor": ACTOR, "doc_type": grade},
                files={"file": (f.name, pdf_bytes, "application/pdf")},
            )
            elapsed = (time.perf_counter() - t0) * 1000
            if r.status_code == 201:
                data = r.json()
                doc_id = data.get("doc_id")
                char_count = data.get("char_count", 0)
                print(f"  [OK] {domain}/{grade} -> doc_id={doc_id} chars={char_count} {elapsed:.0f}ms")
                results.append({"domain": domain, "grade": grade, "doc_id": doc_id,
                                 "char_count": char_count, "status": "ok"})
            else:
                print(f"  [FAIL] {domain}/{grade} -> {r.status_code}: {r.text[:100]}")
                results.append({"domain": domain, "grade": grade, "status": "error", "detail": r.text[:100]})
        except Exception as e:
            print(f"  ❌ {domain}/{grade} → {e}")
            results.append({"domain": domain, "grade": grade, "status": "exception", "detail": str(e)})

        if len(tested) >= 12:
            break

ok_count = sum(1 for r in results if r["status"] == "ok")
print(f"\nE2E 업로드: {ok_count}/{len(results)} PASS")
Path("reports/phase4_upload_report.json").parent.mkdir(parents=True, exist_ok=True)
Path("reports/phase4_upload_report.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print("리포트: reports/phase4_upload_report.json")
