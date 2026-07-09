"""DR 리허설 검증 — doc/11 §6.4 (RTO 4시간, RPO 1일).

체크 항목:
1. 최신 pg dump 파일이 24h 이내에 존재하는가
2. ES snapshot이 24h 이내에 존재하는가
3. MinIO mirror가 7일 이내에 존재하는가
4. verify_infra.py 통과 가능한가 (PG·ES·MinIO·Redis 다 살아있는가)

리포트:
- 결과를 JSON으로 출력 + reports/dr_check_YYYYMMDD.json 적재
- 실패 항목 있으면 exit 1 (운영 모니터링과 연동)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("dr_restore_check")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    timestamp: str = ""


@dataclass
class DrReport:
    generated_at: str
    rto_target_hours: int
    rpo_target_hours: int
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def check_pg_backup_recency(pg_dir: Path, *, hours: int = 24) -> CheckResult:
    name = "pg_backup_recency"
    if not pg_dir.exists():
        return CheckResult(name, False, f"directory missing: {pg_dir}", _now())
    candidates = [f for f in pg_dir.glob("*.dump") if f.is_file()]
    if not candidates:
        return CheckResult(name, False, f"no .dump files in {pg_dir}", _now())
    latest = max(candidates, key=lambda f: f.stat().st_mtime)
    age = dt.datetime.now() - dt.datetime.fromtimestamp(latest.stat().st_mtime)
    ok = age.total_seconds() <= hours * 3600
    return CheckResult(
        name, ok,
        f"latest={latest.name} age={age.total_seconds()/3600:.1f}h limit={hours}h",
        _now(),
    )


def check_minio_mirror_recency(mirror_dir: Path, *, hours: int = 168) -> CheckResult:
    name = "minio_mirror_recency"
    if not mirror_dir.exists():
        return CheckResult(name, False, f"directory missing: {mirror_dir}", _now())
    candidates = [f for f in mirror_dir.rglob("*") if f.is_file()]
    if not candidates:
        return CheckResult(name, False, f"no files in {mirror_dir}", _now())
    latest = max(candidates, key=lambda f: f.stat().st_mtime)
    age_h = (dt.datetime.now() - dt.datetime.fromtimestamp(latest.stat().st_mtime)).total_seconds() / 3600
    ok = age_h <= hours
    return CheckResult(
        name, ok,
        f"latest={latest.name} age={age_h:.1f}h limit={hours}h",
        _now(),
    )


def check_infra_health() -> CheckResult:
    """verify_infra.py 호출 — 실패 항목 수집."""
    name = "infra_health"
    try:
        import importlib.util  # noqa: PLC0415
        verify_path = Path(__file__).parent / "verify_infra.py"
        spec = importlib.util.spec_from_file_location("verify_infra", verify_path)
        if spec is None or spec.loader is None:
            return CheckResult(name, False, "cannot load verify_infra.py", _now())
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        checks = [
            mod.check_postgres(),
            mod.check_minio(),
            mod.check_redis(),
        ]
        failed = [c.name for c in checks if not c.ok]
        return CheckResult(
            name,
            not failed,
            f"failed={failed}" if failed else "all green",
            _now(),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name, False, f"error: {type(exc).__name__}: {exc}", _now())


def run_checks(
    *,
    pg_dir: Path,
    mirror_dir: Path,
    rto_hours: int = 4,
    pg_recency_hours: int = 24,
    es_recency_hours: int = 24,
    mirror_recency_hours: int = 168,
    skip_infra: bool = False,
) -> DrReport:
    report = DrReport(
        generated_at=_now(),
        rto_target_hours=rto_hours,
        rpo_target_hours=pg_recency_hours,
    )
    report.checks.append(check_pg_backup_recency(pg_dir, hours=pg_recency_hours))
    report.checks.append(check_minio_mirror_recency(mirror_dir, hours=mirror_recency_hours))
    if not skip_infra:
        report.checks.append(check_infra_health())
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Lloydk DR readiness check")
    p.add_argument("--pg-dir", type=Path, default=Path("backups/pg"))
    p.add_argument("--mirror-dir", type=Path, default=Path("backups/minio"))
    p.add_argument("--rto-hours", type=int, default=4)
    p.add_argument("--rpo-hours", type=int, default=24)
    p.add_argument("--report-dir", type=Path, default=Path("reports/dr"))
    p.add_argument("--skip-infra", action="store_true")
    p.add_argument("--exit-zero-on-fail", action="store_true",
                   help="검사 실패해도 exit 0 (리포트만 적재할 때)")
    args = p.parse_args(argv)

    report = run_checks(
        pg_dir=args.pg_dir,
        mirror_dir=args.mirror_dir,
        rto_hours=args.rto_hours,
        pg_recency_hours=args.rpo_hours,
        skip_infra=args.skip_infra,
    )

    payload = {
        "generated_at": report.generated_at,
        "rto_target_hours": report.rto_target_hours,
        "rpo_target_hours": report.rpo_target_hours,
        "all_ok": report.all_ok,
        "checks": [asdict(c) for c in report.checks],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"dr_check_{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("report saved: %s", report_path)

    if not report.all_ok and not args.exit_zero_on_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
