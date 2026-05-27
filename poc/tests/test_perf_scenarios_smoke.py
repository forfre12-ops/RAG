"""PSH scenarios.py 단위 smoke tests — 커버리지 보강용.

설계 원칙:
- 시나리오 함수가 import 가능한지·시그니처 정합인지·SPECS 등록 여부만 검증
- 실제 시나리오 실행은 `scripts/run_perf_scenarios.py`가 담당 (중복 회피)
- 본 파일은 m3_labeling 시드 변경·KPI 추가 같은 리팩토링 시 회귀 방지용
- 외부 의존 (PG/ES/LLM) 0
"""
from __future__ import annotations


from lloydk.perf import scenarios as sc
from lloydk.perf.harness import AvailableResources, ScenarioContext


class TestImports:
    def test_specs_registered(self):
        """SPECS 리스트에 18개 시나리오가 모두 등록."""
        scenario_ids = {s.id for s in sc.SPECS}
        expected = {f"S{i}" for i in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]}
        assert scenario_ids == expected, f"missing: {expected - scenario_ids}, extra: {scenario_ids - expected}"

    def test_all_runners_callable(self):
        """모든 ScenarioSpec.runner가 callable."""
        for spec in sc.SPECS:
            assert callable(spec.runner), f"{spec.id} runner not callable"

    def test_spec_metadata(self):
        """ScenarioSpec 필드 정합."""
        for spec in sc.SPECS:
            assert isinstance(spec.id, str) and spec.id.startswith("S")
            assert isinstance(spec.title, str) and spec.title
            assert isinstance(spec.requires, list)


class TestHelpers:
    def test_label_keywords_loaded(self):
        """_LABEL_KEYWORDS가 시드 v3 로드되어 4등급 모두 존재."""
        assert set(sc._LABEL_KEYWORDS.keys()) == {"TS", "S1", "S2", "S3"}
        for grade, kws in sc._LABEL_KEYWORDS.items():
            assert len(kws) >= 30, f"{grade} 키워드 부족: {len(kws)}"

    def test_s10_eval_set_builds_100(self):
        """S10 평가셋이 4등급 × 25 = 100쌍 생성."""
        eval_set = sc._build_s10_eval_set(n_per_grade=25)
        assert len(eval_set) == 100
        labels = {target for _, target in eval_set}
        assert labels == {"TS", "S1", "S2", "S3"}

    def test_s10_eval_set_small(self):
        """S10 평가셋 n_per_grade=5 → 20쌍."""
        eval_set = sc._build_s10_eval_set(n_per_grade=5)
        assert len(eval_set) == 20

    def test_evidence_texts_extraction(self):
        """_evidence_texts가 다양한 schema 처리."""
        assert sc._evidence_texts({"evidence": ["a", "b"]}) == ["a", "b"]
        assert sc._evidence_texts({"evidence": [{"text": "x"}]}) == ["x"]
        assert sc._evidence_texts({"evidence": [{"snippet": "y"}]}) == ["y"]
        assert sc._evidence_texts({"evidence": []}) == []
        assert sc._evidence_texts({}) == []
        assert sc._evidence_texts({"evidence": None}) == []
        assert sc._evidence_texts({"evidence": "not-a-list"}) == []

    def test_hdr_function(self):
        """_hdr가 필수 헤더 4종 반환."""
        h = sc._hdr()
        assert "X-API-Key" in h
        assert "X-Actor-Id" in h
        assert "X-Actor-Role" in h
        assert "X-Tenant-Id" in h
        assert h["X-Actor-Role"] == "kl_backend"

    def test_hdr_admin_role(self):
        """_hdr(role='admin')이 role 변경."""
        h = sc._hdr(role="admin")
        assert h["X-Actor-Role"] == "admin"

    def test_api_key_resolves(self):
        """_api_key가 settings.api_key 반환."""
        from lloydk.config import settings

        assert sc._api_key() == settings.api_key

    def test_time_call_measures(self):
        """_time_call이 (elapsed_ms, result) 반환."""
        elapsed, result = sc._time_call(lambda: 42)
        assert isinstance(elapsed, float)
        assert elapsed >= 0
        assert result == 42


class TestScenarioContext:
    def test_record_accumulates(self):
        ctx = ScenarioContext("S1", AvailableResources(), "dryrun")
        ctx.record("s1_1", 10.0)
        ctx.record("s1_1", 20.0)
        ctx.record("s1_1", 30.0)
        assert ctx.measurements["s1_1"] == [10.0, 20.0, 30.0]

    def test_skip_sets_state(self):
        ctx = ScenarioContext("S7", AvailableResources(), "dryrun")
        ctx.skip("PG unavailable")
        assert ctx.skipped is True
        assert "PG" in ctx.skip_reason


class TestResources:
    def test_resources_default_false(self):
        r = AvailableResources()
        assert r.pg is False
        assert r.es is False
        assert r.redis is False
        assert r.minio is False
        assert r.llm is False
        assert r.gpu is False
        assert r.trained_model is False

    def test_resources_has_method(self):
        r = AvailableResources(pg=True, es=False)
        assert r.has("pg") is True
        assert r.has("es") is False
        assert r.has("nonexistent") is False
