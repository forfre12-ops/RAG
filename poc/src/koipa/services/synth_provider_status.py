"""LLM provider 가용성 조회 — 콘솔이 "우리가 실제로 가진 것만" 보여주게 한다.

배경(2026-09-28): healthz.llm_providers_supported 는 스키마가 받는 값 전부
(정본 config._VALID_LLM_PROVIDER, 9개)를 그대로 내려준다 — **실제로 이 서버가 그 provider로
지금 생성할 수 있는지와 무관하다.** 로컬(ollama·vllm·local_openai·lm_studio)은 그 서버가
떠 있어야 하고, 상용(anthropic·openai)은 키가 서버에 설정돼 있어야 한다. 이 모듈이 그 구분을
추가한다 — _VALID_LLM_PROVIDER 는 그대로 두고(스키마 호환 유지), 화면에 무엇을 "고를 수
있는 것"으로 보여줄지만 여기서 가른다.

⚠ /healthz(무인증, KL 규약서에 실려 발주처가 직접 호출) 에는 이 조회를 넣지 않는다 — 로컬
  서버가 없으면 후보마다 타임아웃을 기다려야 하고, 그 지연을 계약 상태 확인 경로에 얹으면
  안 된다. 그래서 별도 경로(/synth/providers/status)로 두고, 콘솔이 학습 후보 카드를 열 때만
  부른다.

⚠ 실제 토큰을 써서 호출해 보는 것이 아니다 — 상용은 키 설정 여부만, 로컬은 서버가 그
  주소에 응답하는지만 본다("보유" 확인이지 "성공적으로 생성됨" 확인이 아니다). 비용 없이
  빠르게 재확인할 수 있어야 콘솔에서 매번 불러도 부담이 없다.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from koipa.config import settings

_PROBE_TIMEOUT_SEC = 0.6
_CACHE_TTL_SEC = 5.0

# [2026-09-28] 상용은 GPT·Claude 둘만 advertise 한다. google/gemini 는 코드에서
# anthropic·openai 와 같은 자리(LocalOpenAIProvider 분기, adapters/llm/__init__.py:41)를 이미
# 쓰고 있어 계속 받지만(하위호환·API 스키마 불변), 콘솔 드롭다운에서는 중복이라 뺀다.
_ADVERTISED_COMMERCIAL: tuple[str, ...] = ("anthropic", "openai")
_LOCAL_PROVIDERS: tuple[str, ...] = ("ollama", "vllm", "local_openai", "lm_studio")

_LOCAL_DEFAULT_BASE_URL = {
    "ollama": "http://localhost:11434/v1",
    "lm_studio": "http://localhost:1234/v1",
}

_cache: dict[str, tuple[float, bool]] = {}


def _local_base_url(name: str) -> str:
    if name in _LOCAL_DEFAULT_BASE_URL:
        return _LOCAL_DEFAULT_BASE_URL[name]
    # vllm · local_openai — settings.local_llm_base_url 공용.
    return getattr(settings, "local_llm_base_url", "") or "http://localhost:8001/v1"


def _probe_http(base_url: str) -> bool:
    """서버가 그 주소에 떠 있는가 — 내용은 안 본다, 연결만 본다.

    HTTP 오류(404·401 등)도 "서버가 응답했다"로 본다. 연결 자체가 안 되거나
    타임아웃일 때만 "없다"로 본다 — 예를 들어 인증 실패(401)는 서버가 있다는 뜻이다.
    """
    url = base_url.rstrip("/") + "/models"
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=_PROBE_TIMEOUT_SEC)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001 — 연결 실패 사유는 다양하다(거부·타임아웃·DNS)
        return False


def _cached_probe(name: str, base_url: str) -> bool:
    """같은 화면 새로고침 안에서 로컬 서버를 반복해서 두드리지 않는다(TTL 5초)."""
    now = time.monotonic()
    hit = _cache.get(name)
    if hit and now - hit[0] < _CACHE_TTL_SEC:
        return hit[1]
    ok = _probe_http(base_url)
    _cache[name] = (now, ok)
    return ok


def provider_status() -> list[dict]:
    """advertise 대상 provider 각각의 가용성. noop→상용→로컬 순서."""
    out: list[dict] = [
        {"name": "noop", "kind": "deterministic", "available": True, "reason": ""},
    ]
    for name in _ADVERTISED_COMMERCIAL:
        key_field = f"{name}_api_key"
        has_key = bool(getattr(settings, key_field, "") or "")
        out.append(
            {
                "name": name,
                "kind": "commercial",
                "available": has_key,
                "reason": "" if has_key else f"서버에 {key_field.upper()} 미설정",
            }
        )
    for name in _LOCAL_PROVIDERS:
        base_url = _local_base_url(name)
        ok = _cached_probe(name, base_url)
        out.append(
            {
                "name": name,
                "kind": "local",
                "available": ok,
                "reason": "" if ok else f"{base_url} 응답 없음",
            }
        )
    return out
