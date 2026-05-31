"""render_perf_report.py 단위 테스트.

설계:
- sparkline_svg / sparkline_with_threshold 순수 함수 검증
- _value_passes / _fmt_value / _fmt_threshold 포매팅 정확성
- render_html 통합 — 최소 보고서로 HTML 산출 + 필수 섹션 포함 검증
- 임계선 SVG·점 컬러·전체 KPI 추세 표 모두 포함되는지 확인
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


# scripts/ 임포트를 위해 sys.path 추가
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


class TestSparklineSvg:
    def test_empty_returns_empty(self):
        from render_perf_report import sparkline_svg
        assert sparkline_svg([]) == ""

    def test_single_value_duplicates(self):
        from render_perf_report import sparkline_svg
        out = sparkline_svg([1.0])
        assert "<svg" in out
        assert "<path" in out

    def test_multi_value_path(self):
        from render_perf_report import sparkline_svg
        out = sparkline_svg([1.0, 2.0, 3.0])
        assert out.startswith("<svg")
        assert 'd="M ' in out
        assert "<circle" in out  # last value marker


class TestSparklineWithThreshold:
    def test_le_threshold_color_split(self):
        """compare=le: 값 ≤ threshold면 PASS(초록), 초과면 FAIL(빨강)."""
        from render_perf_report import sparkline_with_threshold

        svg = sparkline_with_threshold(
            [1.0, 5.0, 2.0],
            threshold=3.0,
            compare="le",
        )
        assert "<svg" in svg
        # 임계선 점선
        assert "stroke-dasharray" in svg
        assert "#d97706" in svg  # 임계선 컬러
        # PASS 회차(1.0, 2.0) → 초록 점, FAIL(5.0) → 빨강 점
        assert "#16a34a" in svg
        assert "#dc2626" in svg

    def test_ge_threshold_color_split(self):
        from render_perf_report import sparkline_with_threshold

        svg = sparkline_with_threshold(
            [0.5, 0.9, 0.6],
            threshold=0.8,
            compare="ge",
        )
        # 0.9만 PASS, 0.5/0.6 FAIL
        assert "#16a34a" in svg
        assert "#dc2626" in svg

    def test_no_threshold_no_threshold_line(self):
        from render_perf_report import sparkline_with_threshold

        svg = sparkline_with_threshold([1.0, 2.0, 3.0], threshold=None)
        # 임계선 없음
        assert "stroke-dasharray" not in svg
        # 점은 모두 PASS(초록)
        assert "#16a34a" in svg
        assert "#dc2626" not in svg

    def test_bool_threshold_coerced(self):
        """bool True/False 임계는 1.0/0.0으로 변환되어 표시됨."""
        from render_perf_report import sparkline_with_threshold

        svg = sparkline_with_threshold(
            [1.0, 1.0, 0.0],
            threshold=True,
            compare="ge",
        )
        assert "<svg" in svg
        assert "stroke-dasharray" in svg

    def test_empty_values_returns_empty(self):
        from render_perf_report import sparkline_with_threshold

        assert sparkline_with_threshold([], threshold=1.0) == ""


class TestValuePasses:
    def test_le(self):
        from render_perf_report import _value_passes
        assert _value_passes(0.04, "le", 0.05) is True
        assert _value_passes(0.06, "le", 0.05) is False

    def test_ge(self):
        from render_perf_report import _value_passes
        assert _value_passes(0.85, "ge", 0.80) is True
        assert _value_passes(0.70, "ge", 0.80) is False

    def test_no_threshold_always_passes(self):
        from render_perf_report import _value_passes
        assert _value_passes(123.0, "le", None) is True


class TestFormatters:
    def test_fmt_value_units(self):
        from render_perf_report import _fmt_value
        assert _fmt_value(0.85, "ratio") == "85.0%"
        assert _fmt_value(1.0, "bool") == "True"
        assert _fmt_value(0.0, "bool") == "False"
        assert _fmt_value(123.4, "ms") == "123ms"
        assert _fmt_value(5.7, "docs/s") == "5.7"
        assert _fmt_value(0.0123, "USD") == "$0.0123"
        assert _fmt_value(42.0, "count") == "42"

    def test_fmt_threshold_symbols(self):
        from render_perf_report import _fmt_threshold
        assert _fmt_threshold(0.05, "le", "ratio") == "≤ 5.0%"
        assert _fmt_threshold(0.80, "ge", "ratio") == "≥ 80.0%"
        assert _fmt_threshold(True, "ge", "bool") == "True"


class TestRenderHtml:
    """render_html 통합 — 최소 보고서로 산출 + 필수 섹션 포함."""

    def _sample_report(self) -> dict:
        return {
            "mode": "dryrun",
            "ts": "2026-05-28T00:00:00Z",
            "duration_sec": 1.234,
            "env": {
                "python": "3.11.9",
                "platform": "win",
                "cpu_count": 16,
                "ram_gb": 64.0,
                "gpu": "N/A",
                "git_sha": "abc1234",
                "git_branch": "main",
                "pytest_collected": 324,
                "llm_provider": "noop",
                "embedding_provider": "hash",
                "vector_backend": "inmemory",
                "services": {
                    "postgres": "UP",
                    "elasticsearch": "UP",
                    "redis": "DOWN",
                    "minio": "UNKNOWN",
                },
            },
            "scenarios": [
                {
                    "scenario": "S1",
                    "title": "테스트 S1",
                    "status": "PASS",
                    "duration_ms": 100.0,
                    "kpis": [
                        {
                            "kpi_id": "S1.1",
                            "scenario": "S1",
                            "name": "p50 latency",
                            "unit": "ms",
                            "measured": 30.0,
                            "threshold": 500,
                            "compare": "le",
                            "passed": True,
                            "status": "PASS",
                            "skip_reason": "",
                            "n_samples": 8,
                        },
                    ],
                    "error": None,
                    "skip_reason": "",
                }
            ],
            "summary": {
                "total_kpis": 1,
                "pass": 1,
                "fail": 0,
                "skip": 0,
                "pass_rate": 1.0,
                "scenarios": {"total": 1, "pass": 1, "fail": 0, "skip": 0, "error": 0},
            },
        }

    def test_render_creates_html_file(self, tmp_path):
        from render_perf_report import render_html

        report = self._sample_report()
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        # 직전 회차 1건 추가 (추세 표시)
        prev = self._sample_report()
        prev["ts"] = "2026-05-27T00:00:00Z"
        prev["scenarios"][0]["kpis"][0]["measured"] = 40.0
        # recorder.write_report와 같은 파일명 규칙으로 직접 적재
        (history_dir / "perf_20260527000000Z_dryrun_aaa.json").write_text(
            json.dumps(prev, ensure_ascii=False), encoding="utf-8"
        )

        out = tmp_path / "report.html"
        result = render_html(report, out_path=out, history_dir=history_dir, mode="dryrun")
        assert result.exists()
        html = out.read_text(encoding="utf-8")

        # 필수 섹션
        assert "시나리오 성능 보고서" in html
        assert "§0" in html
        assert "§1" in html
        assert "§2" in html
        assert "§2.5" in html  # 신규 전체 KPI 추세
        assert "§3" in html
        assert "§4" in html
        # 임계선 SVG 표시 (직전 회차가 있으니 추세 차트 생성됨)
        assert "stroke-dasharray" in html

    def test_render_n1_shows_message(self, tmp_path):
        """history=1 (현재만)이면 추세 메시지 표시."""
        from render_perf_report import render_html

        report = self._sample_report()
        history_dir = tmp_path / "history"
        history_dir.mkdir()

        out = tmp_path / "report.html"
        render_html(report, out_path=out, history_dir=history_dir, mode="dryrun")
        html = out.read_text(encoding="utf-8")
        assert "측정 누적 중" in html or "N=1" in html

    def test_render_includes_env_badges(self, tmp_path):
        from render_perf_report import render_html

        report = self._sample_report()
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        out = tmp_path / "report.html"
        render_html(report, out_path=out, history_dir=history_dir, mode="dryrun")
        html = out.read_text(encoding="utf-8")
        # 서비스 배지
        assert "PG: UP" in html
        assert "Redis: DOWN" in html
