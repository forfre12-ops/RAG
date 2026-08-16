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

# Windows cp949 콘솔에서 한글·유니코드 기호(— ↑ ↓ ≥) 출력 보장.
# Linux/CI에는 영향 없음 (이미 utf-8).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass
import time
from datetime import datetime, timezone
from pathlib import Path

# repo root 자동 인식
HERE = Path(__file__).resolve().parent
POC_ROOT = HERE.parent
REPO_ROOT = POC_ROOT.parent
sys.path.insert(0, str(POC_ROOT / "src"))

# PSH는 TestClient inprocess로 분당 수십 호출 — slowapi rate-limit 자동 비활성.
# 사용자가 명시적으로 RATE_LIMIT_DISABLED=0 설정한 경우만 활성 유지.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")


def _requested_mode(argv: list[str]) -> str:
    """argparse 이전에 --mode 를 읽는다 — 아래 자격증명 기본값이 koipa import 전에 필요하다."""
    for i, arg in enumerate(argv):
        if arg == "--mode" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--mode="):
            return arg.split("=", 1)[1]
    return "dryrun"


# dryrun 전용 자격증명 기본값.
#
# settings.api_key 기본값은 빈 문자열이고, 빈 키는 인증 자체가 불가하다(config.py 가드).
# 그래서 키를 안 준 dryrun 은 전 시나리오가 401 을 받았고, 스키마·폴링·게이트 KPI 가
# "측정 0.0 ≥ True" 로 FAIL, 지연 KPI 는 값을 못 남겨 SKIP 으로 집계됐다
# (2026-08-16 실측: 66 KPI 중 PASS 15 = 22.7%. 키·DB 를 주면 PASS 43 = 65.2%).
# 역할 헤더 신뢰도 같이 켠다 — 시나리오가 admin·reviewer·kl_backend 를 번갈아 자칭하는데
# 기본값(api_key_role=system)으로는 confirm·relabel·등급체계 API 가 403 이다.
# 둘 다 setdefault 라 운영자가 준 값이 우선하고, full 모드는 실 자격증명을 쓰므로 건드리지 않는다.
# 운영(poc_mode=full)에서는 config startup 검사가 이 편의 플래그를 fail-fast 로 막는다.
if _requested_mode(sys.argv) == "dryrun":
    os.environ.setdefault("API_KEY", "psh-dryrun-inprocess-key")
    os.environ.setdefault("API_KEY_TRUST_ACTOR_ROLE_HEADER", "1")

from koipa.perf import capture_env  # noqa: E402
from koipa.perf.harness import (  # noqa: E402
    AvailableResources,
    ScenarioRunner,
    summarize,
)
from koipa.perf.recorder import write_report  # noqa: E402
from koipa.perf.scenarios import SPECS  # noqa: E402


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
    ap.add_argument(
        "--push-prom",
        default=os.environ.get("PROM_PUSHGATEWAY_URL", ""),
        help=(
            "Prometheus pushgateway URL (예: http://pushgw:9091). "
            "환경변수 PROM_PUSHGATEWAY_URL로도 설정 가능. "
            "전송 실패는 silent — PSH 자체 결과엔 영향 없음."
        ),
    )
    ap.add_argument(
        "--push-job",
        default="koipa_psh",
        help="pushgateway job 이름 (Prometheus 라벨)",
    )
    ap.add_argument(
        "--regression-threshold",
        type=float,
        default=0.0,
        help=(
            "직전 회차 대비 N%% 이상 악화 시 회귀로 판정 (0=비활성). "
            "예: 20 → 20%% 이상 악화 시 보고서에 표기, fail-on-regression 사용 시 exit 1."
        ),
    )
    ap.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="회귀 1건이라도 발견 시 exit 1 (CI 회귀 게이트)",
    )
    args = ap.parse_args()

    print(f"[PSH] mode={args.mode}  out_dir={args.out_dir}")
    report = build_report(mode=args.mode, probe_services=not args.no_probe)

    # 회귀 비교 — 직전 회차 (동일 mode) 1건만 사용
    regressions: list[dict] = []
    if args.regression_threshold > 0:
        from koipa.perf.recorder import load_history  # noqa: PLC0415
        from koipa.perf.regression import (  # noqa: PLC0415
            detect_regression_trend,
            detect_regressions,
        )

        prev_history = load_history(args.out_dir, mode=args.mode, limit=1)
        prev = prev_history[0] if prev_history else None
        regressions = detect_regressions(
            report, prev, threshold_pct=args.regression_threshold
        )
        report["regressions"] = regressions

        # [QW] 단발(prev 1건) 회귀만 보면 일시 스파이크와 지속 저하를 구별 못 한다.
        # 최근 회차로 추세분석을 run-summary에 동반 기록(이전엔 HTML 리포트에서만 호출).
        trend_history = load_history(args.out_dir, mode=args.mode, limit=5)
        if len(trend_history) >= 3:
            report["regression_trends"] = detect_regression_trend(
                trend_history, threshold_pct=args.regression_threshold
            )
        if regressions:
            print(f"[PSH] REGRESSIONS ({len(regressions)} ≥ {args.regression_threshold}%):")
            for r in regressions:
                arrow = "↑" if r["direction"] == "up" else "↓"
                print(
                    f"        {r['kpi_id']} {r['name']}  "
                    f"{r['prev']:.3g}{r['unit']} → {r['curr']:.3g}{r['unit']}  "
                    f"({arrow} {abs(r['delta_pct']):.1f}%)"
                )
        else:
            print(f"[PSH] 회귀 없음 (≥ {args.regression_threshold}%)")

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

    if args.push_prom:
        from koipa.perf.pushgateway import push  # noqa: PLC0415

        ok = push(report, url=args.push_prom, job=args.push_job)
        if ok:
            print(f"[PSH] pushgateway → {args.push_prom} (job={args.push_job})")
        else:
            print(f"[PSH] pushgateway 전송 실패 (URL={args.push_prom}) — silent skip")

    if args.fail_on_miss and summ["fail"] > 0:
        print(f"[PSH] FAIL ({summ['fail']} KPI miss) — exit 1")
        return 1
    if args.fail_on_regression and regressions:
        print(f"[PSH] REGRESSION ({len(regressions)} KPI) — exit 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
