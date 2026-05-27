"""W7 백업 스크립트 단위 테스트.

전략:
- pg/es/minio 실제 호출은 stub/mock (호스트 docker 의존 회피)
- cleanup_old_backups, retention 로직 같은 순수 함수만 풀 테스트
- DR 리허설은 가짜 디렉터리 구조로 시뮬레이션
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import pytest


# 스크립트 디렉터리를 sys.path에 추가
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ============================================================
# backup_postgres
# ============================================================

class TestBackupPostgres:
    def test_cleanup_old_backups_removes_aged(self, tmp_path):
        import backup_postgres as bp

        old = tmp_path / "old.dump"
        new = tmp_path / "new.dump"
        old.write_text("x")
        new.write_text("y")
        # old 파일의 mtime을 60일 전으로
        sixty_days_ago = (dt.datetime.now() - dt.timedelta(days=60)).timestamp()
        os.utime(old, (sixty_days_ago, sixty_days_ago))

        removed = bp.cleanup_old_backups(tmp_path, retention_days=30)
        assert removed == 1
        assert not old.exists()
        assert new.exists()

    def test_cleanup_handles_missing_dir(self, tmp_path):
        import backup_postgres as bp
        missing = tmp_path / "does-not-exist"
        assert bp.cleanup_old_backups(missing, retention_days=30) == 0

    def test_main_skip_dump_returns_zero(self, tmp_path):
        import backup_postgres as bp
        rc = bp.main(["--skip-dump", "--output-dir", str(tmp_path)])
        assert rc == 0


# ============================================================
# backup_minio_mirror
# ============================================================

class TestBackupMinioMirror:
    def test_cleanup_old_files(self, tmp_path):
        import backup_minio_mirror as bm

        old = tmp_path / "sub" / "old.bin"
        new = tmp_path / "new.bin"
        old.parent.mkdir(parents=True)
        old.write_text("x")
        new.write_text("y")
        old_ts = (dt.datetime.now() - dt.timedelta(days=120)).timestamp()
        os.utime(old, (old_ts, old_ts))

        removed = bm.cleanup_old_files(tmp_path, retention_days=90)
        assert removed == 1
        assert not old.exists()


# ============================================================
# dr_restore_check
# ============================================================

class TestDrRestoreCheck:
    def test_pg_backup_recency_fresh(self, tmp_path):
        import dr_restore_check as dr
        f = tmp_path / "lloydk-20260527-020000.dump"
        f.write_text("x")
        result = dr.check_pg_backup_recency(tmp_path, hours=24)
        assert result.ok is True
        assert "latest=" in result.detail

    def test_pg_backup_recency_stale(self, tmp_path):
        import dr_restore_check as dr
        f = tmp_path / "old.dump"
        f.write_text("x")
        # 48h 전으로 마크
        old_ts = (dt.datetime.now() - dt.timedelta(hours=48)).timestamp()
        os.utime(f, (old_ts, old_ts))
        result = dr.check_pg_backup_recency(tmp_path, hours=24)
        assert result.ok is False

    def test_pg_backup_recency_empty_dir(self, tmp_path):
        import dr_restore_check as dr
        result = dr.check_pg_backup_recency(tmp_path, hours=24)
        assert result.ok is False
        assert "no .dump" in result.detail

    def test_pg_backup_recency_missing_dir(self, tmp_path):
        import dr_restore_check as dr
        missing = tmp_path / "missing"
        result = dr.check_pg_backup_recency(missing, hours=24)
        assert result.ok is False
        assert "missing" in result.detail

    def test_minio_mirror_recency_fresh(self, tmp_path):
        import dr_restore_check as dr
        f = tmp_path / "data" / "obj.bin"
        f.parent.mkdir(parents=True)
        f.write_text("x")
        result = dr.check_minio_mirror_recency(tmp_path, hours=168)
        assert result.ok is True

    def test_run_checks_aggregates_report(self, tmp_path):
        import dr_restore_check as dr
        pg_dir = tmp_path / "pg"
        pg_dir.mkdir()
        (pg_dir / "fresh.dump").write_text("x")
        mirror_dir = tmp_path / "minio"
        mirror_dir.mkdir()
        (mirror_dir / "obj").write_text("y")

        report = dr.run_checks(
            pg_dir=pg_dir,
            mirror_dir=mirror_dir,
            skip_infra=True,  # ES 호출 회피
        )
        # pg + minio 2개 통과, es는 가능성 둘 다 → all_ok 여부는 ES 상태에 의존
        assert any(c.name == "pg_backup_recency" and c.ok for c in report.checks)
        assert any(c.name == "minio_mirror_recency" and c.ok for c in report.checks)

    def test_main_writes_report_file(self, tmp_path):
        import dr_restore_check as dr
        pg_dir = tmp_path / "pg"
        pg_dir.mkdir()
        (pg_dir / "fresh.dump").write_text("x")
        report_dir = tmp_path / "reports"

        rc = dr.main([
            "--pg-dir", str(pg_dir),
            "--mirror-dir", str(tmp_path / "nope"),
            "--report-dir", str(report_dir),
            "--skip-infra",
            "--exit-zero-on-fail",  # mirror missing 실패해도 exit 0
        ])
        assert rc == 0
        reports = list(report_dir.glob("dr_check_*.json"))
        assert reports
        payload = json.loads(reports[0].read_text(encoding="utf-8"))
        assert "checks" in payload
        assert "all_ok" in payload


# ============================================================
# backup_es_snapshot (스크립트 import만 검증 — ES 실 호출은 mock 필요)
# ============================================================

class TestBackupEsSnapshot:
    def test_import_and_dry_run_without_es(self, monkeypatch):
        import backup_es_snapshot as bes
        # ES 없는 환경에서 ping False → exit 2, --dry-run이면 exit 0
        # 진짜 ES 클라이언트를 fake로 교체
        class FakeClient:
            def ping(self): return False
        monkeypatch.setattr(bes, "_get_client", lambda *a, **kw: FakeClient())
        rc = bes.main(["--dry-run"])
        assert rc == 0
