"""새 서버에 MariaDB 로 배포할 수 있는가 — 오버레이 계약 잠금.

왜(2026-09-07). 배포 정본은 docker-compose.airgap.yml 이고 그것은 PostgreSQL 이다
(커밋 5c7ed034 — "dev 기본 DB 를 MariaDB 로 넘긴다, **운영 배포는 그대로 PostgreSQL**").
KL 포털이 MariaDB 라 그쪽에 맞춘 배포가 필요해졌는데, **배포용 compose 가 PG 뿐**이었다.

정본을 복사해 두 벌로 만들면 한쪽만 고쳐지는 날이 온다. 바뀌는 것만 담은 오버레이를 두고
이 시험이 그 계약을 잠근다:

  ① MariaDB 구성에는 postgres 가 없고 mariadb 가 있다
  ② api·worker·beat 의 DATABASE_URL 이 전부 mariadb 를 가리킨다
  ③ **기존 PG 경로가 안 깨진다** — 오버레이 없이 정본만 쓰면 종전 그대로다

⚠ docker 없이도 도는 시험이다. compose 렌더링은 docker 가 있을 때만 하고, 없으면
  파일 자체의 계약만 본다 — CI 에 docker 가 없다고 이 계약이 안 지켜져도 되는 것은 아니다.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
BASE = _POC / "docker-compose.airgap.yml"
OVERLAY = _POC / "docker-compose.airgap.mariadb.yml"

_DB_SERVICES = ("api", "worker", "beat")


def _render(*files: Path, tmp_path: Path) -> str:
    """격리된 디렉터리에서 compose 를 렌더링한다.

    리포에 .env 를 만들지 않는다 — config.py(pydantic-settings)가 그 파일을 읽어
    **다른 세션의 로컬 설정을 바꾼다.** 임시 폴더에 복사해서 돌린다.
    """
    for f in files:
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=unused\nMARIADB_PASSWORD=pw\nMARIADB_ROOT_PASSWORD=rootpw\n"
        "IMAGE_TAG=latest\n",
        encoding="utf-8",
    )
    args = ["docker", "compose"]
    for f in files:
        args += ["-f", f.name]
    args += ["config"]
    proc = subprocess.run(
        args, cwd=tmp_path, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail(f"compose 렌더링 실패:\n{proc.stderr[-1500:]}")
    return proc.stdout


def _docker_available() -> bool:
    try:
        return subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, timeout=60,
        ).returncode == 0
    except Exception:  # noqa: BLE001
        return False


# ── 파일 계약 (docker 없이도 본다) ─────────────────────────────────────────

def test_overlay_exists_and_does_not_copy_the_canonical_file():
    """오버레이는 **바뀌는 것만** 담는다 — 정본을 복사하면 한쪽만 고쳐진다."""
    assert OVERLAY.exists(), "MariaDB 배포 오버레이가 없다"
    text = OVERLAY.read_text(encoding="utf-8")
    # 정본에만 있어야 하는 것들이 복사돼 오면 두 벌이 된 것이다.
    for canonical_only in ("nginx-mtls:", "storagedata:", "golden_data:"):
        assert canonical_only not in text, (
            f"{canonical_only} 가 오버레이에 복사됐다 — 정본과 두 벌이 되면 갈린다."
        )


def test_overlay_points_every_app_service_at_mariadb():
    text = OVERLAY.read_text(encoding="utf-8")
    for service in _DB_SERVICES:
        assert re.search(rf"^  {service}:$", text, re.M), f"{service} 재정의가 없다"
    urls = re.findall(r"DATABASE_URL:\s*(\S+)", text)
    assert len(urls) == len(_DB_SERVICES), f"DATABASE_URL 이 {len(urls)}개다"
    assert all(u.startswith("mariadb+pymysql://") for u in urls), urls


def test_overlay_pins_the_image_digest():
    """공급망 — ORM 이 10.11 에서 검증됐다. 태그만 두면 조용히 올라간다."""
    text = OVERLAY.read_text(encoding="utf-8")
    assert "mariadb:10.11@sha256:" in text, "MariaDB 이미지 digest 가 고정돼 있지 않다"


# ── 렌더링 계약 (docker 가 있을 때) ────────────────────────────────────────

@pytest.mark.skipif(not _docker_available(), reason="docker 없음 — 렌더링 계약은 건너뛴다")
def test_mariadb_overlay_replaces_postgres(tmp_path):
    rendered = _render(BASE, OVERLAY, tmp_path=tmp_path)
    assert re.search(r"^  mariadb:$", rendered, re.M), "mariadb 서비스가 없다"
    assert not re.search(r"^  postgres:$", rendered, re.M), (
        "postgres 가 남아 있다 — MariaDB 배포에서 쓰지 않는 DB 가 함께 뜬다."
    )
    assert rendered.count("mariadb+pymysql://") == len(_DB_SERVICES)
    assert "postgresql+psycopg" not in rendered


@pytest.mark.skipif(not _docker_available(), reason="docker 없음 — 렌더링 계약은 건너뛴다")
def test_canonical_postgres_path_is_unchanged(tmp_path):
    """오버레이를 안 쓰면 종전 그대로다 — 이 작업이 기존 배포를 건드리면 안 된다."""
    rendered = _render(BASE, tmp_path=tmp_path)
    assert re.search(r"^  postgres:$", rendered, re.M)
    assert not re.search(r"^  mariadb:$", rendered, re.M)
    assert rendered.count("postgresql+psycopg") == len(_DB_SERVICES)


# ── 번들 계약 ──────────────────────────────────────────────────────────────

def test_bundle_requires_a_db_but_not_specifically_postgres():
    """폐쇄망 번들은 DB 이미지를 **하나는** 요구한다 — 어느 쪽인지는 배포마다 다르다.

    종전에는 postgres 를 필수로 못박아 MariaDB 번들이 아예 만들어지지 않았다
    (실측: "코어 서비스 이미지 누락: ['postgres']").
    """
    import sys

    sys.path.insert(0, str(_POC / "scripts"))
    try:
        import build_offline_bundle as bundle  # noqa: PLC0415
    finally:
        sys.path.remove(str(_POC / "scripts"))

    assert "postgres" not in bundle._REQUIRED_CORE_SERVICES, (
        "DB 를 postgres 로 못박으면 MariaDB 배포본 번들을 만들 수 없다."
    )
    assert set(bundle._REQUIRED_DB_SERVICES) == {"postgres", "mariadb"}
    # DB 를 아예 안 요구하면 폐쇄망에서 DB 없이 뜨는 번들이 나온다 — 그건 막아야 한다.
    assert bundle._REQUIRED_DB_SERVICES, "DB 요구가 사라지면 fail-closed 가 열린다"
