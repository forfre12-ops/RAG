"""PoC 일괄 실행 + 합격선 종합 판정.

순서: P4(추출) -> P3(합성) -> P1(분류) -> P5(E2E)
출력: reports/summary.md

[2026-09-06] P2(임베딩 비교)를 뺐다. 2026-09-04 커밋 319069b9 가 유사문서 조회를 걷어내며
`p2_compare_embeddings.py` 를 지웠는데 이 실행기가 그대로 불러 **없는 파일**을 실행했다.
임베딩 자체는 살아 있다(app 이 기동 때 워밍업한다) — 없어진 것은 검색용 임베더를 **고르려던**
비교 실험이고, 고를 대상이 없어졌다.

⚠ in-process 실행에는 API 키가 필요하다(아래 _ensure_inproc_env). 없으면 P5 가
  {"detail":"invalid api key"} 로 떨어진다 — 기능 실패로 보이지만 환경 문제다.
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
    # smoke 검증 — datasets/synthetic을 40건으로 생성 (P1·P2·analyze가 default로 읽음)
    # 800건 풀 합성은 make p3-800이 별도 디렉터리(datasets/synthetic_800)로 빌드
    ("p3_synth", [
        sys.executable, str(_HERE / "p3_generate_synthetic.py"),
        "--total", "40", "--provider", "noop",
    ]),
    ("p1_classifier", [sys.executable, str(_HERE / "p1_train_classifier.py"), "--mode", "dryrun"]),
    ("p5_e2e", [sys.executable, str(_HERE / "p5_e2e_smoke.py"), "--mode", "inproc"]),
]


def _ensure_inproc_env() -> dict:
    """in-process PoC 가 자기 API 를 부를 수 있게 한다.

    [2026-09-06] P5 가 {"detail":"invalid api key"} 로 떨어지고 있었다. 기능 실패처럼
    보이지만 **키가 없어서**다. run_perf_scenarios 는 dryrun 에서 같은 값을 심는다
    (거기 주석: "키를 안 준 dryrun 은 전 시나리오가 401 을 받았다").

    ⚠ setdefault 라 운영자가 준 값이 우선한다. 이 값은 in-process TestClient 전용이고
      어디로도 나가지 않는다.
    """
    import os  # noqa: PLC0415

    env = dict(os.environ)
    env.setdefault("TESTING", "1")
    env.setdefault("API_KEY", "poc-inproc-key")
    env.setdefault("API_KEY_TRUST_ACTOR_ROLE_HEADER", "1")
    env.setdefault("RATE_LIMIT_DISABLED", "1")
    return env


def run() -> list[dict]:
    cwd = _HERE.parent
    env = _ensure_inproc_env()
    results: list[dict] = []
    for name, cmd in STEPS:
        t0 = time.perf_counter()
        try:
            cp = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=600, env=env,
            )
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
