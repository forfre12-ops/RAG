"""데모 시나리오 — PDF 업로드 → 등급 분류 → 결과 출력."""
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import httpx

API_URL  = "http://localhost:8000"
API_KEY  = "devkey"
ACTOR    = json.dumps({"tenant_id": "poc", "user_id": "demo-user", "role": "system"})
PDF_DIR  = Path("datasets/test_docs_pdf")
TEST_DIR = Path("datasets/test_set_v2")

GRADE_DESC = {
    "TS": "특급기밀 - 유출 시 회사 존립 위협",
    "S1": "1급 비밀 - 유출 시 중대한 손해",
    "S2": "2급 대외비 - 유출 시 경쟁 불이익",
    "S3": "공개 - 일반 사내자료",
}

DEMO_FILES = [
    ("tech_TS_030.pdf",       "TS"),
    ("business_TS_030.pdf",   "TS"),
    ("finance_S1_030.pdf",    "S1"),
    ("hr_S2_060.pdf",         "S2"),
    ("legal_S1_030.pdf",      "S1"),
    ("security_TS_030.pdf",   "TS"),
    ("tech_S3_090.pdf",       "S3"),
    ("business_S3_090.pdf",   "S3"),
]

results = []
print("=" * 60)
print("  LLOYDK AI 영업비밀 등급분류 시스템 데모")
print("=" * 60)

with httpx.Client(timeout=120.0) as cli:
    for fname, expected in DEMO_FILES:
        pdf_path = PDF_DIR / fname
        if not pdf_path.exists():
            parts = fname.replace(".pdf","").split("_")
            domain, grade = parts[0], parts[1]
            alts = sorted(PDF_DIR.glob(f"{domain}_{grade}_*.pdf"))
            if not alts:
                print(f"\n  [SKIP] {fname} 없음")
                continue
            pdf_path = alts[0]

        json_path = TEST_DIR / pdf_path.with_suffix(".json").name
        body_text = ""
        if json_path.exists():
            body_text = json.loads(json_path.read_text("utf-8")).get("body", "")

        pdf_bytes = pdf_path.read_bytes()
        print(f"\n  파일: {pdf_path.name}")

        # Step 1: 업로드
        t0 = time.perf_counter()
        try:
            r = cli.post(
                f"{API_URL}/api/v1/documents",
                headers={"X-API-Key": API_KEY},
                data={"actor": ACTOR, "doc_type": expected},
                files={"file": (pdf_path.name, pdf_bytes, "application/pdf")},
            )
            upload_ms = (time.perf_counter() - t0) * 1000
            if r.status_code == 201:
                up = r.json()
                doc_id = up.get("doc_id")
                print(f"  업로드: {up['char_count']}자 추출 ({upload_ms:.0f}ms)  doc_id={doc_id}")
            else:
                print(f"  [UPLOAD FAIL] {r.status_code}")
                continue
        except Exception as e:
            print(f"  [UPLOAD ERROR] {e}")
            continue

        # Step 2: 분류 (content 직접 전달)
        t1 = time.perf_counter()
        r2 = cli.post(
            f"{API_URL}/api/v1/classify",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json={"doc_id": doc_id or "demo-" + pdf_path.stem,
                  "tenant_id": "poc", "content": body_text, "use_rag": True},
        )
        cls_ms = (time.perf_counter() - t1) * 1000

        if r2.status_code == 200:
            cl = r2.json()
            pred  = cl.get("label", "?")
            conf  = cl.get("confidence", 0)
            match = pred == expected
            mark  = "[OK]" if match else "[NG]"
            print(f"  분류: {pred} ({conf*100:.1f}%) - {GRADE_DESC.get(pred,'')}  {cls_ms:.0f}ms  {mark}")
            results.append({"file": pdf_path.name, "expected": expected,
                             "pred": pred, "conf": round(conf,3), "match": match})
        else:
            print(f"  [CLASSIFY FAIL] {r2.status_code}")

print(f"\n{'='*60}")
correct = sum(1 for r in results if r["match"])
print(f"  결과: {correct}/{len(results)} 정확 ({correct/max(len(results),1)*100:.1f}%)")
print(f"{'='*60}")

Path("reports").mkdir(exist_ok=True)
Path("reports/demo_result.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
