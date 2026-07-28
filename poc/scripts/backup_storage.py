"""원문 스토리지(암호화 at-rest) 볼륨 백업 → `backups/storage/` (로컬FS, MinIO 미사용).

배경(왜 이 스크립트가 필요한가):
- 폐쇄망 배포는 원문을 named 볼륨 `/app/.storage`(LocalStorage + EncryptingStorage)에 둔다.
- backup_postgres.py 는 PG 만 덤프한다 → 이 볼륨이 유실되면 PG 를 복원해도 **원문은 영구 소실**
  (원문 read-back miss → 빈문서 분류, 무음). dr_restore.py 의 `--target storage` 는 이미
  `backups/storage` 미러를 복원 대상으로 기대하지만, 그 미러를 **생성하는 쪽이 없었다**. 여기서 만든다.

설계(backup_postgres.py 와 대칭):
- pg_dump 가 `docker exec ... pg_dump` 인 것과 동일하게, 실행 중 api/worker 컨테이너에서
  `docker exec ... tar` 로 마운트 경로(/app/.storage)를 아카이브한다(호스트 tar/볼륨명 불요).
- 산출물: `storage-YYYYMMDD-HHMMSS.tar.gz` (gzip tar, 볼륨 내용이 루트).
- 원문은 이미 at-rest 암호화라 아카이브도 암호문 그대로 — 복원 시 **암호화 키(.env
  STORAGE_ENCRYPTION_KEY)를 별도 보관**해야 평문 복원이 된다(아카이브만으론 복호 불가).
- 30일 이상 백업은 자동 정리(pg 와 동일 retention).
- fail-closed: 컨테이너 미가동 / tar 부재 / 빈 아카이브 → 항상 non-zero.

cron 등록은 backup_dr.py(오케스트레이터)가 pg+storage 를 한 번에 돌리는 것을 권장.
단독 사용 예:
  python scripts/backup_storage.py                         # 실행 중 스택 자동탐지
  python scripts/backup_storage.py --container lloydk-airgap-worker-1
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from dr_discovery import autodetect_container

logger = logging.getLogger("backup_storage")

DEFAULT_RETENTION_DAYS = 30
DEFAULT_OUTPUT_DIR = Path("backups/storage")   # dr_restore.py `--target storage` 기본 미러 경로와 정합
DEFAULT_MOUNT = "/app/.storage"                # WORKDIR=/app · LocalStorage root=.storage


def _ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def autodetect_storage_container() -> str | None:
    """스토리지 마운트(/app/.storage)를 가진 컨테이너(worker 우선, 없으면 api) 1개를 자동 선택.

    스택이 2개 이상(예: dual 테스트서버 jjw+cust)이면 모호 → None (명시 --container 필요).
    """
    return autodetect_container(("worker", "api"))


def _container_running(name: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True, text=True, check=False,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def run_storage_backup(*, container: str, mount: str, output_dir: Path) -> Path:
    """docker exec tar → 호스트 tar.gz. fail-closed(실패/빈 파일 → 삭제 후 예외)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"storage-{_ts()}.tar.gz"
    # -C <mount> . → 볼륨 '내용'이 아카이브 루트(복원 시 대상 디렉터리에 그대로 풀림).
    cmd = ["docker", "exec", container, "tar", "czf", "-", "-C", mount, "."]
    logger.info("running: %s → %s", " ".join(cmd), out_path)
    with out_path.open("wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"storage tar failed: {proc.stderr.decode(errors='replace')[-500:]}")
    if out_path.stat().st_size == 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError("storage tar produced empty file")
    return out_path


def cleanup_old_backups(directory: Path, retention_days: int) -> int:
    """retention_days 보다 오래된 파일 삭제. 반환: 삭제된 파일 수."""
    if not directory.exists():
        return 0
    cutoff_ts = (dt.datetime.now() - dt.timedelta(days=retention_days)).timestamp()
    removed = 0
    for f in directory.iterdir():
        if not f.is_file():
            continue
        if f.stat().st_mtime < cutoff_ts:
            f.unlink()
            removed += 1
            logger.info("cleaned: %s", f.name)
    return removed


def mirror_to_second_media(path: Path, mirror_dir: Path) -> Path:
    """두 번째 매체(별도 디스크/NAS 마운트)로 복사 — 폐쇄망 오프사이트(MinIO 미사용)."""
    mirror_dir.mkdir(parents=True, exist_ok=True)
    dest = mirror_dir / path.name
    shutil.copy2(path, dest)
    logger.info("mirrored: %s", dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Lloydk 원문 스토리지 볼륨 백업(로컬FS, fail-closed)")
    p.add_argument("--container", default=None,
                   help="api/worker 컨테이너명(미지정 시 실행 중 스택 자동탐지; 2스택+면 모호→명시 필요)")
    p.add_argument("--mount", default=DEFAULT_MOUNT, help="컨테이너 내 스토리지 경로")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--mirror-dir", type=Path, default=None,
                   help="두 번째 매체 복사 경로(별도 디스크/NAS). 지정 시 오프사이트 사본 생성.")
    p.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    p.add_argument("--skip-dump", action="store_true", help="dry-run: 백업 건너뜀(전제만 확인)")
    args = p.parse_args(argv)

    try:
        if shutil.which("docker") is None:
            logger.error("docker CLI not found in PATH")
            return 2

        if args.skip_dump:
            logger.info("--skip-dump: dry-run only")
            return 0

        container = args.container or autodetect_storage_container()
        if container is None:
            logger.error(
                "스토리지 컨테이너 자동탐지 실패(미가동 또는 2스택+ 모호) — --container 로 명시하세요"
            )
            return 2
        if not _container_running(container):
            logger.error("대상 컨테이너 미가동: %s", container)
            return 2

        out_path = run_storage_backup(
            container=container, mount=args.mount, output_dir=args.output_dir,
        )
        logger.info("storage backup OK: %s (%d bytes)", out_path, out_path.stat().st_size)

        if args.mirror_dir is not None:
            mirror_to_second_media(out_path, args.mirror_dir)

        removed = cleanup_old_backups(args.output_dir, args.retention_days)
        if removed:
            logger.info("retention: removed %d old files", removed)

        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("storage backup failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
