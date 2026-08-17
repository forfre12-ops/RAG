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
    # S7(URGENT_RETRAIN)은 /train 을 부르는데, 학습 라우터는 enable_training·
    # enable_incremental_retrain 일 때만 등록된다(순수 추론 노드엔 없음). 기본 프로파일에서는
    # 404 라 S7.2/S7.3 이 값 없이 SKIP 됐다. dryrun 은 학습 노드 프로파일로 잰다.
    os.environ.setdefault("ENABLE_TRAINING", "1")
    # PSH 는 TestClient in-process 로 분당 수십 호출이라 slowapi rate-limit(분류 60/min)에 걸린다.
    # 단 이 플래그는 운영 프로파일(poc_mode=full)에서 config 가 fail-clear 로 막는다
    # ("RATE_LIMIT_DISABLED=1 은 운영 모드에서 허용되지 않습니다") — 그래서 dryrun 에서만 켠다.
    # full 실측은 운영 설정을 그대로 쓰되, 레이트리밋을 낮춰야 하면 실행자가 명시적으로 준다.
    os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from koipa.perf import capture_env  # noqa: E402
from koipa.perf.harness import (  # noqa: E402
    AvailableResources,
    ScenarioRunner,
    summarize,
)
from koipa.perf.recorder import write_report  # noqa: E402
from koipa.perf.scenarios import SPECS  # noqa: E402


def _select_specs(specs: list, only: str, skip: str) -> list:
    """--only / --skip 로 시나리오를 고른다.

    운영 중인 서버에서 실측할 때 부하(S11)·대량 배치(S12)·학습 트리거(S7)만 빼고 재는
    경우가 있다. 전부 아니면 전무였던 이전에는 그런 측정을 아예 못 했다.
    """
    only_ids = {x.strip().upper() for x in only.split(",") if x.strip()}
    skip_ids = {x.strip().upper() for x in skip.split(",") if x.strip()}
    picked = [sp for sp in specs if (not only_ids or sp.id.upper() in only_ids)
              and sp.id.upper() not in skip_ids]
    dropped = [sp.id for sp in specs if sp not in picked]
    if dropped:
        # 조용히 빼면 보고서가 "전 시나리오 통과" 로 읽힌다 — 뺀 것을 로그에 남긴다.
        print(f"[PSH] 제외된 시나리오({len(dropped)}): {', '.join(dropped)}")
    return picked


def build_report(*, mode: str, probe_services: bool, only: str = "", skip: str = "") -> dict:
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
    specs = _select_specs(SPECS, only, skip)
    t0 = time.perf_counter()
    results = runner.run(specs)
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
    ap.add_argument("--only", default="", help="이 시나리오만 실행 (쉼표구분, 예: S1,S3,S16)")
    ap.add_argument("--skip", default="", help="이 시나리오를 제외 (쉼표구분, 예: S7,S11,S12)")
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
    report = build_report(
        mode=args.mode, probe_services=not args.no_probe, only=args.only, skip=args.skip,
    )
    if args.only or args.skip:
        # 부분 실행분이 전수 실행분과 같은 파일명·추세에 섞이면 회귀 비교가 거짓말을 한다.
        report["partial_run"] = {"only": args.only, "skip": args.skip}

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

    from koipa.perf.scenarios import rate_limited_count  # noqa: PLC0415

    n_429 = rate_limited_count()
    if n_429:
        print(
            f"[PSH] 경고 — 429(레이트리밋) 응답 {n_429}건. 그만큼 KPI 가 실제보다 낮게 잡힌다. "
            "PSH_PACE_SEC=60 으로 시나리오 간격을 주거나 --only 로 나눠 실행할 것."
        )

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
