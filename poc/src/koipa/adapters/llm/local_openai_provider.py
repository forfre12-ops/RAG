"""OpenAI 호환 endpoint를 가진 로컬 LLM 일반 어댑터.

지원 대상:
- vLLM (Qwen3·Llama·Mistral 등) — 기본 :8001/v1
- Ollama — http://localhost:11434/v1 (api_key="ollama")
- LM Studio — http://localhost:1234/v1 (api_key="lm-studio")
- llama.cpp server, Text Generation WebUI 등 OpenAI 호환 endpoint 전반

납품 일반화 정책:
- GPU 보유 환경: 로컬 vLLM/Ollama로 운영비 0
- GPU 미보유 환경: 원격 Anthropic·OpenAI로 운영
- 같은 코드, settings.llm_provider만 변경하면 됨
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from koipa.adapters.llm.base import (
    LLMResponse,
    UsageRecord,
    estimate_cost_usd,
    retry_with_backoff,
)
from koipa.config import settings

logger = logging.getLogger(__name__)


# 작업 #18: 로컬/원격 OpenAI 호환 endpoint도 OpenAI SDK 사용 — 동일 재시도 분류.
# RateLimitError(429)/APITimeoutError/APIConnectionError + status>=500.
_RETRYABLE_EXC_NAMES = frozenset(
    {
        "RateLimitError",  # 429
        "APITimeoutError",  # 요청 타임아웃
        "APIConnectionError",  # 일시 네트워크
        "InternalServerError",  # 5xx
        "APIConnectionTimeoutError",
    }
)


def _is_retryable(exc: BaseException) -> bool:
    """429/5xx/타임아웃/연결 오류면 True. 그 외 4xx 등 항구적 오류는 False."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return type(exc).__name__ in _RETRYABLE_EXC_NAMES


class LocalOpenAIProvider:
    """OpenAI 호환 endpoint 일반 어댑터.

    호출자가 provider 이름을 'vllm'/'ollama'/'local_openai' 중 무엇으로 부르든 동작.
    실제 endpoint·model·api_key는 settings.local_llm_* 또는 인자로 결정.
    """

    name = "local_openai"

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        provider_label: Optional[str] = None,
    ) -> None:
        from openai import OpenAI

        # local_llm_* 우선, 없으면 vllm_* (하위호환)
        effective_base = (
            base_url or settings.local_llm_base_url or settings.vllm_base_url
        )
        effective_model = model or settings.local_llm_model or settings.vllm_model
        effective_key = api_key or settings.local_llm_api_key or "EMPTY"

        self._client = OpenAI(base_url=effective_base, api_key=effective_key)
        self.base_url = effective_base          # 호출부가 「이 서버가 사내인가」를 확인할 수 있게 드러낸다(규정 참고 표시의 반출 확인)
        self.model = effective_model
        self.enable_thinking = (
            enable_thinking
            if enable_thinking is not None
            else (settings.local_llm_enable_thinking or settings.vllm_enable_thinking)
        )
        if provider_label:
            self.name = provider_label
        # 작업 #18: 지수 백오프 기본값 — settings에 값 있으면 우선.
        self._max_retries = int(getattr(settings, "llm_max_retries", 3))
        self._base_delay = float(getattr(settings, "llm_retry_base_delay", 0.5))
        self._max_delay = float(getattr(settings, "llm_retry_max_delay", 8.0))

    def limit_calls(self, *, timeout_s: float, max_retries: int = 0) -> None:
        """이 인스턴스의 호출 한도를 조인다 — 사람이 화면에서 기다리는 호출이 OpenAI SDK 기본(시간 초과 600초 · 재시도 2회)에
        어댑터 재시도(기본 3회)까지 겹쳐 서버가 응답을 안 할 때 스레드를 몇 시간씩 붙잡지 않게 한다(최악 약 7,200초 — 설계서 §3.6).
        이 인스턴스만 바뀐다 — 합성·라벨링이 쓰는 다른 인스턴스와 기본값은 그대로다."""
        self._client = self._client.with_options(timeout=float(timeout_s), max_retries=0)
        self._max_retries = max(0, int(max_retries))

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        json_schema: Optional[dict] = None,
        no_reasoning: bool = False,
    ) -> LLMResponse:
        """json_schema 를 주면 서버에 구조화 출력(response_format)을 요구한다.

        no_reasoning=True 는 Ollama+Qwen3 의 추론을 끄라고 요청한다(규정 참고 표시 전용 — 아래 ⚠). 기본 False 라 다른 호출자
        (합성·라벨링·판정)의 요청은 종전과 똑같다.

        종전에는 프롬프트로 "JSON 만 출력하라"고 부탁만 했고, 모델이 인사말·코드펜스를
        덧붙이면 호출부가 파싱에 실패해 재시도했다. 스키마를 넘기면 서버(vLLM guided
        decoding · OpenAI json_schema)가 틀 밖 토큰 자체를 만들지 않는다.

        ⚠ 지원 여부는 **서버 구현·버전**에 달렸다. 지원하지 않으면 400 등으로 실패하는데,
        이 어댑터는 실패를 예외로 올리지 않고 success=False 응답으로 돌려주므로 호출부가
        스키마 없이 다시 부를 수 있다(generator 가 그렇게 한다). 어느 쪽이었는지는
        meta["json_schema"] 로 남는다.
        """
        start = time.perf_counter()

        # Qwen3 thinking 토글
        if self.enable_thinking and "qwen" in self.model.lower():
            directive = "/think"
        elif "qwen" in self.model.lower():
            directive = "/no_think"
        else:
            directive = None

        full_user = f"{prompt}\n\n{directive}" if directive else prompt

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": full_user})

        try:
            # Qwen3 thinking mode 제어 — Ollama/vLLM extra_body로 전달
            extra: dict = {}
            if "qwen" in self.model.lower():
                extra["think"] = bool(self.enable_thinking)
                # ⚠ Ollama 의 OpenAI 호환 endpoint(/v1)는 think·chat_template_kwargs·`/no_think` 를 **무시하고 추론을 돌린다**
                #   (0.34.2 실측, qwen3:14b: 완성 토큰 247·추론 909자 — max_tokens 가 작으면 추론에 다 쓰여 내용이 빈 문자열로 끝난다,
                #   finish_reason=length). `reasoning_effort: "none"` 만 추론을 끈다(완성 토큰 60·추론 0자). Ollama 로 부를 때만 넣는다 —
                #   vLLM 은 이 값의 허용 범위가 달라 "none" 을 400 으로 거절할 수 있다.
                #   ⚠ **요청한 호출만** 보낸다(no_reasoning) — 어댑터를 같이 쓰는 합성·라벨링의 출력이 이 기능 때문에 바뀌지 않게(독립 리뷰 R3).
                if no_reasoning and self.name == "ollama" and not self.enable_thinking:
                    extra["reasoning_effort"] = "none"

            # 구조화 출력 — OpenAI 호환 서버 공통 형식.
            kwargs: dict = {}
            if json_schema:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "synthetic_document",
                        "schema": json_schema,
                        "strict": True,
                    },
                }

            # 작업 #18: 429/5xx/타임아웃/연결 오류에 full-jitter 지수 백오프 재시도.
            resp = retry_with_backoff(
                lambda: self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    extra_body=extra or None,
                    **kwargs,
                ),
                is_retryable=_is_retryable,
                max_retries=self._max_retries,
                base_delay=self._base_delay,
                max_delay=self._max_delay,
                label=self.name,
            )
            choice = resp.choices[0]
            text = choice.message.content or ""
            finish_reason = str(getattr(choice, "finish_reason", None) or "unavailable")
            in_tok = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
            out_tok = getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0
            return LLMResponse(
                text=text,
                usage=UsageRecord(
                    provider=self.name,
                    model=self.model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=estimate_cost_usd(self.model, in_tok, out_tok),
                    latency_ms=int((time.perf_counter() - start) * 1000),
                ),
                meta={
                    "thinking": self.enable_thinking,
                    "endpoint": "local_openai",
                    "finish_reason": finish_reason,
                    "json_schema": bool(json_schema),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return LLMResponse(
                text="",
                usage=UsageRecord(
                    provider=self.name,
                    model=self.model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    success=False,
                    error_code=type(exc).__name__,
                ),
                meta={"error": str(exc), "json_schema": bool(json_schema)},
            )

    def count_tokens(self, text: str) -> int:
        # 정확한 토크나이저는 모델별 상이 — 보수적 추정 (한국어 평균)
        return max(1, len(text) // 3)


# ============================================================
# Convenience presets for common local servers
# ============================================================


def ollama_provider(
    model: str = "qwen3:14b", base_url: str = "http://localhost:11434/v1"
) -> LocalOpenAIProvider:
    """Ollama OpenAI 호환 endpoint."""
    return LocalOpenAIProvider(
        base_url=base_url,
        model=model,
        api_key="ollama",
        provider_label="ollama",
    )


def lm_studio_provider(
    model: str, base_url: str = "http://localhost:1234/v1"
) -> LocalOpenAIProvider:
    """LM Studio OpenAI 호환 endpoint."""
    return LocalOpenAIProvider(
        base_url=base_url,
        model=model,
        api_key="lm-studio",
        provider_label="lm_studio",
    )


def vllm_provider(
    model: Optional[str] = None, base_url: Optional[str] = None
) -> LocalOpenAIProvider:
    """vLLM OpenAI 호환 endpoint — VLLMProvider 호환."""
    return LocalOpenAIProvider(
        base_url=base_url,
        model=model,
        api_key="EMPTY",
        provider_label="vllm",
    )
