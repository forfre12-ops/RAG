"""setup.sh 7단계 — 설치가 끝나면 콘솔 주소만 열어도 로그인되는 상태가 되는지.

배경(2026-09-25). 패키지로 설치하면 로그인 화면이 "토큰을 붙여넣으십시오" 를 요구해 고객사·전문가가
막힌다는 지적이 나왔다. 2026-08-24 결정은 "사람이 토큰을 입력하는 안은 답이 아니다" 였고,
211·223 시험서버는 로그인 화면에 토큰을 미리 채워 두어 주소만 열면 스스로 로그인하는 방식으로
그 결정을 지키고 있었다 — 그런데 패키지 설치(setup.sh)는 그 설정을 하지 않았다.

이 시험이 지키는 것(가짜 compose 로 setup.sh 의 해당 구간만 떼어 돌린다 — 실제 도커 없이 논리만 본다):
  · 지재원 노드는 reviewer 전용 토큰, 고객사 노드는 관리자 토큰이 로그인 화면에 미리 채워진다
  · 하드닝 프로파일이 기동을 거부하지 않도록 ALLOW_UNSAFE 가 함께 적힌다
  · 토큰이 호스트 파일로 꺼내진다(컨테이너를 다시 만들면 안의 토큰 파일이 사라지므로) — 권한 600
  · 다시 돌려도 .env 에 프리필 줄이 하나만 남는다(멱등) · 나머지 줄은 그대로다
  · CONSOLE_AUTOLOGIN=0 이면 프리필이 지워지고 스택이 다시 올라간다
  · dry-run 은 아무것도 바꾸지 않는다 · 토큰을 못 꺼내면 자동 로그인을 켜지 않는다
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1] / "scripts" / "setup.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash 가 없다")


def _section(text: str, start: str, end: str) -> str:
    a = text.index(start)
    b = text.index(end, a)
    return text[a:b]


HELPERS = _section(SETUP.read_text(encoding="utf-8"), "# >>> console-login helpers", "# <<< console-login helpers")
STEP7 = _section(SETUP.read_text(encoding="utf-8"), "# ── 7. 검수 콘솔 로그인", "# ── 8. 설치 검증")

FAKE_COMPOSE = r"""#!/usr/bin/env bash
# 가짜 compose — `... exec -T api <명령>` 만 흉내 낸다.
echo "$*" >> "$FAKE_LOG"
while [ "$#" -gt 0 ] && [ "$1" != "exec" ]; do shift; done
shift; shift; shift            # exec -T api
if [ "$1" = "python" ]; then
  sub=""; roles=""
  while [ "$#" -gt 0 ]; do
    case "$1" in --sub) sub="$2"; shift;; --roles) roles="$2"; shift;; esac
    shift
  done
  [ "${FAKE_ISSUE_FAIL:-0}" = "1" ] && { echo "boom" >&2; exit 1; }
  printf '{\n  "키": "기존 키 사용",\n  "sub": "%s",\n  "roles": ["%s"],\n  "만료": "2027-09-25T00:00:00+00:00",\n}\n' "$sub" "$roles"
elif [ "$1" = "cat" ]; then
  [ "${FAKE_CAT_EMPTY:-0}" = "1" ] && exit 0
  name="$(basename "$2" .txt)"
  printf 'hdr.%s.sig\n' "$name"
fi
"""


def _run(tmp: Path, *, node: str, autologin: str = "1", dry: str = "0", env_text: str = "API_KEY=xyz\nFOO=bar\n",
         reviewer: str = "expert-01", extra_env: dict | None = None, runs: int = 1) -> subprocess.CompletedProcess:
    (tmp / ".env").write_text(env_text, encoding="utf-8", newline="\n")
    fake = tmp / "fakecompose"
    fake.write_text(FAKE_COMPOSE, encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    (tmp / "deploy_airgap.sh").write_text('echo "DEPLOY GPU=${GPU_OVERLAY:-none}" >> "$FAKE_LOG"\n', encoding="utf-8", newline="\n")
    (tmp / "infra-config").mkdir(exist_ok=True)
    body = "\n".join([
        "set -euo pipefail",
        f'BUNDLE="{tmp.as_posix()}"; cd "$BUNDLE"',
        'ENV_FILE=.env; API_PORT=8000',
        f'DRY_RUN={dry}; NODE={node}; REVIEWER_ID="{reviewer}"',
        f'CONSOLE_AUTOLOGIN={autologin}; CONSOLE_TOKEN_DAYS=365',
        f'CRT_COMPOSE="{fake.as_posix()}"',
        "b(){ echo \">> $*\"; }; ok(){ echo \"[ok] $*\"; }; inf(){ echo \"$*\"; }",
        HELPERS,
        *[STEP7] * runs,
        'echo "RESULT autologin=$_autologin_on expiry=$_expiry who=$_prefill_who"',
    ])
    script = tmp / "driver.sh"
    script.write_text(body, encoding="utf-8", newline="\n")
    env = {**os.environ, "FAKE_LOG": (tmp / "calls.log").as_posix(), **(extra_env or {})}
    return subprocess.run([BASH, script.as_posix()], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)


def _env_lines(tmp: Path) -> list[str]:
    return (tmp / ".env").read_text(encoding="utf-8").splitlines()


def _calls(tmp: Path) -> str:
    p = tmp / "calls.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_jjw_node_prefills_the_reviewer_only_token_and_turns_autologin_on(tmp_path):
    r = _run(tmp_path, node="jjw")

    assert r.returncode == 0, r.stdout + r.stderr
    lines = _env_lines(tmp_path)
    assert "CONSOLE_LOGIN_PREFILL_TOKEN=hdr.expert-01.sig" in lines        # 전문가는 reviewer 전용 토큰으로 들어간다
    assert "CONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE=1" in lines                   # 하드닝 프로파일의 기동 거부를 명시적으로 연다
    assert "API_KEY=xyz" in lines and "FOO=bar" in lines                     # 나머지 줄은 그대로
    assert "RESULT autologin=1 expiry=2027-09-25 who=expert-01" in r.stdout
    calls = _calls(tmp_path)
    assert "--sub admin --roles admin,reviewer --days 365" in calls
    assert "--sub expert-01 --roles reviewer --days 365" in calls
    assert calls.count("DEPLOY") == 1                                        # 프리필을 읽히려고 스택을 한 번 다시 올린다
    assert (tmp_path / "console_admin_token.txt").read_text(encoding="utf-8") == "hdr.admin.sig"
    assert (tmp_path / "console_reviewer_token.txt").read_text(encoding="utf-8") == "hdr.expert-01.sig"
    if os.name != "nt":
        assert _mode(tmp_path / "console_admin_token.txt") == 0o600
        assert _mode(tmp_path / "console_reviewer_token.txt") == 0o600
        assert _mode(tmp_path / ".env") == 0o600


def test_customer_node_prefills_the_admin_token_and_makes_no_reviewer_token(tmp_path):
    r = _run(tmp_path, node="customer")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "CONSOLE_LOGIN_PREFILL_TOKEN=hdr.admin.sig" in _env_lines(tmp_path)
    assert not (tmp_path / "console_reviewer_token.txt").exists()
    assert "--roles reviewer " not in _calls(tmp_path)
    assert "RESULT autologin=1 expiry=2027-09-25 who=admin" in r.stdout


def test_running_again_leaves_exactly_one_prefill_line(tmp_path):
    r = _run(tmp_path, node="jjw", runs=2)

    assert r.returncode == 0, r.stdout + r.stderr
    lines = _env_lines(tmp_path)
    assert sum(x.startswith("CONSOLE_LOGIN_PREFILL_TOKEN=") for x in lines) == 1
    assert sum(x.startswith("CONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE=") for x in lines) == 1
    assert lines[:2] == ["API_KEY=xyz", "FOO=bar"]


def test_autologin_off_removes_an_existing_prefill_and_restarts_the_stack(tmp_path):
    old = "API_KEY=xyz\nCONSOLE_LOGIN_PREFILL_TOKEN=old.tok.en\nCONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE=1\nFOO=bar\n"
    r = _run(tmp_path, node="customer", autologin="0", env_text=old)

    assert r.returncode == 0, r.stdout + r.stderr
    assert _env_lines(tmp_path) == ["API_KEY=xyz", "FOO=bar"]
    assert _calls(tmp_path).count("DEPLOY") == 1                            # 지운 값이 반영되려면 다시 올려야 한다
    assert "RESULT autologin=0" in r.stdout


def test_autologin_off_with_nothing_to_remove_does_not_restart(tmp_path):
    r = _run(tmp_path, node="customer", autologin="0")

    assert r.returncode == 0, r.stdout + r.stderr
    assert _env_lines(tmp_path) == ["API_KEY=xyz", "FOO=bar"]
    assert "DEPLOY" not in _calls(tmp_path)


def test_dry_run_changes_nothing(tmp_path):
    r = _run(tmp_path, node="jjw", dry="1")

    assert r.returncode == 0, r.stdout + r.stderr
    assert _env_lines(tmp_path) == ["API_KEY=xyz", "FOO=bar"]
    assert not (tmp_path / "console_admin_token.txt").exists()
    assert "DEPLOY" not in _calls(tmp_path)
    assert "(dry-run)" in r.stdout


def test_autologin_is_not_turned_on_when_the_token_cannot_be_read_out(tmp_path):
    r = _run(tmp_path, node="customer", extra_env={"FAKE_CAT_EMPTY": "1"})

    assert r.returncode == 0, r.stdout + r.stderr
    assert not any(x.startswith("CONSOLE_LOGIN_PREFILL") for x in _env_lines(tmp_path))
    assert "DEPLOY" not in _calls(tmp_path)
    assert "RESULT autologin=0" in r.stdout


def test_a_failed_issue_prints_the_error_and_does_not_stop_the_install(tmp_path):
    r = _run(tmp_path, node="customer", extra_env={"FAKE_ISSUE_FAIL": "1", "FAKE_CAT_EMPTY": "1"})

    assert r.returncode == 0, r.stdout + r.stderr
    assert "boom" in r.stderr
    assert "RESULT autologin=0" in r.stdout


def test_a_reviewer_id_with_odd_characters_maps_to_the_token_file_name_the_issuer_uses(tmp_path):
    r = _run(tmp_path, node="jjw", reviewer="kim expert")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "secrets/console_jwt/tokens/kim_expert.txt" in _calls(tmp_path)    # 발급기의 파일명 규칙(_safe_name)과 같다
