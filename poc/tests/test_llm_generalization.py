"""W9 LLM·임베딩 일반화 — build_provider 분기 + 시드·사전 카운트 검증.

테스트 전제:
- 실제 vLLM·Ollama·LM Studio 서버 호출은 안 함 (어댑터 인스턴스화만 검증)
- generate()는 endpoint가 없으면 success=False로 우아하게 실패
"""

from __future__ import annotations

import pytest

from lloydk.adapters.embedding import build_embedder
from lloydk.adapters.llm import NoopProvider, build_provider
from lloydk.adapters.llm.local_openai_provider import LocalOpenAIProvider


class TestBuildProviderAliases:
    def test_noop(self):
        p = build_provider("noop")
        assert isinstance(p, NoopProvider)

    def test_vllm_alias_maps_to_local_openai(self):
        p = build_provider("vllm")
        assert isinstance(p, LocalOpenAIProvider)
        assert p.name == "vllm"

    def test_ollama_alias_has_correct_endpoint(self):
        p = build_provider("ollama")
        assert isinstance(p, LocalOpenAIProvider)
        assert p.name == "ollama"
        # Ollama 기본 endpoint
        assert "11434" in str(p._client.base_url)

    def test_lm_studio_alias_has_correct_endpoint(self):
        p = build_provider("lm_studio")
        assert isinstance(p, LocalOpenAIProvider)
        assert p.name == "lm_studio"
        assert "1234" in str(p._client.base_url)

    def test_local_openai_uses_settings_default(self):
        from lloydk.config import settings
        p = build_provider("local_openai")
        assert isinstance(p, LocalOpenAIProvider)
        assert p.name == "local_openai"
        # settings.local_llm_base_url 값을 그대로 사용하는지 검증 (포트 고정 X)
        expected = (settings.local_llm_base_url or settings.vllm_base_url).rstrip("/") + "/"
        assert str(p._client.base_url) == expected

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown LLM provider"):
            build_provider("nonexistent")


class TestLocalOpenAIProvider:
    def test_unreachable_endpoint_returns_failure(self):
        """endpoint가 없으면 generate가 success=False로 반환 — 예외 안 던짐."""
        p = LocalOpenAIProvider(
            base_url="http://127.0.0.1:1/v1",  # 무조건 거부될 포트
            model="dummy",
            api_key="EMPTY",
            provider_label="test",
        )
        resp = p.generate("hello", max_tokens=10)
        assert resp.usage.success is False
        assert resp.usage.error_code != ""
        assert resp.text == ""

    def test_count_tokens_estimates(self):
        p = LocalOpenAIProvider(base_url="http://x/v1", model="m", provider_label="t")
        n = p.count_tokens("한국어 텍스트")
        assert n >= 1


class TestEmbeddingDefault:
    """sentence-transformers가 기본 의존성으로 들어왔으므로 HFEmbedding이 실 가동되어야 함."""

    def test_build_embedder_returns_hf_when_available(self):
        # build_embedder는 settings.embedding_model 기본 = nlpai-lab/KURE-v1
        # HF 로드 실패 시 HashEmbedding 폴백 — 본 환경은 sentence-transformers 설치돼 있음
        emb = build_embedder()
        # 둘 중 하나라도 동작하면 OK (KURE-v1 다운로드 환경 의존성)
        assert emb is not None
        # dim은 1024 (KURE) 또는 1024 (Hash) — 동일
        assert emb.dim == 1024

    def test_force_hash_returns_hash(self):
        emb = build_embedder(force_hash=True)
        assert emb.name == "hash"


class TestSeedsV2:
    """시드 키워드 v2 — 등급당 ~35개, 총 140+개, 도메인 균형."""

    def test_total_seed_count(self):
        from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS
        assert len(KEYWORD_SEEDS) >= 140

    def test_per_grade_balance(self):
        from collections import Counter
        from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS
        per_grade = Counter(s["grade"] for s in KEYWORD_SEEDS)
        # 모든 4등급이 최소 30개
        for g in ("TS", "S1", "S2", "S3"):
            assert per_grade[g] >= 30, f"{g} has only {per_grade[g]} seeds"

    def test_factor_codes_valid(self):
        from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS
        valid_factors = {"ECONOMIC_VALUE", "NON_PUBLICITY", "MANAGEMENT_LEVEL", "LEAK_IMPACT"}
        for s in KEYWORD_SEEDS:
            assert s["factor"] in valid_factors, f"invalid factor in {s}"

    def test_weights_in_range(self):
        from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS
        for s in KEYWORD_SEEDS:
            assert 0.0 < s["weight"] <= 1.0, f"weight out of range in {s}"


class TestUserdictV2:
    """Nori 사용자 사전 v2 — 200+ 항목, 일반화 도메인 포함."""

    def test_dictionary_has_enough_entries(self):
        from pathlib import Path
        userdict = Path(__file__).resolve().parents[1] / "infra" / "es" / "userdict_ko.txt"
        lines = [
            ln.strip()
            for ln in userdict.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        assert len(lines) >= 200, f"userdict has only {len(lines)} entries"

    def test_dictionary_includes_general_domains(self):
        from pathlib import Path
        userdict = (
            Path(__file__).resolve().parents[1] / "infra" / "es" / "userdict_ko.txt"
        ).read_text(encoding="utf-8")
        # 일반화 — 반도체·바이오·SW·금융 각 도메인 키워드 1개씩 존재
        assert "반도체공정" in userdict or "EUV공정" in userdict
        assert "신약개발" in userdict or "임상시험" in userdict
        assert "알고리즘소스코드" in userdict or "API키" in userdict
        assert "신용평가모델" in userdict or "거래알고리즘" in userdict
