"""setup.sh 6-1(검수 문서 적재)·6-2(배포 모델 등록) — 설치가 끝났을 때 콘솔이 빈 화면이 아니게.

배경(2026-09-25). 설치 단계에 검수 문서를 볼륨에 올리는 단계가 없어서, 설치가 끝나면 콘솔은 뜨는데 검수할
문서가 0건이었다. 종전 안내는 `cp -n golden_review_batch/* <볼륨>/…` 였는데 볼륨 경로를 모르면 못 하고,
파일 소유자가 uid 1000 이 아니면 서명 제출이 500 이었다. 모델 등록도 setup.sh 어디서도 부르지 않아
`GET /metrics/latest` 가 404 "no active model" 이고 모델 활성화가 '미등록'으로 실패했다.

가짜 compose 로 해당 구간만 떼어 돌린다(도커 없이 논리만 본다). 이 시험이 지키는 것:
  · 지재원 노드는 번들의 검수 문서가 볼륨으로 복사된다 — 이미 있던 후보·검수 원장은 덮어쓰지 않는다
  · 고객사 노드는 검수 문서를 안 올리고 모델 등록만 한다
  · 검수 문서 폴더가 없거나 복사를 확인하지 못하면 성공이라 적지 않고 설치도 안 멈춘다
  · 모델 등록이 실패해도 설치는 계속된다 · dry-run 은 아무것도 안 한다
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1] / "scripts" / "setup.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash 가 없다")


def _section(text: str, start: str, end: str) -> str:
    a = text.index(start)
    return text[a:text.index(end, a)]


_TEXT = SETUP.read_text(encoding="utf-8")
HELPERS = _section(_TEXT, "# >>> install-data helpers", "# <<< install-data helpers")
STEPS = _section(_TEXT, "# >>> install-data steps", "# <<< install-data steps")

FAKE_COMPOSE = r"""#!/usr/bin/env bash
# 가짜 compose — `run ... -v SRC:/incoming ... sh -c SCRIPT` 와 `exec -T api python register...` 만 흉내 낸다.
echo "$*" >> "$FAKE_LOG"
args=("$@"); sub=""; i=0
while [ "$i" -lt "${#args[@]}" ]; do
  case "${args[$i]}" in run|exec) sub="${args[$i]}"; break;; esac
  i=$((i+1))
done
if [ "$sub" = "run" ]; then
  [ "${FAKE_RUN_FAIL:-0}" = "1" ] && exit 1
  src=""
  for ((j=i; j<${#args[@]}; j++)); do
    if [ "${args[$j]}" = "-v" ]; then v="${args[$((j+1))]}"; src="${v%%:/incoming*}"; fi
  done
  script="${args[$((${#args[@]}-1))]}"
  script="${script//\/incoming/$src}"
  script="${script//\/app\/datasets/$FAKE_VOL}"
  bash -c "$script"
elif [ "$sub" = "exec" ]; then
  [ "${FAKE_REGISTER_FAIL:-0}" = "1" ] && exit 1
  echo registered
fi
"""


def _bundle(tmp: Path, *, docs: int = 3, with_batch: bool = True) -> Path:
    if with_batch:
        b = tmp / "golden_review_batch"
        b.mkdir()
        for n in range(1, docs + 1):
            (b / f"MD-{n:04d}.metadata.json").write_text(f'{{"doc_id":"MD-{n:04d}","from":"bundle"}}', encoding="utf-8")
            (b / f"MD-{n:04d}_review.md").write_text(f"본문 {n}", encoding="utf-8")
    return tmp / "vol"


def _run(tmp: Path, *, node: str, dry: str = "0", extra_env: dict | None = None) -> subprocess.CompletedProcess:
    fake = tmp / "fakecompose"
    fake.write_text(FAKE_COMPOSE, encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    vol = tmp / "vol"
    vol.mkdir(exist_ok=True)
    body = "\n".join([
        "set -euo pipefail",
        f'BUNDLE="{tmp.as_posix()}"; cd "$BUNDLE"',
        f'ENV_FILE=.env; DRY_RUN={dry}; NODE={node}',
        f'CRT_COMPOSE="{fake.as_posix()}"',
        "b(){ echo \">> $*\"; }; ok(){ echo \"[ok] $*\"; }; inf(){ echo \"$*\"; }",
        HELPERS,
        STEPS,
        'echo "END"',
    ])
    script = tmp / "driver.sh"
    script.write_text(body, encoding="utf-8", newline="\n")
    env = {**os.environ, "FAKE_LOG": (tmp / "calls.log").as_posix(), "FAKE_VOL": vol.as_posix(), **(extra_env or {})}
    return subprocess.run([BASH, script.as_posix()], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)


def _calls(tmp: Path) -> str:
    p = tmp / "calls.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _candidates(tmp: Path) -> Path:
    return tmp / "vol" / "proxy_gold" / "single_document_candidates"


def test_the_review_batch_reaches_the_volume_and_existing_candidates_are_not_overwritten(tmp_path):
    _bundle(tmp_path, docs=3)
    keep = _candidates(tmp_path)
    keep.mkdir(parents=True)
    (keep / "MD-0002.metadata.json").write_text("이미 있던 후보와 결정", encoding="utf-8")

    r = _run(tmp_path, node="jjw")

    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.name for p in _candidates(tmp_path).glob("MD-*.metadata.json")) == [f"MD-000{n}.metadata.json" for n in (1, 2, 3)]
    assert (_candidates(tmp_path) / "MD-0002.metadata.json").read_text(encoding="utf-8") == "이미 있던 후보와 결정"   # 덮어쓰지 않았다
    assert (_candidates(tmp_path) / "MD-0003_review.md").read_text(encoding="utf-8") == "본문 3"
    assert "[ok] 검수 문서 적재 — 볼륨에 후보 3건(번들 3건)" in r.stdout
    assert "register_deployed_model.py" in _calls(tmp_path)                      # 모델 등록도 같이 한다


def test_the_customer_node_registers_the_model_but_loads_no_review_documents(tmp_path):
    _bundle(tmp_path, docs=3)

    r = _run(tmp_path, node="customer")

    assert r.returncode == 0, r.stdout + r.stderr
    assert not _candidates(tmp_path).exists()
    calls = _calls(tmp_path)
    assert "incoming" not in calls and "register_deployed_model.py" in calls
    assert "6-1" not in r.stdout


def test_a_bundle_without_review_documents_says_so_and_still_registers_the_model(tmp_path):
    _bundle(tmp_path, with_batch=False)

    r = _run(tmp_path, node="jjw")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "golden_review_batch/ 없음" in r.stdout
    assert "register_deployed_model.py" in _calls(tmp_path)


def test_a_copy_that_cannot_be_confirmed_is_not_reported_as_success(tmp_path):
    _bundle(tmp_path, docs=3)

    r = _run(tmp_path, node="jjw", extra_env={"FAKE_RUN_FAIL": "1"})

    assert r.returncode == 0, r.stdout + r.stderr                                # 설치는 멈추지 않는다
    assert "[ok] 검수 문서 적재" not in r.stdout
    assert "확인하지 못했다" in r.stdout and "번들 3건" in r.stdout


def test_a_failed_model_registration_does_not_stop_the_install(tmp_path):
    _bundle(tmp_path, docs=2)

    r = _run(tmp_path, node="jjw", extra_env={"FAKE_REGISTER_FAIL": "1"})

    assert r.returncode == 0, r.stdout + r.stderr
    assert "모델 등록에 실패했다" in r.stdout and "END" in r.stdout
    assert "[ok] 검수 문서 적재" in r.stdout                                      # 앞 단계 결과는 그대로


def test_dry_run_changes_nothing(tmp_path):
    _bundle(tmp_path, docs=3)

    r = _run(tmp_path, node="jjw", dry="1")

    assert r.returncode == 0, r.stdout + r.stderr
    assert not _candidates(tmp_path).exists()
    assert _calls(tmp_path) == ""
    assert r.stdout.count("(dry-run)") == 2
