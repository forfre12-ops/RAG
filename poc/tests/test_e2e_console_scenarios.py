"""관리자 콘솔 e2e 시나리오를 pytest 에서 돌린다.

본체는 `tests/e2e_console/` 의 node 하니스다(jsdom 으로 실제 DOM 을 띄우고 버튼을 누른다).
여기서는 그것을 한 번 실행하고 **시나리오 하나를 시험 하나로** 펼쳐 보여 준다 — 어느
시나리오가 왜 깨졌는지 pytest 출력만 보고 알 수 있게.

왜 파이썬 시험이 아니라 node 하니스인가.
  화면 결함은 "브라우저에서 스크립트가 실제로 도는가"의 문제다. 파이썬에서 HTML 문자열을
  검사하는 방식으로는 2026-08-18(없는 함수 호출로 화면 전체가 빔)·2026-08-23(지운 요소를
  만져 초기화가 중단됨) 두 사고를 모두 못 잡았다 — 그때 콘솔 계열 시험 423건이 통과했다.

준비(한 번만):
    cd poc/tests/e2e_console && npm install
node 나 의존성이 없으면 이 파일은 통째로 skip 한다 — 파이썬 CI 에 node 를 강제하지 않는다.

실서버를 상대로 같은 시나리오를 돌리려면(고장 주입 시나리오는 자동 제외):
    cd poc/tests/e2e_console && node run.mjs --base http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "e2e_console"
_RUNNER = _HARNESS / "run.mjs"
_NODE = shutil.which("node")
_DEPS_READY = (_HARNESS / "node_modules" / "jsdom").is_dir()

_SKIP_REASON = (
    "node 없음" if not _NODE
    else "jsdom 미설치 — `cd poc/tests/e2e_console && npm install` 한 번 실행하면 켜진다"
)

pytestmark = pytest.mark.skipif(
    not (_NODE and _DEPS_READY and _RUNNER.exists()), reason=_SKIP_REASON
)


def _run_node(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_NODE, str(_RUNNER), *args],
        cwd=str(_HARNESS),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _scenario_ids() -> list[str]:
    """수집 시점에 시나리오 목록만 가져온다(화면은 띄우지 않는다)."""
    if not (_NODE and _DEPS_READY and _RUNNER.exists()):
        return []
    try:
        proc = _run_node(["--list"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [ln.split("\t")[0] for ln in proc.stdout.splitlines() if ln.strip()]


_IDS = _scenario_ids()


@pytest.fixture(scope="module")
def results() -> dict[str, dict]:
    """전 시나리오를 한 번 실행하고 id → 결과로 돌려준다."""
    proc = _run_node(["--json"], timeout=900)
    if not proc.stdout.strip():
        pytest.fail(f"하니스가 아무것도 출력하지 않았다.\nstderr:\n{proc.stderr[-4000:]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # 하니스 자체가 죽은 경우
        pytest.fail(
            f"하니스 출력이 JSON 이 아니다({exc}).\n"
            f"stdout 앞부분:\n{proc.stdout[:2000]}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return {r["id"]: r for r in payload["results"]}


def test_harness_collected_scenarios():
    """시나리오가 수집됐는지부터 확인한다 — 0건이면 아래 전부가 조용히 통과해 버린다."""
    assert len(_IDS) >= 40, f"시나리오가 너무 적다({len(_IDS)}건) — 하니스 로딩 실패 의심"


@pytest.mark.parametrize("scenario_id", _IDS)
def test_console_scenario(results: dict[str, dict], scenario_id: str):
    r = results.get(scenario_id)
    assert r is not None, f"실행 결과에 없다: {scenario_id}"

    if r["status"] == "skipped":
        pytest.skip(r.get("reason") or "건너뜀")

    if r["status"] == "crash":
        pytest.fail(f"[{r['title']}] 시나리오가 중단됐다:\n{r.get('crash')}")

    if r["failures"]:
        lines = "\n".join(
            f"  · {f['label']}" + (f"\n      {f['detail']}" if f.get("detail") else "")
            for f in r["failures"]
        )
        why = f"\n(이 시나리오가 있는 이유: {r['why']})" if r.get("why") else ""
        pytest.fail(f"[{r['title']}]{why}\n{lines}")

    assert r["passed"], f"확인한 것이 하나도 없다: {scenario_id}"
