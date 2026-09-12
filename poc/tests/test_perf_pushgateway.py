"""koipa.perf.pushgateway 단위 테스트.

설계:
- build_exposition() — Prometheus 텍스트 포맷 정확성 (라벨·메트릭 이름·값)
- push() — 네트워크 실패 시 silent False 반환 (PSH 결과에 영향 없음)
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock
from urllib.error import URLError


SAMPLE_REPORT = {
    "mode": "dryrun",
    "ts": "2026-05-28T00:00:00Z",
    "duration_sec": 1.0,
    "env": {"git_sha": "abc1234"},
    "scenarios": [
        {
            "scenario": "S1",
            "title": "T",
            "status": "PASS",
            "duration_ms": 0,
            "kpis": [
                {
                    "kpi_id": "S1.1",
                    "scenario": "S1",
                    "name": "p50",
                    "unit": "ms",
                    "measured": 30.0,
                    "threshold": 500,
                    "compare": "le",
                    "passed": True,
                    "status": "PASS",
                    "skip_reason": "",
                    "n_samples": 8,
                },
                {
                    "kpi_id": "S1.4",
                    "scenario": "S1",
                    "name": "FNR",
                    "unit": "ratio",
                    "measured": 0.0,
                    "threshold": 0.05,
                    "compare": "le",
                    "passed": False,
                    "status": "SKIP",
                    "skip_reason": "missing: trained_model",
                    "n_samples": 0,
                },
            ],
            "error": None,
            "skip_reason": "",
        }
    ],
    "summary": {
        "total_kpis": 2,
        "pass": 1,
        "fail": 0,
        "skip": 1,
        "pass_rate": 0.5,
        "scenarios": {"total": 1, "pass": 1, "fail": 0, "skip": 0, "error": 0},
    },
}


class TestBuildExposition:
    def test_includes_summary_metrics(self):
        from koipa.perf.pushgateway import build_exposition

        out = build_exposition(SAMPLE_REPORT)
        assert "koipa_psh_summary_pass" in out
        assert "koipa_psh_summary_fail" in out
        assert "koipa_psh_summary_skip" in out
        assert "koipa_psh_summary_total" in out
        assert "koipa_psh_pass_rate" in out

    def test_includes_kpi_gauges(self):
        from koipa.perf.pushgateway import build_exposition

        out = build_exposition(SAMPLE_REPORT)
        assert "koipa_psh_kpi_measured" in out
        assert "koipa_psh_kpi_threshold" in out
        assert "koipa_psh_kpi_passed" in out
        # 라벨 포함
        assert 'kpi_id="S1.1"' in out
        assert 'scenario="S1"' in out
        assert 'mode="dryrun"' in out
        assert 'git_sha="abc1234"' in out

    def test_summary_values_match(self):
        from koipa.perf.pushgateway import build_exposition

        out = build_exposition(SAMPLE_REPORT)
        assert 'koipa_psh_summary_pass{mode="dryrun",git_sha="abc1234"} 1' in out
        assert 'koipa_psh_summary_skip{mode="dryrun",git_sha="abc1234"} 1' in out
        assert 'koipa_psh_pass_rate{mode="dryrun",git_sha="abc1234"} 0.5' in out

    def test_status_to_value(self):
        from koipa.perf.pushgateway import build_exposition

        out = build_exposition(SAMPLE_REPORT)
        # PASS=1, SKIP=-1, FAIL=0
        # passed line for S1.1: ends with " 1"
        assert "koipa_psh_kpi_passed" in out
        # passed에는 unit/compare 라벨 없음
        assert (
            'koipa_psh_kpi_passed{kpi_id="S1.1",scenario="S1",mode="dryrun",git_sha="abc1234"} 1'
            in out
        )
        assert (
            'koipa_psh_kpi_passed{kpi_id="S1.4",scenario="S1",mode="dryrun",git_sha="abc1234"} -1'
            in out
        )

    def test_help_and_type_lines(self):
        from koipa.perf.pushgateway import build_exposition

        out = build_exposition(SAMPLE_REPORT)
        assert "# HELP koipa_psh_summary_pass" in out
        assert "# TYPE koipa_psh_summary_pass gauge" in out
        assert "# HELP koipa_psh_kpi_measured" in out
        assert "# TYPE koipa_psh_kpi_measured gauge" in out

    def test_label_escaping_safe(self):
        """git_sha 라벨에 quote/backslash가 와도 안전 이스케이프."""
        from koipa.perf.pushgateway import build_exposition

        report = dict(SAMPLE_REPORT)
        report["env"] = {"git_sha": 'abc"\\1234'}
        out = build_exposition(report)
        # Prometheus 라벨 값에서 backslash는 \\, double quote는 \" 로 이스케이프
        assert 'git_sha="abc\\"\\\\1234"' in out


class TestPushFunction:
    def test_empty_url_returns_false(self):
        from koipa.perf.pushgateway import push

        assert push(SAMPLE_REPORT, url="") is False

    def test_network_error_returns_false(self):
        from koipa.perf.pushgateway import push

        with patch(
            "urllib.request.urlopen",
            side_effect=URLError("connection refused"),
        ):
            assert push(SAMPLE_REPORT, url="http://nonexistent:9091") is False

    def test_unexpected_error_returns_false(self):
        from koipa.perf.pushgateway import push

        with patch("urllib.request.urlopen", side_effect=RuntimeError("boom")):
            assert push(SAMPLE_REPORT, url="http://x:9091") is False

    def test_success_returns_true(self):
        from koipa.perf.pushgateway import push

        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda *a: False
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert push(SAMPLE_REPORT, url="http://pushgw:9091") is True

    def test_non_2xx_returns_false(self):
        from koipa.perf.pushgateway import push

        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda *a: False
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert push(SAMPLE_REPORT, url="http://pushgw:9091") is False
