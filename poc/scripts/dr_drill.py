"""P2-C6: DR 리허설 자동화 스크립트 — RTO 4시간 검증.

실측 가이드. 분기 1회 staging 환경에서 실행 권장.

단계:
1. 최신 pg/storage 백업 위치 확인 (폐쇄망 저장소=로컬FS, MinIO 미사용)
2. staging 컨테이너 기동 (별도 compose project)
3. dr_restore.py 로 실복구 (pg_restore + 로컬FS 미러) — fail-closed
4. 핵심 read·write 시나리오 5개 검증
5. 시간 측정 + 리포트 산출
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


STAGES = [
    "find_latest_backups",
    "spin_up_staging",
    "restore_postgres",
    "restore_storage",   # 폐쇄망 원문 저장소=로컬FS(file://). MinIO 미사용이라 storage 복원.
    "smoke_classify",
    "smoke_guide",
    "smoke_async_batch",
    "smoke_audit_chain",
    "smoke_kpi_endpoint",
    "teardown",
]


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def run_stage(name: str, dry_run: bool, cmd: list[str] | None = None) -> dict:
    t0 = time.time()
    error = None
    if dry_run:
        status = "SKIPPED"
    elif cmd:
        status = "RUN"
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            status = "PASS" if r.returncode == 0 else "FAIL"
            error = r.stderr[-500:] if r.returncode else None
        except Exception as e:  # noqa: BLE001
            status = "ERROR"
            error = str(e)
    else:
        # cmd 없음 = 자동 검증 러너 부재 → MANUAL(수동/스크립트 검증 필요). PASS 로 위장하지 않는다:
        # 과거엔 print() 스텁이 returncode 0 → PASS 로 기록돼 /guide·async batch 가 깨져도 드릴이
        # '검증됨'을 주장하는 fake-green 이었다. MANUAL 은 passed 집계에서 제외(아래 main).
        status = "MANUAL"
    elapsed = time.time() - t0
    return {
        "stage": name,
        "status": status,
        "elapsed_sec": round(elapsed, 2),
        "ts": _now(),
        "error": error,
    }


def stage_commands(staging_compose: str) -> dict[str, list[str] | None]:
    return {
        "find_latest_backups": ["ls", "-la", "backups/"],
        "spin_up_staging": ["docker", "compose", "-p", "lloydk-dr", "-f", staging_compose, "up", "-d"],
        # 실복구는 dr_restore.py(실 pg_restore, fail-closed) — dr_restore_check.py 는 recency
        # 점검일 뿐 복원을 하지 않는다. 과거엔 존재하지 않는 --target/--staging 플래그로 호출해
        # argparse 오류(exit 2)로 항상 죽었다.
        "restore_postgres": ["python", "scripts/dr_restore.py", "--target", "postgres", "--staging"],
        "restore_storage": ["python", "scripts/dr_restore.py", "--target", "storage", "--staging"],
        # p5_e2e_smoke.py 는 --mode {http,inproc} 만 허용(staging 은 argparse exit 2 로 항상 죽음) → http.
        "smoke_classify": ["python", "scripts/p5_e2e_smoke.py", "--mode", "http"],
        # /guide·async batch 실 스테이징 검증 러너 미구현 → 자동 검증 불가. print 스텁으로 PASS 위장하지
        # 않고 MANUAL 로 기록(수동/스크립트 검증 필요). 러너 생기면 실 명령으로 교체.
        "smoke_guide": None,
        "smoke_async_batch": None,
        "smoke_audit_chain": ["python", "-c",
            "from lloydk.services.audit_chain import verify_chain; r=verify_chain(); print(r)"],
        "smoke_kpi_endpoint": ["curl", "-fsS", "http://staging:8000/api/v1/metrics"],
        "teardown": ["docker", "compose", "-p", "lloydk-dr", "-f", staging_compose, "down", "-v"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="DR 리허설 — RTO 4h 검증")
    parser.add_argument("--dry-run", action="store_true", help="명령 실행 없이 단계 시뮬레이션만")
    parser.add_argument("--staging-compose", default="docker-compose.dr-staging.yml")
    parser.add_argument("--report-out", type=Path, default=Path("reports/dr/dr_drill_latest.json"))
    parser.add_argument("--rto-seconds", type=int, default=4 * 3600, help="RTO 목표(초). 초과 시 비-zero exit.")
    args = parser.parse_args()

    cmds = stage_commands(args.staging_compose)
    results = []
    overall_start = time.time()

    for stage in STAGES:
        cmd = cmds.get(stage)
        r = run_stage(stage, args.dry_run, cmd)
        results.append(r)
        print(f"[{r['status']}] {stage}  {r['elapsed_sec']}s")
        if r["status"] in ("FAIL", "ERROR") and stage not in ("teardown",):
            print(f"[stop] {stage} {r['status']} — aborting drill (run teardown still)", file=sys.stderr)
            # teardown은 강제 실행
            if stage != "teardown":
                t_cmd = cmds.get("teardown")
                results.append(run_stage("teardown", args.dry_run, t_cmd))
            break

    total_elapsed = time.time() - overall_start
    within_rto = total_elapsed <= args.rto_seconds
    # DR 판정: 어떤 단계라도 FAIL/ERROR 면 실패. 과거엔 within_rto 만 봐서, 복원이 실패해도
    # 조기 abort 로 경과시간이 짧으면 exit 0(fake-green)이었다. teardown 실패는 판정에서 제외.
    stage_failures = [
        r["stage"] for r in results
        if r["status"] in ("FAIL", "ERROR") and r["stage"] != "teardown"
    ]
    manual_stages = [r["stage"] for r in results if r["status"] == "MANUAL"]
    passed = within_rto and not stage_failures
    report = {
        "ts": _now(),
        "dry_run": args.dry_run,
        "rto_target_sec": args.rto_seconds,
        "total_elapsed_sec": round(total_elapsed, 2),
        "within_rto": within_rto,
        "stage_failures": stage_failures,
        # 자동 검증 불가(러너 부재) 단계 — passed 를 막지는 않으나 '검증됨'이 아님을 정직히 노출.
        "manual_stages": manual_stages,
        "auto_verified_stages": [r["stage"] for r in results if r["status"] == "PASS"],
        "passed": passed,
        "stages": results,
    }
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {args.report_out}")
    print(f"Total: {total_elapsed:.1f}s  RTO {args.rto_seconds}s  within={within_rto}  "
          f"failures={stage_failures or 'none'}  manual={manual_stages or 'none'}  -> {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
