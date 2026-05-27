"""PoC 5종 일괄 실행 + 합격선 종합 판정.

순서: P4(추출) -> P3(합성) -> P2(임베딩) -> P1(분류) -> P5(E2E)
출력: reports/summary.md
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent

STEPS: list[tuple[str, list[str]]] = [
    ("build_p4_corpus", [sys.executable, str(_HERE / "build_p4_corpus.py")]),
    ("p4_extract", [sys.executable, str(_HERE / "p4_extract_eval.py")]),
    # smoke 전용 디렉터리로 분리 — 별도 명령(make p3-800)으로 만든 datasets/synthetic을 덮어쓰지 않도록
    ("p3_synth", [
        sys.executable, str(_HERE / "p3_generate_synthetic.py"),
        "--total", "40", "--provider", "noop",
        "--out", "datasets/synthetic_smoke",
    ]),
    ("p2_embedding", [
        sys.executable, str(_HERE / "p2_compare_embeddings.py"),
        "--mode", "dryrun",
        "--synth-dir", "datasets/synthetic_smoke",  # smoke 전용
    ]),
    ("p1_classifier", [sys.executable, str(_HERE / "p1_train_classifier.py"), "--mode", "dryrun"]),
    ("p5_e2e", [sys.executable, str(_HERE / "p5_e2e_smoke.py"), "--mode", "inproc"]),
]


def run() -> list[dict]:
    cwd = _HERE.parent
    results: list[dict] = []
    for name, cmd in STEPS:
        t0 = time.perf_counter()
        try:
            cp = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
            ok = cp.returncode == 0
            stderr_tail = (cp.stderr or "").splitlines()[-2:] if cp.stderr else []
        except subprocess.TimeoutExpired:
            ok = False
            stderr_tail = ["TIMEOUT"]
        elapsed = round(time.perf_counter() - t0, 2)
        results.append({"step": name, "ok": ok, "elapsed_s": elapsed, "tail": stderr_tail})
        print(f"  [{'OK' if ok else 'FAIL'}] {name} ({elapsed}s)")
    return results


def write_summary(results: list[dict]) -> None:
    reports_dir = _HERE.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / "summary.md"
    overall = "PASS" if all(r["ok"] for r in results) else "FAIL"
    lines = [
        "# PoC 종합 리포트",
        "",
        f"- **종합 판정**: {overall}",
        f"- 실행 단계: {len(results)}",
        f"- 통과: {sum(1 for r in results if r['ok'])} / 실패: {sum(1 for r in results if not r['ok'])}",
        "",
        "| Step | Result | Elapsed |",
        "|---|:---:|---:|",
    ]
    for r in results:
        lines.append(f"| {r['step']} | {'PASS' if r['ok'] else 'FAIL'} | {r['elapsed_s']}s |")
    lines += [
        "",
        "## 개별 리포트",
        "- [P4 추출](p4_extract_report.md)",
        "- [P3 합성](p3_synthesis_report.md)",
        "- [P2 임베딩](p2_embedding_report.md)",
        "- [P1 분류기](p1_classifier_report.md)",
        "- [P5 E2E](p5_e2e_report.md)",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[summary] {out}")


def main() -> int:
    print("=== run_all_pocs ===")
    results = run()
    write_summary(results)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
