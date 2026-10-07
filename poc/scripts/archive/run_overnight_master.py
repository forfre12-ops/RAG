"""오버나이트 마스터 — Phase 3~5 + P1재학습 + 결과 요약 자동 실행.

실행 순서:
  Phase 3: ES 재적재 (index_rag_corpus.py)
  Phase 4: PDF 변환 + 업로드 E2E (run_phase4_pdf_upload.py)   <- Phase 3 완료 후
  Phase 5: p5 교체 + E2E 측정 (run_phase5_p5_update.py)       <- Phase 3 완료 후
  P1 재학습: 6도메인 균형 데이터 (run_p1_retrain_v2.py)        <- Phase 3과 병렬
  P2 Recall 재측정: 새 코퍼스 기준                            <- Phase 3 완료 후
"""
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

LOG_DIR = Path("reports/overnight")
LOG_DIR.mkdir(parents=True, exist_ok=True)

START = time.time()


def run(name: str, script: str, *args) -> tuple[int, float]:
    t0 = time.time()
    log_path = LOG_DIR / f"{name}.log"
    cmd = [sys.executable, f"scripts/{script}"] + list(args)
    print(f"\n{'='*55}")
    print(f">> {name}")
    print(f"  cmd: {' '.join(cmd)}")
    print(f"  log: {log_path}")
    with open(log_path, "w", encoding="utf-8") as lf:
        ret = subprocess.call(cmd, stdout=lf, stderr=subprocess.STDOUT)
    elapsed = time.time() - t0
    status  = "PASS" if ret == 0 else f"FAIL(exit={ret})"
    print(f"  {status}  {elapsed:.0f}s")
    lines = log_path.read_text("utf-8", errors="replace").splitlines()
    for l in lines[-10:]:
        print(f"    {l}")
    return ret, elapsed


summary = {}

# Phase 3: ES 재적재
ret, t = run("phase3_es_index", "index_rag_corpus.py")
summary["phase3_es_index"] = {"exit": ret, "elapsed_s": round(t)}

if ret == 0:
    ret4, t4 = run("phase4_pdf_upload", "run_phase4_pdf_upload.py")
    summary["phase4_pdf_upload"] = {"exit": ret4, "elapsed_s": round(t4)}

    ret5, t5 = run("phase5_p5_update", "run_phase5_p5_update.py")
    summary["phase5_p5_update"] = {"exit": ret5, "elapsed_s": round(t5)}

    retP2, tP2 = run("p2_recall5",
                     "p2_compare_embeddings.py",
                     "--mode", "hybrid-only",
                     "--backends", "es",
                     "--synth-dir", "datasets/rag_corpus_v2",
                     "--report", "reports/p2_v2_embedding_report.md")
    summary["p2_recall5"] = {"exit": retP2, "elapsed_s": round(tP2)}

    retP1, tP1 = run("p1_retrain_v2", "run_p1_retrain_v2.py")
    summary["p1_retrain_v2"] = {"exit": retP1, "elapsed_s": round(tP1)}
else:
    print("\n[WARN] Phase 3 실패 -- Phase 4/5/P1 스킵")
    summary["skipped"] = ["phase4", "phase5", "p1_retrain", "p2_recall"]

total_elapsed = time.time() - START
summary["total_elapsed_s"] = round(total_elapsed)

print(f"\n{'='*55}")
print(f"오버나이트 작업 완료  총 {total_elapsed/60:.1f}분")
for k, v in summary.items():
    if isinstance(v, dict) and "exit" in v:
        status = "PASS" if v["exit"] == 0 else "FAIL"
        print(f"  {status} {k}: {v['elapsed_s']}s")

report_path = LOG_DIR / "summary.json"
report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n요약: {report_path}")
