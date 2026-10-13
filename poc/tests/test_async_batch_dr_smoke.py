"""DR 비동기 배치 스모크의 HTTP 계약 회귀."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import p5_async_batch_smoke as smoke  # noqa: E402
import dr_drill  # noqa: E402


class _Response:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _Client:
    def __init__(self, polls: list[_Response]):
        self.polls = polls
        self.posts: list[tuple[str, dict, dict]] = []
        self.gets: list[tuple[str, dict | None]] = []

    def get(self, url: str, headers=None):
        self.gets.append((url, headers))
        if url.endswith("/healthz"):
            return _Response(200, {"status": "ok"})
        return self.polls.pop(0)

    def post(self, url: str, headers=None, json=None):
        self.posts.append((url, headers, json))
        return _Response(202, {"job_id": "dr-job-1", "status_url": "/api/v1/classify/jobs/dr-job-1"})


def test_async_batch_smoke_polls_until_terminal_and_checks_counters():
    client = _Client([
        _Response(200, {"status": "queued"}),
        _Response(200, {"status": "done", "total": 2, "completed": 2, "failed": 0}),
    ])

    result = smoke.run_smoke(
        url="http://staging.example/",
        api_key="only-for-test",
        timeout_seconds=2,
        poll_seconds=0.001,
        client=client,
    )

    assert result["status"] == "done"
    assert result["completed"] == 2
    assert client.posts == [(
        "http://staging.example/api/v1/classify/batch",
        {"Content-Type": "application/json", "X-API-Key": "only-for-test"},
        {"documents": smoke._DOCUMENTS},
    )]
    assert client.gets[-1][0].endswith("/api/v1/classify/jobs/dr-job-1")


def test_async_batch_smoke_rejects_terminal_counter_mismatch():
    client = _Client([
        _Response(200, {"status": "done", "total": 2, "completed": 1, "failed": 0}),
    ])

    with pytest.raises(smoke.SmokeFailure, match="counters do not add up"):
        smoke.run_smoke(
            url="http://staging.example",
            api_key="",
            timeout_seconds=1,
            poll_seconds=0.001,
            client=client,
        )


def test_async_batch_smoke_rejects_partial_completion_even_when_counters_match():
    client = _Client([
        _Response(200, {"status": "partial", "total": 2, "completed": 1, "failed": 1}),
    ])

    with pytest.raises(smoke.SmokeFailure, match="did not finish cleanly"):
        smoke.run_smoke(
            url="http://staging.example",
            api_key="",
            timeout_seconds=1,
            poll_seconds=0.001,
            client=client,
        )


def test_dr_stage_commands_use_staging_network_and_explicit_write_opt_in():
    default = dr_drill.stage_commands("docker-compose.dr-staging.yml")
    opted_in = dr_drill.stage_commands("docker-compose.dr-staging.yml", allow_write_smoke=True)

    assert default["smoke_classify"][-2:] == ["--url", "http://staging:8000"]
    assert default["smoke_guide"] is None
    assert default["smoke_async_batch"] is None
    assert opted_in["smoke_async_batch"][-1] == "--allow-write"


def test_cli_reads_dr_api_key_from_environment_without_putting_it_in_stage_command(monkeypatch):
    monkeypatch.setenv("KOIPA_DR_API_KEY", "environment-only-key")
    args = smoke.build_parser().parse_args(["--allow-write"])
    assert args.api_key == "environment-only-key"
    assert "environment-only-key" not in dr_drill.stage_commands(
        "docker-compose.dr-staging.yml", allow_write_smoke=True
    )["smoke_async_batch"]
