"""Phase 5: test_set_v2 기반으로 p5_e2e_smoke.py SAMPLES 교체 + 실행."""
import io
import json
import os
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(".env")

import httpx

TEST_DIR = Path("datasets/test_set_v2")
API_URL  = "http://localhost:8000"
API_KEY  = "devkey"
DOMAINS  = ["tech", "business", "finance", "hr", "legal", "security"]
GRADES   = ["TS", "S1", "S2", "S3"]


def pick_samples(test_dir: Path) -> list[dict]:
    samples = []
    for domain in DOMAINS:
        for grade in GRADES:
            candidates = sorted(test_dir.glob(f"{domain}_{grade}_*.json"))
            chosen = None
            for f in candidates:
                rec = json.loads(f.read_text("utf-8"))
                if rec.get("label_match"):
                    chosen = rec
                    break
            if not chosen and candidates:
                chosen = json.loads(candidates[0].read_text("utf-8"))
            if chosen:
                samples.append({
                    "doc_id":         f"smoke-{domain}-{grade}-001",
                    "tenant_id":      "poc",
                    "title":          chosen.get("title", ""),
                    "content":        chosen.get("body", "")[:2000],
                    "expected_grade": grade,
                    "domain":         domain,
                })
    return samples


# ── 1. smoke_samples.json 갱신 ────────────────────────────────
print("=== Phase 5-A: p5 SAMPLES 갱신 ===")
samples = pick_samples(TEST_DIR)
SAMPLES_JSON = Path("datasets/test_set_v2/smoke_samples.json")
SAMPLES_JSON.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"smoke_samples.json 저장: {len(samples)}건")

# ── 2. E2E 실행 ───────────────────────────────────────────────
print("\n=== Phase 5-B: E2E 실행 ===")
results = []
with httpx.Client(timeout=60.0) as cli:
    for s in samples:
        payload = {k: v for k, v in s.items() if k not in ("expected_grade", "domain")}
        payload["use_rag"] = True
        t0 = time.perf_counter()
        try:
            r = cli.post(f"{API_URL}/api/v1/classify",
                         headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
                         json=payload)
            elapsed = (time.perf_counter() - t0) * 1000
            data = r.json()
            pred     = data.get("label", "?")
            expected = s["expected_grade"]
            match    = pred == expected
            mark = "[OK]" if match else "[NG]"
            print(f"  {mark} {s['domain']}/{expected} -> {pred}  {elapsed:.0f}ms")
            results.append({"domain": s["domain"], "expected": expected, "pred": pred,
                             "match": match, "elapsed_ms": round(elapsed)})
        except Exception as e:
            print(f"  [ERR] {s['domain']}/{s['expected_grade']} -> {e}")
            results.append({"domain": s["domain"], "expected": s["expected_grade"],
                             "pred": "ERROR", "match": False, "elapsed_ms": 0})

correct = sum(1 for r in results if r["match"])
total   = len(results)
print(f"\n정확도: {correct}/{total} = {correct/max(total,1)*100:.1f}%")

by_grade = {}
for r in results:
    g = r["expected"]
    by_grade.setdefault(g, {"correct": 0, "total": 0})
    by_grade[g]["total"] += 1
    if r["match"]:
        by_grade[g]["correct"] += 1

print("등급별:")
for g in GRADES:
    s2 = by_grade.get(g, {"correct": 0, "total": 0})
    print(f"  {g}: {s2['correct']}/{s2['total']}")

Path("reports").mkdir(exist_ok=True)
Path("reports/phase5_e2e_report.json").write_text(
    json.dumps({"accuracy": correct/max(total,1), "results": results, "by_grade": by_grade},
               ensure_ascii=False, indent=2), encoding="utf-8")
print("\n리포트: reports/phase5_e2e_report.json")
