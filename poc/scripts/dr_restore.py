"""DR 실복구 실행기 — backup_postgres.py 로 만든 dump 를 실제로 복원한다.

dr_restore_check.py(백업 recency/인프라 readiness 점검)와 역할이 다르다: 이 스크립트는
**실제 pg_restore 를 수행**한다. RTO 4h 드릴(dr_drill.py)의 restore_* 단계가 호출한다.

fail-closed 원칙(안전하게 틀리고 명확히 멈춘다):
  - dump 부재 / pg_restore 미탑재 / 컨테이너 미가동 / 복원 실패 → 항상 non-zero.
  - --dry-run 은 '실 write 없이 전제조건만' 검증(dump 존재·docker·컨테이너·pg_restore 가용).
    전제 충족 시 0, 하나라도 미충족 시 non-zero — 성공으로 위장하지 않는다.

대상(--target):
  postgres : backups/pg 의 최신 *.dump 를 docker exec pg_restore 로 --clean 복원.
             (backup_postgres.py 가 `docker exec pg_dump -F c` 로 만든 custom-format dump 대상)
  storage  : backup_storage.py 가 만든 `backups/storage/storage-*.tar.gz`(최신)를 storage 경로로
             전개 복원(아카이브 없으면 평문 디렉터리 미러도 백-호환). MinIO 미사용 — 로컬FS 결정.
             주의: 원문은 at-rest 암호문 그대로라 STORAGE_ENCRYPTION_KEY 를 별도 보관해야 평문 복원.

종료 코드:
  0 = 복원(또는 dry-run 전제검증) 성공
  1 = 복원 대상 부재/노후 또는 복원 실패(재현 가능한 데이터 문제)
  2 = 전제 인프라 부재(docker/컨테이너/pg_restore 없음 — 환경 문제)

사용 예:
  # staging 드릴에서 실제 복원
  python scripts/dr_restore.py --target postgres --staging
  # CI/perf 에서 전제조건만(실 write 없음)
  python scripts/dr_restore.py --target postgres --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from dr_discovery import autodetect_container

logger = logging.getLogger("dr_restore")

# backup_postgres.py DEFAULT_PG_CONTAINER 와 정합(운영 dev compose 컨테이너명).
_PROD_PG_CONTAINER = "koipa-poc-postgres-1"
# dr_drill.py 가 `docker compose -p koipa-dr` 로 띄우는 staging 프로젝트의 pg 컨테이너.
_STAGING_PG_CONTAINER = "koipa-dr-postgres-1"


def _latest_dump(pg_dir: Path) -> Path | None:
    """backup_postgres.py 산출물(`{db}-YYYYMMDD-HHMMSS.dump`) 중 최신 파일."""
    if not pg_dir.exists():
        return None
    dumps = [f for f in pg_dir.glob("*.dump") if f.is_file()]
    if not dumps:
        return None
    return max(dumps, key=lambda f: f.stat().st_mtime)


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _container_running(name: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def restore_postgres(
    *, pg_dir: Path, container: str, db: str, user: str, dry_run: bool,
) -> int:
    dump = _latest_dump(pg_dir)
    if dump is None:
        logger.error("복원할 dump 없음: %s — backup_postgres.py 로 먼저 백업 필요", pg_dir)
        return 1
    logger.info("최신 dump: %s (%.1f MB)", dump.name, dump.stat().st_size / 1_048_576)

    if not _docker_available():
        logger.error("docker CLI 없음 — 복원 불가")
        return 2
    if not _container_running(container):
        logger.error("대상 컨테이너 미가동: %s (staging compose 를 먼저 기동)", container)
        return 2
    which = subprocess.run(
        ["docker", "exec", container, "sh", "-c", "command -v pg_restore"],
        capture_output=True, text=True,
    )
    if which.returncode != 0:
        logger.error("컨테이너에 pg_restore 없음: %s", container)
        return 2

    if dry_run:
        logger.info("[dry-run] 전제조건 충족 — dump·docker·컨테이너·pg_restore 모두 OK")
        return 0

    logger.info("pg_restore 시작 → %s/%s (--clean --if-exists)", container, db)
    with dump.open("rb") as fh:
        proc = subprocess.run(
            [
                "docker", "exec", "-i", container,
                "pg_restore", "-U", user, "-d", db,
                "--clean", "--if-exists", "--no-owner", "--no-privileges",
            ],
            stdin=fh, capture_output=True, text=False,
        )
    if proc.returncode != 0:
        logger.error("pg_restore 실패: %s", proc.stderr.decode(errors="replace")[-500:])
        return 1
    logger.info("pg_restore 완료: %s", db)
    return 0


def _latest_storage_archive(mirror_dir: Path) -> Path | None:
    """backup_storage.py 산출물(`storage-YYYYMMDD-HHMMSS.tar.gz`) 중 최신 아카이브."""
    if not mirror_dir.exists():
        return None
    arcs = [f for f in mirror_dir.glob("*.tar.gz") if f.is_file()]
    arcs += [f for f in mirror_dir.glob("*.tar") if f.is_file()]
    if not arcs:
        return None
    return max(arcs, key=lambda f: f.stat().st_mtime)


def _safe_extractall(tf: tarfile.TarFile, target_dir: Path) -> None:
    """경로 탈출(../, 절대경로) 멤버를 거부한 뒤 전개 — tar-slip 방어(fail-closed)."""
    base = target_dir.resolve()
    base_prefix = str(base) + os.sep
    for member in tf.getmembers():
        dest = (base / member.name).resolve()
        if dest != base and not str(dest).startswith(base_prefix):
            raise RuntimeError(f"아카이브에 안전하지 않은 경로: {member.name}")
    tf.extractall(target_dir)  # noqa: S202 — 멤버 경로 사전검증 완료


def restore_storage(*, mirror_dir: Path, target_dir: Path, dry_run: bool) -> int:
    if not mirror_dir.exists():
        logger.error("복원할 스토리지 미러 없음: %s", mirror_dir)
        return 1

    # 우선순위 1: backup_storage.py 아카이브(.tar.gz) — 시점별 보존 + retention.
    archive = _latest_storage_archive(mirror_dir)
    if archive is not None:
        logger.info("최신 스토리지 아카이브: %s (%.1f MB)",
                    archive.name, archive.stat().st_size / 1_048_576)
        if dry_run:
            logger.info("[dry-run] 전제조건 충족 — 아카이브 존재")
            return 0
        target_dir.mkdir(parents=True, exist_ok=True)
        # backup_storage.py 는 `tar -C /app/.storage .` 라 멤버 루트가 볼륨 내용 → target_dir 에 그대로 전개.
        with tarfile.open(archive, "r:*") as tf:
            _safe_extractall(tf, target_dir)
        logger.info("스토리지 복원 완료(아카이브) → %s", target_dir)
        return 0

    # 우선순위 2(백-호환): 아카이브가 없으면 평문 디렉터리 미러(구 방식)로 처리.
    files = [f for f in mirror_dir.rglob("*") if f.is_file()]
    if not files:
        logger.error("스토리지 미러가 비어 있음(.tar.gz/.tar/파일 모두 없음): %s", mirror_dir)
        return 1
    logger.info("스토리지 미러(디렉터리): %s (%d files)", mirror_dir, len(files))
    if dry_run:
        logger.info("[dry-run] 전제조건 충족 — 미러 존재·파일 %d개", len(files))
        return 0
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(mirror_dir, target_dir, dirs_exist_ok=True)
    logger.info("스토리지 복원 완료(디렉터리) → %s", target_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Koipa DR 실복구 실행기 (fail-closed)")
    p.add_argument("--target", required=True, choices=["postgres", "storage"])
    p.add_argument("--staging", action="store_true", help="staging(dr_drill) 컨테이너/경로 대상")
    p.add_argument("--dry-run", action="store_true", help="실 write 없이 전제조건만 검증")
    p.add_argument("--pg-dir", type=Path, default=Path("backups/pg"))
    p.add_argument("--mirror-dir", type=Path, default=Path("backups/storage"))
    p.add_argument("--target-dir", type=Path, default=Path(".storage"),
                   help="storage 복원 대상 경로")
    p.add_argument("--db", default="koipa")
    p.add_argument("--user", default="koipa")
    p.add_argument("--container", default=None,
                   help="pg 컨테이너명(미지정 시 --staging 여부로 자동 선택)")
    args = p.parse_args(argv)

    if args.target == "postgres":
        if args.container:
            container = args.container
        elif args.staging:
            container = _STAGING_PG_CONTAINER
        else:
            # 실복구(비-staging): 실행 중 postgres 컨테이너 자동탐지(airgap/dual 컨테이너명 상이) → 폴백은 dev 명.
            container = autodetect_container(("postgres",)) or _PROD_PG_CONTAINER
        return restore_postgres(
            pg_dir=args.pg_dir, container=container, db=args.db, user=args.user,
            dry_run=args.dry_run,
        )
    return restore_storage(
        mirror_dir=args.mirror_dir, target_dir=args.target_dir, dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
