"""폐기 도구(M28) — 안전장치가 실제로 막는가.

왜(2026-09-08). 폐기는 한 번뿐이고 되돌릴 수 없다. 그래서 이 도구에서 가장 중요한 것은
지우는 코드가 아니라 **지우지 않는 코드**다. 시험도 그쪽을 본다.

  ① 기본은 dry-run — 옵션 없이 부르면 아무것도 지우지 않는다
  ② --execute 만으로는 안 된다 — 프로젝트명을 다시 적어야 한다(--confirm)
  ③ 암호 소거가 확인되지 않으면 중단 — 키가 남으면 백업된 암호문이 나중에 복호된다
  ④ 삭제 대상 목록이 배포 compose 의 볼륨과 일치한다 — 빠지면 조용히 남는다

⚠ 이 시험은 docker 를 호출하지 않는다. survey() 를 가짜로 바꿔 분기만 본다.
  진짜 docker 를 부르면 시험이 개발기의 볼륨을 지울 수 있다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

import decommission as dc  # noqa: E402


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    """docker 를 못 부르게 막는다 — 시험이 실제로 무언가를 지우면 안 된다."""
    def _boom(*_a, **_k):
        raise AssertionError("시험이 docker 를 호출했다 - 삭제 위험")
    monkeypatch.setattr(dc, "_docker", _boom)
    monkeypatch.setattr(dc, "destroy", _boom)
    monkeypatch.setattr(dc, "survey", lambda project: {
        "volumes": [{"name": f"{project}_pgdata", "why": "", "exists": True}],
        "paths": [], "containers": [],
    })


def test_default_is_dry_run_and_deletes_nothing():
    """옵션 없이 부르면 dry-run 이다. destroy 는 위 픽스처가 막아 뒀다."""
    assert dc.main(["--project", "koipa-airgap"]) == 0


def test_execute_without_matching_confirm_aborts():
    """--execute 만으로는 지우지 못한다. 이름을 두 번 적게 한다."""
    assert dc.main(["--project", "koipa-airgap", "--execute"]) == 2
    assert dc.main(["--project", "koipa-airgap", "--execute", "--confirm", "koipa"]) == 2


def test_execute_without_key_destruction_aborts():
    """암호 소거가 이 절차의 핵심이다 - 확인 없이는 진행하지 않는다.

    키가 남으면 볼륨을 지워도 백업된 암호문이 나중에 복호될 수 있다.
    """
    rc = dc.main(["--project", "koipa-airgap", "--execute", "--confirm", "koipa-airgap"])
    assert rc == 2, "키 파기 확인 없이 폐기가 진행됐다"


def test_tool_does_not_claim_to_destroy_the_key():
    """도구가 키를 대신 지웠다고 말하면 안 된다 - 우리가 모르는 사본이 있다."""
    src = Path(dc.__file__).read_text(encoding="utf-8")
    assert "대신 지워 주지 않는다" in src
    assert "crypto_erase_attested" in src, "확인서에 '누가 확인했는가'가 남아야 한다"


# ── 삭제 대상이 배포와 어긋나지 않는가 ──────────────────────────────────────

def test_volume_list_matches_deployment_composes():
    """compose 가 만드는 볼륨이 삭제 목록에 다 있어야 한다.

    빠지면 폐기했다고 보고한 뒤에도 데이터가 남는다. compose 에 볼륨을 새로 추가하면
    이 시험이 먼저 깨진다.
    """
    declared: set[str] = set()
    for name in ("docker-compose.airgap.yml", "docker-compose.airgap.mariadb.yml"):
        path = _POC / name
        if not path.exists():
            continue
        tail = path.read_text(encoding="utf-8").split("\nvolumes:")[-1]
        declared |= set(re.findall(r"^  ([a-z_][a-z0-9_]*):", tail, re.M))

    covered = {short for short, _ in dc._VOLUMES}
    missing = declared - covered
    assert not missing, (
        f"배포가 만드는 볼륨이 폐기 목록에 없다: {sorted(missing)}. "
        "폐기 후에도 데이터가 남는다."
    )


def test_host_paths_cover_model_and_training_data():
    """M28 이 이름을 대는 것들 - AI모델·학습데이터가 대상에 있어야 한다."""
    covered = {rel for rel, _ in dc._HOST_PATHS}
    assert {"models", "datasets"} <= covered
