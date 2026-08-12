"""W9 일반화 — LocalOpenAIProvider OpenAI 호환 endpoint 회로 검증 (smoke).

실제 vLLM·Ollama·LM Studio 서버 없이 `openai.OpenAI` 클라이언트를 mock하여
다음 3가지 회로를 검증한다:
  1. base_url 환경변수/인자가 OpenAI 클라이언트 생성에 반영되는가
  2. model·messages·temperature·max_tokens가 OpenAI 스펙대로 전송되는가
  3. ChatCompletion 응답 구조(choices[0].message.content + usage)를 정확히 파싱하는가
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.model_download
# ---- 어댑터 존재 확인 (없으면 skip) ---------------------------------------

try:
    from koipa.adapters.llm.local_openai_provider import (  # noqa: F401
        LocalOpenAIProvider,
        lm_studio_provider,
        ollama_provider,
        vllm_provider,
    )
except ImportError as exc:  # pragma: no cover - W9 일반화 미적용 환경 대비
    pytest.skip(f"LocalOpenAIProvider 어댑터 미존재: {exc}", allow_module_level=True)


# ---- helper: OpenAI SDK mock 빌더 ----------------------------------------


def _fake_chat_completion(
    content: str = "안녕하세요", in_tok: int = 11, out_tok: int = 7
):
    """openai.types.chat.ChatCompletion과 같은 형태의 가짜 응답."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, role="assistant"),
                finish_reason="stop",
                index=0,
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=in_tok,
            completion_tokens=out_tok,
            total_tokens=in_tok + out_tok,
        ),
        model="dummy",
        id="chatcmpl-mock-1",
    )


def _build_mock_openai_client(
    content: str = "안녕하세요", in_tok: int = 11, out_tok: int = 7
):
    """openai.OpenAI() 호출을 가로채는 MagicMock + chat.completions.create mock 반환."""
    client = MagicMock(name="OpenAIClient")
    client.chat.completions.create.return_value = _fake_chat_completion(
        content, in_tok, out_tok
    )
    # base_url은 어댑터가 인자로 받은 값을 그대로 client.base_url에 저장하도록 흉내
    return client


# ============================================================================
# 검증 1: base_url 인자/환경변수가 endpoint에 반영
# ============================================================================


class TestBaseUrlPropagation:
    def test_base_url_arg_is_passed_to_openai_client(self):
        """LocalOpenAIProvider(base_url=...) → OpenAI(base_url=..., api_key=...) 호출."""
        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value = _build_mock_openai_client()
            _ = LocalOpenAIProvider(
                base_url="http://my-vllm.internal:9999/v1",
                model="custom-model",
                api_key="secret-key",
                provider_label="test",
            )
            mock_openai.assert_called_once()
            kwargs = mock_openai.call_args.kwargs
            assert kwargs["base_url"] == "http://my-vllm.internal:9999/v1"
            assert kwargs["api_key"] == "secret-key"

    def test_ollama_preset_uses_11434(self):
        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value = _build_mock_openai_client()
            _ = ollama_provider(model="qwen3:14b")
            kwargs = mock_openai.call_args.kwargs
            assert "11434" in kwargs["base_url"]
            assert kwargs["api_key"] == "ollama"

    def test_lm_studio_preset_uses_1234(self):
        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value = _build_mock_openai_client()
            _ = lm_studio_provider(model="local-llama-3-8b")
            kwargs = mock_openai.call_args.kwargs
            assert "1234" in kwargs["base_url"]
            assert kwargs["api_key"] == "lm-studio"

    def test_vllm_preset_uses_empty_api_key(self):
        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value = _build_mock_openai_client()
            _ = vllm_provider(model="Qwen/Qwen3-14B", base_url="http://gpu-box:8001/v1")
            kwargs = mock_openai.call_args.kwargs
            assert kwargs["base_url"] == "http://gpu-box:8001/v1"
            assert kwargs["api_key"] == "EMPTY"


# ============================================================================
# 검증 2: 호출 파라미터가 OpenAI 스펙대로 전송
# ============================================================================


class TestRequestParameters:
    def test_chat_completions_called_with_openai_spec(self):
        """create() 인자에 model·messages·max_tokens·temperature가 OpenAI 표준 형식으로 들어가야."""
        mock_client = _build_mock_openai_client()
        with patch("openai.OpenAI", return_value=mock_client):
            p = LocalOpenAIProvider(
                base_url="http://x/v1",
                model="custom-model",
                api_key="EMPTY",
                provider_label="test",
                enable_thinking=False,
            )
            _ = p.generate(
                "신규 사업 검토 문서",
                system="당신은 한국 영업비밀 분류 전문가",
                max_tokens=512,
                temperature=0.3,
            )

        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        # OpenAI 스펙 키
        assert call_kwargs["model"] == "custom-model"
        assert call_kwargs["max_tokens"] == 512
        assert call_kwargs["temperature"] == 0.3
        # messages 구조
        msgs = call_kwargs["messages"]
        assert isinstance(msgs, list) and len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "당신은 한국 영업비밀 분류 전문가"
        assert msgs[1]["role"] == "user"
        assert "신규 사업 검토 문서" in msgs[1]["content"]

    def test_qwen_thinking_directive_appended(self):
        """Qwen 모델 + enable_thinking=True면 user 메시지 끝에 /think 디렉티브가 붙어야."""
        mock_client = _build_mock_openai_client()
        with patch("openai.OpenAI", return_value=mock_client):
            p = LocalOpenAIProvider(
                base_url="http://x/v1",
                model="Qwen/Qwen3-14B",
                api_key="EMPTY",
                provider_label="test",
                enable_thinking=True,
            )
            _ = p.generate("질문", max_tokens=64)

        msgs = mock_client.chat.completions.create.call_args.kwargs["messages"]
        # system 없이 user 1개
        assert msgs[-1]["role"] == "user"
        assert "/think" in msgs[-1]["content"]

    def test_qwen_no_think_when_disabled(self):
        mock_client = _build_mock_openai_client()
        with patch("openai.OpenAI", return_value=mock_client):
            p = LocalOpenAIProvider(
                base_url="http://x/v1",
                model="Qwen/Qwen3-14B",
                api_key="EMPTY",
                provider_label="test",
                enable_thinking=False,
            )
            _ = p.generate("질문", max_tokens=64)
        msgs = mock_client.chat.completions.create.call_args.kwargs["messages"]
        assert "/no_think" in msgs[-1]["content"]


# ============================================================================
# 검증 3: ChatCompletion 응답 파싱
# ============================================================================


class TestResponseParsing:
    def test_parses_choices_message_content(self):
        mock_client = _build_mock_openai_client(
            content="분류: TS — 특급기밀", in_tok=42, out_tok=15
        )
        with patch("openai.OpenAI", return_value=mock_client):
            p = LocalOpenAIProvider(
                base_url="http://x/v1",
                model="test-model",
                api_key="EMPTY",
                provider_label="test",
                enable_thinking=False,
            )
            resp = p.generate("샘플 프롬프트", max_tokens=128)

        assert resp.text == "분류: TS — 특급기밀"
        assert resp.usage.success is True
        assert resp.usage.input_tokens == 42
        assert resp.usage.output_tokens == 15
        assert resp.usage.provider == "test"
        assert resp.usage.model == "test-model"
        assert resp.usage.latency_ms >= 0
        assert resp.meta["finish_reason"] == "stop"

    def test_handles_empty_content_gracefully(self):
        """choices[0].message.content가 None이어도 빈 문자열로 안전 처리."""
        mock_client = _build_mock_openai_client(content=None, in_tok=5, out_tok=0)  # type: ignore[arg-type]
        # 위 helper는 content=None을 그대로 넣어줌 → 어댑터는 "" 로 정규화해야
        with patch("openai.OpenAI", return_value=mock_client):
            p = LocalOpenAIProvider(
                base_url="http://x/v1",
                model="test-model",
                api_key="EMPTY",
                provider_label="test",
                enable_thinking=False,
            )
            resp = p.generate("프롬프트", max_tokens=8)
        assert resp.text == ""
        assert resp.usage.success is True
