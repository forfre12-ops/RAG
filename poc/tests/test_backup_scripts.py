"""W7 백업 스크립트 단위 테스트.

전략:
- pg/es/minio 실제 호출은 stub/mock (호스트 docker 의존 회피)
- cleanup_old_backups, retention 로직 같은 순수 함수만 풀 테스트
- DR 리허설은 가짜 디렉터리 구조로 시뮬레이션
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import sys
import tarfile
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
        f = tmp_path / "koipa-20260527-020000.dump"
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
# dr_discovery — 컨테이너 자동탐지 (docker 미의존, 리스트 주입)
# ============================================================

class TestDrDiscovery:
    _CONTAINERS = [
        {"name": "koipa-airgap-postgres-1", "project": "koipa-airgap", "service": "postgres"},
        {"name": "koipa-airgap-worker-1", "project": "koipa-airgap", "service": "worker"},
        {"name": "koipa-airgap-api-1", "project": "koipa-airgap", "service": "api"},
    ]
    _DUAL = _CONTAINERS + [
        {"name": "koipa-cust-postgres-1", "project": "koipa-cust", "service": "postgres"},
        {"name": "koipa-cust-api-1", "project": "koipa-cust", "service": "api"},
    ]

    def test_autodetect_pg_single_stack(self):
        import dr_discovery as d
        assert d.autodetect_container(("postgres",), self._CONTAINERS) == "koipa-airgap-postgres-1"

    def test_autodetect_storage_prefers_worker(self):
        import dr_discovery as d
        # worker 가 api 보다 우선
        assert d.autodetect_container(("worker", "api"), self._CONTAINERS) == "koipa-airgap-worker-1"

    def test_autodetect_storage_falls_back_to_api(self):
        import dr_discovery as d
        only_api = [c for c in self._CONTAINERS if c["service"] in ("postgres", "api")]
        assert d.autodetect_container(("worker", "api"), only_api) == "koipa-airgap-api-1"

    def test_autodetect_ambiguous_two_stacks_returns_none(self):
        import dr_discovery as d
        # dual = 2 스택 → 모호 → None (명시 필요)
        assert d.autodetect_container(("postgres",), self._DUAL) is None

    def test_autodetect_empty_returns_none(self):
        import dr_discovery as d
        assert d.autodetect_container(("postgres",), []) is None

    def test_group_stacks_single(self):
        import dr_discovery as d
        stacks = d.group_stacks(self._CONTAINERS)
        assert set(stacks) == {"koipa-airgap"}
        assert stacks["koipa-airgap"]["postgres"] == "koipa-airgap-postgres-1"
        assert stacks["koipa-airgap"]["storage"] == "koipa-airgap-worker-1"  # worker 우선

    def test_group_stacks_dual(self):
        import dr_discovery as d
        stacks = d.group_stacks(self._DUAL)
        assert set(stacks) == {"koipa-airgap", "koipa-cust"}
        assert stacks["koipa-cust"]["storage"] == "koipa-cust-api-1"  # cust 는 api 만

    def test_group_stacks_skips_incomplete(self):
        import dr_discovery as d
        # postgres 만 있고 api/worker 없는 스택은 제외(스토리지 백업 불가)
        pg_only = [{"name": "koipa-x-postgres-1", "project": "koipa-x", "service": "postgres"}]
        assert d.group_stacks(pg_only) == {}


# ============================================================
# backup_storage — retention + skip-dump dry-run
# ============================================================

class TestBackupStorage:
    def test_cleanup_old_backups_removes_aged(self, tmp_path):
        import backup_storage as bs
        old = tmp_path / "storage-old.tar.gz"
        new = tmp_path / "storage-new.tar.gz"
        old.write_text("x")
        new.write_text("y")
        aged = (dt.datetime.now() - dt.timedelta(days=45)).timestamp()
        os.utime(old, (aged, aged))
        removed = bs.cleanup_old_backups(tmp_path, retention_days=30)
        assert removed == 1
        assert not old.exists() and new.exists()

    def test_main_skip_dump_returns_zero(self, tmp_path, monkeypatch):
        import backup_storage as bs
        # docker 미설치 환경에서도 skip-dump 는 0 (which docker 통과시켜 skip 분기 도달)
        monkeypatch.setattr(bs.shutil, "which", lambda _n: "/usr/bin/docker")
        rc = bs.main(["--skip-dump", "--output-dir", str(tmp_path)])
        assert rc == 0

    def test_main_no_docker_returns_2(self, tmp_path, monkeypatch):
        import backup_storage as bs
        monkeypatch.setattr(bs.shutil, "which", lambda _n: None)
        rc = bs.main(["--output-dir", str(tmp_path)])
        assert rc == 2


# ============================================================
# backup_dr — 오케스트레이터 계획/집계 (docker 미의존)
# ============================================================

class TestBackupDr:
    def test_plan_targets_single_stack_uses_default_paths(self, tmp_path):
        import backup_dr as bdr
        stacks = {"koipa-airgap": {"postgres": "pg1", "storage": "wk1"}}
        targets = bdr.plan_targets(stacks, tmp_path)
        assert len(targets) == 1
        t = targets[0]
        # 단일 스택 → backups/pg · backups/storage (복구 기본경로 정합, project 하위폴더 없음)
        assert t["pg_dir"] == tmp_path / "pg"
        assert t["storage_dir"] == tmp_path / "storage"

    def test_plan_targets_dual_uses_project_subdirs(self, tmp_path):
        import backup_dr as bdr
        stacks = {
            "koipa-jjw": {"postgres": "pgj", "storage": "wkj"},
            "koipa-cust": {"postgres": "pgc", "storage": "wkc"},
        }
        targets = bdr.plan_targets(stacks, tmp_path)
        assert len(targets) == 2
        dirs = {t["project"]: t["pg_dir"] for t in targets}
        assert dirs["koipa-jjw"] == tmp_path / "koipa-jjw" / "pg"
        assert dirs["koipa-cust"] == tmp_path / "koipa-cust" / "pg"

    def test_main_dry_run_reports_plan(self, tmp_path, monkeypatch):
        import backup_dr as bdr
        monkeypatch.setattr(bdr.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(bdr, "group_stacks",
                            lambda: {"koipa-airgap": {"postgres": "pg1", "storage": "wk1"}})
        rc = bdr.main([
            "--dry-run", "--backups-root", str(tmp_path / "b"),
            "--report-dir", str(tmp_path / "r"),
        ])
        assert rc == 0
        reports = list((tmp_path / "r").glob("backup_*.json"))
        assert reports
        payload = json.loads(reports[0].read_text(encoding="utf-8"))
        assert payload["all_ok"] is True
        assert payload["stacks"][0]["pg"]["status"] == "PLAN"

    def test_main_no_stacks_returns_2(self, tmp_path, monkeypatch):
        import backup_dr as bdr
        monkeypatch.setattr(bdr.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(bdr, "group_stacks", lambda: {})
        rc = bdr.main(["--report-dir", str(tmp_path / "r")])
        assert rc == 2

    def test_main_backup_failure_is_fail_closed(self, tmp_path, monkeypatch):
        import backup_dr as bdr
        monkeypatch.setattr(bdr.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(bdr, "group_stacks",
                            lambda: {"koipa-airgap": {"postgres": "pg1", "storage": "wk1"}})

        def _boom(**_kw):
            raise RuntimeError("simulated backup failure")

        monkeypatch.setattr(bdr.bp, "run_pg_dump", _boom)
        monkeypatch.setattr(bdr.bs, "run_storage_backup", _boom)
        rc = bdr.main([
            "--backups-root", str(tmp_path / "b"), "--report-dir", str(tmp_path / "r"),
        ])
        assert rc == 1  # 하나라도 실패 → non-zero (fail-closed)
        payload = json.loads(next((tmp_path / "r").glob("backup_*.json")).read_text(encoding="utf-8"))
        assert payload["all_ok"] is False
        assert payload["stacks"][0]["pg"]["status"] == "FAIL"


# ============================================================
# dr_restore — 스토리지 아카이브 복원 + tar-slip 방어
# ============================================================

def _make_storage_archive(path: Path, files: dict[str, bytes]) -> None:
    """backup_storage.py 형식(멤버 루트=볼륨 내용)의 tar.gz 를 만든다."""
    with tarfile.open(path, "w:gz") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))


class TestDrRestoreStorage:
    def test_latest_archive_picks_newest(self, tmp_path):
        import dr_restore as dr
        a = tmp_path / "storage-1.tar.gz"
        b = tmp_path / "storage-2.tar.gz"
        _make_storage_archive(a, {"./x": b"1"})
        _make_storage_archive(b, {"./y": b"2"})
        older = (dt.datetime.now() - dt.timedelta(hours=5)).timestamp()
        os.utime(a, (older, older))
        assert dr._latest_storage_archive(tmp_path) == b

    def test_restore_storage_extracts_archive(self, tmp_path):
        import dr_restore as dr
        mirror = tmp_path / "mirror"
        mirror.mkdir()
        _make_storage_archive(mirror / "storage-1.tar.gz", {"./a.txt": b"hello", "./sub/b.txt": b"world"})
        target = tmp_path / "restored"
        rc = dr.restore_storage(mirror_dir=mirror, target_dir=target, dry_run=False)
        assert rc == 0
        assert (target / "a.txt").read_bytes() == b"hello"
        assert (target / "sub" / "b.txt").read_bytes() == b"world"

    def test_restore_storage_dry_run_ok(self, tmp_path):
        import dr_restore as dr
        mirror = tmp_path / "mirror"
        mirror.mkdir()
        _make_storage_archive(mirror / "storage-1.tar.gz", {"./a.txt": b"x"})
        rc = dr.restore_storage(mirror_dir=mirror, target_dir=tmp_path / "t", dry_run=True)
        assert rc == 0
        assert not (tmp_path / "t").exists()  # dry-run 은 write 없음

    def test_restore_storage_missing_mirror_fails(self, tmp_path):
        import dr_restore as dr
        rc = dr.restore_storage(mirror_dir=tmp_path / "nope", target_dir=tmp_path / "t", dry_run=False)
        assert rc == 1

    def test_restore_storage_backcompat_dir_mirror(self, tmp_path):
        import dr_restore as dr
        # 아카이브가 없고 평문 파일만 있으면 구 방식(copytree)로 복원
        mirror = tmp_path / "mirror"
        (mirror / "sub").mkdir(parents=True)
        (mirror / "sub" / "f.bin").write_bytes(b"z")
        target = tmp_path / "restored"
        rc = dr.restore_storage(mirror_dir=mirror, target_dir=target, dry_run=False)
        assert rc == 0
        assert (target / "sub" / "f.bin").read_bytes() == b"z"

    def test_safe_extract_rejects_path_traversal(self, tmp_path):
        import dr_restore as dr
        mirror = tmp_path / "mirror"
        mirror.mkdir()
        _make_storage_archive(mirror / "storage-evil.tar.gz", {"../evil.txt": b"pwn"})
        with pytest.raises(RuntimeError, match="안전하지 않은"):
            dr.restore_storage(mirror_dir=mirror, target_dir=tmp_path / "t", dry_run=False)
        assert not (tmp_path / "evil.txt").exists()


# ============================================================
# dr_restore_check — 원문 스토리지 백업 신선도
# ============================================================

class TestStorageRecency:
    def test_storage_recency_fresh(self, tmp_path):
        import dr_restore_check as dr
        (tmp_path / "storage-20260729-020000.tar.gz").write_text("x")
        result = dr.check_storage_backup_recency(tmp_path, hours=24)
        assert result.ok is True
        assert "latest=" in result.detail

    def test_storage_recency_stale(self, tmp_path):
        import dr_restore_check as dr
        f = tmp_path / "storage-old.tar.gz"
        f.write_text("x")
        old_ts = (dt.datetime.now() - dt.timedelta(hours=48)).timestamp()
        os.utime(f, (old_ts, old_ts))
        assert dr.check_storage_backup_recency(tmp_path, hours=24).ok is False

    def test_storage_recency_empty_dir(self, tmp_path):
        import dr_restore_check as dr
        result = dr.check_storage_backup_recency(tmp_path, hours=24)
        assert result.ok is False
        assert "no storage archive" in result.detail

    def test_storage_recency_missing_dir(self, tmp_path):
        import dr_restore_check as dr
        result = dr.check_storage_backup_recency(tmp_path / "missing", hours=24)
        assert result.ok is False
        assert "missing" in result.detail

    def test_run_checks_includes_storage_when_dir_given(self, tmp_path):
        import dr_restore_check as dr
        pg = tmp_path / "pg"; pg.mkdir(); (pg / "fresh.dump").write_text("x")
        mirror = tmp_path / "minio"; mirror.mkdir(); (mirror / "o").write_text("y")
        storage = tmp_path / "storage"; storage.mkdir(); (storage / "storage-1.tar.gz").write_text("z")
        report = dr.run_checks(pg_dir=pg, mirror_dir=mirror, storage_dir=storage, skip_infra=True)
        assert any(c.name == "storage_backup_recency" and c.ok for c in report.checks)

    def test_run_checks_omits_storage_when_none(self, tmp_path):
        import dr_restore_check as dr
        pg = tmp_path / "pg"; pg.mkdir(); (pg / "fresh.dump").write_text("x")
        mirror = tmp_path / "minio"; mirror.mkdir(); (mirror / "o").write_text("y")
        report = dr.run_checks(pg_dir=pg, mirror_dir=mirror, storage_dir=None, skip_infra=True)
        assert not any(c.name == "storage_backup_recency" for c in report.checks)
