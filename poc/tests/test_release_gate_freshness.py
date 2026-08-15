"""릴리스 게이트 최신성·동일성 — 오래된 PASS 증거 재사용을 막는다.

왜(실측 2026-08-15). 게이트가 `verdict` 와 gate status 만 읽고 **증거가 언제 만들어졌는지,
무엇을 서술하는지**를 묻지 않았다. 저장소에 실제로 이런 상태가 있었다:

    reports/release_artifact_manifest.json  status=READY  generated_at=2026-06-01
    reports/operational_readiness.json      verdict=FAIL  generated_at=2026-08-05

두 달 반 묵은 READY 를 다른 빌드에 갖다 붙일 수 있었다. readiness 리포트가
`generated_at` · `deployed_model` · `evaluated_model` 을 **이미 담고 있는데** 게이트가
안 봤을 뿐이다.

네 가지를 각각 묻는다:
    1. 증거가 얼마나 오래됐나
    2. 지금 배포하는 코드에서 나왔나
    3. 서버가 실제로 로드할 모델을 서술하나
    4. 배포할 프로파일에서 나왔나
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_gate.py"


def _run(report: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--readiness", str(report), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def _report(tmp_path: Path, **over) -> Path:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "verdict": "PASS",
        "gates": [{"name": "g1", "status": "PASS", "detail": ""}],
        "generated_at": now.strftime("%Y-%m-%d"),
        "git_sha": _head(),
        "deployed_model": "artifacts/classifier_p1_v5_clean/v-fe4b386b",
        "evaluated_model": "artifacts/classifier_p1_v5_clean/v-fe4b386b",
        "deploy_profile": "onprem-local",
    }
    payload.update(over)
    p = tmp_path / "readiness.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    return p


def _head() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def test_fresh_evidence_passes(tmp_path):
    r = _run(_report(tmp_path), "--require-fresh")
    assert r.returncode == 0, r.stdout


def test_stale_report_blocks(tmp_path):
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).strftime("%Y-%m-%d")
    r = _run(_report(tmp_path, generated_at=old), "--require-fresh")
    assert r.returncode == 1
    assert "90d old" in r.stdout or "old" in r.stdout


def test_wrong_git_sha_blocks(tmp_path):
    r = _run(_report(tmp_path, git_sha="deadbeefdeadbeef"), "--require-fresh")
    assert r.returncode == 1
    assert "git sha" in r.stdout


def test_missing_git_sha_blocks(tmp_path):
    """sha 가 없으면 '이 빌드에서 나왔다' 를 증명할 수 없다 - 통과시키면 안 된다."""
    p = _report(tmp_path)
    d = json.loads(p.read_text("utf-8"))
    d.pop("git_sha")
    p.write_text(json.dumps(d), "utf-8")
    r = _run(p, "--require-fresh")
    assert r.returncode == 1
    assert "git sha" in r.stdout


def test_model_mismatch_blocks(tmp_path):
    r = _run(_report(tmp_path), "--require-fresh",
             "--expect-model-dir", "artifacts/classifier_p1_v5_clean/v-OTHER")
    assert r.returncode == 1
    assert "deployed_model" in r.stdout or "evaluated_model" in r.stdout


def test_profile_mismatch_blocks(tmp_path):
    r = _run(_report(tmp_path), "--require-fresh", "--expect-profile", "full-train")
    assert r.returncode == 1
    assert "deploy_profile" in r.stdout


def test_without_flag_only_warns(tmp_path):
    """기존 호출부를 깨지 않는다 - 플래그 없이는 경고만 내고 판정은 그대로."""
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).strftime("%Y-%m-%d")
    r = _run(_report(tmp_path, generated_at=old))
    assert r.returncode == 0, r.stdout
    assert "WARN" in r.stdout


def test_output_is_ascii_only(tmp_path):
    """고객사 cp949 콘솔에서 비-ASCII 한 글자가 게이트를 죽인다.

    파일 자신이 그렇게 경고해 두었는데 2026-08-15 에 em dash 를 넣어 실제로 죽였다.
    """
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).strftime("%Y-%m-%d")
    for args in ((), ("--require-fresh",)):
        r = _run(_report(tmp_path, generated_at=old), *args)
        assert "UnicodeEncodeError" not in (r.stderr or "")
        r.stdout.encode("cp949")  # raises if a non-ASCII glyph slipped in


@pytest.mark.parametrize("verdict", ["FAIL", "UNKNOWN"])
def test_bad_verdict_still_blocks_with_fresh_evidence(verdict, tmp_path):
    """최신성 검사가 기존 차단을 약화시키면 안 된다."""
    r = _run(_report(tmp_path, verdict=verdict), "--require-fresh")
    assert r.returncode == 1
