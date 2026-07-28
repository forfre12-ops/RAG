"""DR 백업 오케스트레이터 — 실행 중 모든 lloydk 스택의 PG + 원문 스토리지를 한 번에 백업.

배경:
- 이전엔 backup_postgres.py 만 있었고 (a) PG만 덤프(원문 스토리지 백업 없음), (b) 컨테이너
  기본값이 dev 명(lloydk-poc-*)이라 airgap/dual 에서 빗나가고, (c) cron 에 배선돼 있지 않았다.
- 이 스크립트가 그 3가지를 한 진입점으로 묶는다: 스택 자동탐지 → PG 덤프 + 스토리지 아카이브 →
  (선택) 두 번째 매체 사본 → retention → JSON 리포트. **fail-closed**(하나라도 실패 시 non-zero)
  라 cron 메일/모니터링이 실패를 놓치지 않는다.

경로 규칙(복구 도구와 정합):
- 단일 스택(예: 고객사 airgap 1대) → backups/pg · backups/storage
  (dr_restore.py / dr_drill.py 의 기본 경로라 별도 --pg-dir 없이 바로 복구/드릴 가능).
- 2스택+(예: dual 테스트서버 jjw+cust) → backups/<project>/{pg,storage}
  (복구 시 dr_restore.py --pg-dir/--mirror-dir 로 스택을 지정).

스케줄링(호스트 cron — worker 컨테이너엔 docker 소켓이 없어 beat 로는 실행 불가):
  # crontab -e (매일 02:00, 로그 append)
  0 2 * * * cd /opt/lloydk/poc && /usr/bin/python scripts/backup_dr.py >> /var/log/lloydk-dr-backup.log 2>&1
또는 systemd 타이머: infra/systemd/lloydk-dr-backup.{service,timer} 참조.

두 번째 매체(오프사이트) 사본:
  python scripts/backup_dr.py --second-media-dir /mnt/backup-nas
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import shutil
import sys
from pathlib import Path

import backup_postgres as bp
import backup_storage as bs
from dr_discovery import group_stacks

logger = logging.getLogger("backup_dr")

DEFAULT_RETENTION_DAYS = 30


def _ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def plan_targets(stacks: dict[str, dict[str, str]], backups_root: Path) -> list[dict]:
    """스택별 백업 대상(컨테이너·출력경로)을 산출. 단일 스택은 복구 기본경로에 정합."""
    single = len(stacks) == 1
    targets = []
    for project, names in sorted(stacks.items()):
        if single:
            pg_dir = backups_root / "pg"
            storage_dir = backups_root / "storage"
        else:
            pg_dir = backups_root / project / "pg"
            storage_dir = backups_root / project / "storage"
        targets.append({
            "project": project,
            "pg_container": names["postgres"],
            "storage_container": names["storage"],
            "pg_dir": pg_dir,
            "storage_dir": storage_dir,
        })
    return targets


def _backup_one(t: dict, args) -> dict:
    """한 스택의 pg + storage 백업. 각 항목 status(OK/FAIL/PLAN) 를 담은 dict 반환."""
    entry: dict = {"project": t["project"], "pg": None, "storage": None}

    # ── PostgreSQL ──
    try:
        if args.dry_run:
            entry["pg"] = {"status": "PLAN", "container": t["pg_container"], "dir": str(t["pg_dir"])}
        else:
            dump = bp.run_pg_dump(
                container=t["pg_container"], db=args.db, user=args.user, output_dir=t["pg_dir"],
            )
            if args.second_media_dir is not None:
                bp.mirror_to_second_media(dump, args.second_media_dir / t["project"] / "pg")
            bp.cleanup_old_backups(t["pg_dir"], args.retention_days)
            entry["pg"] = {"status": "OK", "file": dump.name, "bytes": dump.stat().st_size}
    except Exception as exc:  # noqa: BLE001
        entry["pg"] = {"status": "FAIL", "error": str(exc)}
        logger.error("[%s] pg 백업 실패: %s", t["project"], exc)

    # ── 원문 스토리지 ──
    try:
        if args.dry_run:
            entry["storage"] = {"status": "PLAN", "container": t["storage_container"], "dir": str(t["storage_dir"])}
        else:
            arc = bs.run_storage_backup(
                container=t["storage_container"], mount=args.mount, output_dir=t["storage_dir"],
            )
            if args.second_media_dir is not None:
                bs.mirror_to_second_media(arc, args.second_media_dir / t["project"] / "storage")
            bs.cleanup_old_backups(t["storage_dir"], args.retention_days)
            entry["storage"] = {"status": "OK", "file": arc.name, "bytes": arc.stat().st_size}
    except Exception as exc:  # noqa: BLE001
        entry["storage"] = {"status": "FAIL", "error": str(exc)}
        logger.error("[%s] storage 백업 실패: %s", t["project"], exc)

    return entry


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Lloydk DR 백업 오케스트레이터(pg+storage, fail-closed)")
    p.add_argument("--backups-root", type=Path, default=Path("backups"))
    p.add_argument("--second-media-dir", type=Path, default=None,
                   help="오프사이트 사본 루트(별도 디스크/NAS). 스택별 하위폴더에 사본.")
    p.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    p.add_argument("--db", default="lloydk")
    p.add_argument("--user", default="lloydk")
    p.add_argument("--mount", default="/app/.storage", help="컨테이너 내 스토리지 경로")
    p.add_argument("--report-dir", type=Path, default=Path("reports/dr"))
    p.add_argument("--dry-run", action="store_true", help="탐지·계획만(실 백업 없음)")
    args = p.parse_args(argv)

    if shutil.which("docker") is None:
        logger.error("docker CLI not found in PATH")
        return 2

    stacks = group_stacks()
    if not stacks:
        logger.error("실행 중 lloydk 스택 없음(postgres + worker/api 동시 필요) — 백업 대상 없음")
        return 2

    targets = plan_targets(stacks, args.backups_root)
    logger.info("백업 대상 스택 %d개: %s", len(targets), ", ".join(t["project"] for t in targets))

    results = [_backup_one(t, args) for t in targets]

    def _entry_ok(e: dict, key: str) -> bool:
        return (e[key] or {}).get("status") in ("OK", "PLAN")

    all_ok = all(_entry_ok(e, "pg") and _entry_ok(e, "storage") for e in results)

    report = {
        "ts": _ts(),
        "dry_run": args.dry_run,
        "all_ok": all_ok,
        "retention_days": args.retention_days,
        "second_media": str(args.second_media_dir) if args.second_media_dir else None,
        "stacks": results,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"backup_{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("리포트: %s  all_ok=%s", report_path, all_ok)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
