"""PSH 진입점 — 시나리오 성능 보고서 생성.

사용:
    python scripts/run_perf_scenarios.py                  # dryrun
    python scripts/run_perf_scenarios.py --mode full      # 실측
    python scripts/run_perf_scenarios.py --fail-on-miss   # KPI 미달 시 exit 1 (CI용)

산출:
    poc/reports/perf/perf_{ts}_{mode}_{sha}.json
    poc/reports/perf/perf_latest_{mode}.json
    doc/20_시나리오_성능_보고서.html  (--html-out 으로 변경 가능)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# repo root 자동 인식
HERE = Path(__file__).resolve().parent
POC_ROOT = HERE.parent
REPO_ROOT = POC_ROOT.parent
sys.path.insert(0, str(POC_ROOT / "src"))

from lloydk.perf import capture_env  # noqa: E402
from lloydk.perf.harness import (  # noqa: E402
    AvailableResources,
    ScenarioRunner,
    summarize,
)
from lloydk.perf.recorder import write_report  # noqa: E402
from lloydk.perf.scenarios import SPECS  # noqa: E402


def build_report(*, mode: str, probe_services: bool) -> dict:
    env = capture_env(
        probe_services=probe_services,
        probe_pytest=False,
        llm_provider="noop" if mode == "dryrun" else os.environ.get("LLM_PROVIDER", "noop"),
        embedding_provider="hash" if mode == "dryrun" else os.environ.get("EMB_PROVIDER", "kure-v1"),
        vector_backend="inmemory" if mode == "dryrun" else os.environ.get("VEC_BACKEND", "es"),
    )
    resources = AvailableResources.from_env_snapshot(env, env.llm_provider)
    if mode == "dryrun":
        # dryrun은 외부 의존을 모두 거짓으로 — KPI requires에 의해 SKIP되지 않고 in-process로 통과
        # 단, requires=[]인 KPI는 그대로 측정됨
        pass

    runner = ScenarioRunner(mode=mode, resources=resources)
    t0 = time.perf_counter()
    results = runner.run(SPECS)
    duration = time.perf_counter() - t0
    summary = summarize(results)

    report = {
        "mode": mode,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_sec": round(duration, 3),
        "env": env.to_dict(),
        "scenarios": [s.to_dict() for s in results],
        "summary": summary,
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="PSH — Performance Scenario Harness")
    ap.add_argument("--mode", choices=["dryrun", "full"], default="dryrun")
    ap.add_argument("--out-dir", default=str(POC_ROOT / "reports" / "perf"))
    ap.add_argument(
        "--html-out",
        default=str(REPO_ROOT / "doc" / "20_시나리오_성능_보고서.html"),
    )
    ap.add_argument("--no-html", action="store_true", help="HTML 렌더 생략")
    ap.add_argument("--no-probe", action="store_true", help="서비스 핑 생략 (오프라인용)")
    ap.add_argument("--fail-on-miss", action="store_true", help="KPI 1건이라도 FAIL 시 exit 1")
    args = ap.parse_args()

    print(f"[PSH] mode={args.mode}  out_dir={args.out_dir}")
    report = build_report(mode=args.mode, probe_services=not args.no_probe)

    json_path = write_report(report, out_dir=args.out_dir)
    print(f"[PSH] JSON → {json_path}")

    summ = report["summary"]
    print(
        f"[PSH] KPI: PASS={summ['pass']}  FAIL={summ['fail']}  SKIP={summ['skip']}  "
        f"({summ['pass'] }/{summ['total_kpis']}, {summ['pass_rate']*100:.1f}%)"
    )

    if not args.no_html:
        sys.path.insert(0, str(HERE))
        from render_perf_report import render_html  # type: ignore

        html_path = Path(args.html_out)
        render_html(report, out_path=html_path, history_dir=Path(args.out_dir), mode=args.mode)
        print(f"[PSH] HTML → {html_path}")

    if args.fail_on_miss and summ["fail"] > 0:
        print(f"[PSH] FAIL ({summ['fail']} KPI miss) — exit 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
