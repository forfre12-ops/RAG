"""PSH (Performance Scenario Harness) 자체 단위 테스트.

설계:
- KPI 집계 함수·비교 함수 순수 검증
- AvailableResources `has()`·`from_env_snapshot()` 동작
- trained_model 감지 — env 강제 플래그 우선, DB 미가용 시 False
- KPI requires에 따른 자동 SKIP 동작
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.fullstack
from lloydk.perf.harness import AvailableResources, _detect_trained_model
from lloydk.perf.kpis import KPIS, aggregate, kpi_by_id, passes


class TestAggregate:
    def test_p50_p95(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        assert aggregate(vals, "p50") == pytest.approx(55.0, abs=5.0)
        assert aggregate(vals, "p95") == pytest.approx(95.0, abs=10.0)

    def test_min_max_last(self):
        vals = [3.0, 1.0, 5.0, 2.0, 4.0]
        assert aggregate(vals, "min") == 1.0
        assert aggregate(vals, "max") == 5.0
        assert aggregate(vals, "last") == 4.0

    def test_bool_all_pass(self):
        assert aggregate([True, True, True], "bool_all") == 1.0
        assert aggregate([True, False, True], "bool_all") == 0.0
        assert aggregate([], "bool_all") == 0.0

    def test_empty_returns_zero(self):
        assert aggregate([], "p50") == 0.0
        assert aggregate([], "max") == 0.0


class TestPasses:
    def test_le(self):
        assert passes(0.04, "le", 0.05) is True
        assert passes(0.05, "le", 0.05) is True
        assert passes(0.06, "le", 0.05) is False

    def test_ge(self):
        assert passes(0.80, "ge", 0.80) is True
        assert passes(0.75, "ge", 0.80) is False

    def test_gt_lt(self):
        assert passes(1.0, "gt", 0.0) is True
        assert passes(0.0, "gt", 0.0) is False
        assert passes(-1.0, "lt", 0.0) is True

    def test_bool_threshold_ge(self):
        # bool True를 ge로 비교: measured 1.0 → PASS, 0.0 → FAIL
        assert passes(1.0, "ge", True) is True
        assert passes(0.0, "ge", True) is False


class TestKPIRegistry:
    def test_all_kpis_have_unique_ids(self):
        ids = [k.id for k in KPIS]
        assert len(ids) == len(set(ids))

    def test_kpi_by_id_finds(self):
        kpi = kpi_by_id("S1.1")
        assert kpi.scenario == "S1"
        assert kpi.compare == "le"

    def test_kpi_by_id_unknown_raises(self):
        with pytest.raises(KeyError):
            kpi_by_id("S99.99")

    def test_s1_3_s1_4_require_trained_model(self):
        """W9 결정: F1·FNR은 학습 모델 전제."""
        s1_3 = kpi_by_id("S1.3")
        s1_4 = kpi_by_id("S1.4")
        assert "trained_model" in s1_3.requires
        assert "trained_model" in s1_4.requires

    def test_s5_4_requires_es_and_trained_model(self):
        s5_4 = kpi_by_id("S5.4")
        assert "es" in s5_4.requires
        assert "trained_model" in s5_4.requires


class TestAvailableResources:
    def test_has_returns_bool(self):
        r = AvailableResources(pg=True, es=False)
        assert r.has("pg") is True
        assert r.has("es") is False
        assert r.has("nonexistent") is False  # getattr default False

    def test_trained_model_default_false(self):
        r = AvailableResources()
        assert r.trained_model is False

    def test_from_env_snapshot_maps_services(self):
        env = MagicMock()
        env.services.postgres = "UP"
        env.services.elasticsearch = "DOWN"
        env.services.redis = "UP"
        env.services.minio = "UNKNOWN"
        env.gpu = "N/A"

        with patch("lloydk.perf.harness._detect_trained_model", return_value=False):
            r = AvailableResources.from_env_snapshot(env, "noop")
        assert r.pg is True
        assert r.es is False
        assert r.redis is True
        assert r.minio is False
        assert r.llm is False  # noop
        assert r.gpu is False  # N/A
        assert r.trained_model is False

    def test_llm_real_provider(self):
        env = MagicMock()
        env.services.postgres = "DOWN"
        env.services.elasticsearch = "DOWN"
        env.services.redis = "DOWN"
        env.services.minio = "DOWN"
        env.gpu = "N/A"
        with patch("lloydk.perf.harness._detect_trained_model", return_value=False):
            r = AvailableResources.from_env_snapshot(env, "anthropic")
        assert r.llm is True


class TestDetectTrainedModel:
    def test_env_flag_force_true(self, monkeypatch):
        monkeypatch.setenv("LLOYDK_TRAINED_MODEL", "true")
        assert _detect_trained_model() is True

    def test_env_flag_force_false(self, monkeypatch):
        monkeypatch.setenv("LLOYDK_TRAINED_MODEL", "false")
        assert _detect_trained_model() is False

    def test_no_env_no_db_returns_false(self, monkeypatch):
        """DB 미가용 + env 미설정 → False (dryrun 환경 안전)."""
        monkeypatch.delenv("LLOYDK_TRAINED_MODEL", raising=False)
        # session_scope import에서 예외 발생하도록 mock
        with patch(
            "lloydk.db.session_scope",
            side_effect=RuntimeError("db not available"),
        ):
            assert _detect_trained_model() is False

    def test_env_flag_yes_no(self, monkeypatch):
        for v, expected in [("1", True), ("0", False), ("yes", True), ("no", False)]:
            monkeypatch.setenv("LLOYDK_TRAINED_MODEL", v)
            assert _detect_trained_model() is expected, f"value={v!r}"


class TestRequiresSkipsKPI:
    """KPI requires가 자원 부재 시 자동 SKIP을 유발하는지 — 헬퍼 수준 검증."""

    def test_unsatisfied_requires_returns_missing(self):
        r = AvailableResources(pg=False, es=True, trained_model=False)
        kpi = kpi_by_id("S1.4")  # requires=["trained_model"]
        missing = [name for name in kpi.requires if not r.has(name)]
        assert "trained_model" in missing

    def test_satisfied_requires_empty_missing(self):
        r = AvailableResources(pg=True, es=True, trained_model=True)
        kpi = kpi_by_id("S5.4")  # requires=["es", "trained_model"]
        missing = [name for name in kpi.requires if not r.has(name)]
        assert missing == []
