"""합성 생성 폼의 선택지가 서버가 받는 값과 같은가 — 드리프트 잠금(제공자·도메인).

왜(2026-09-05). 이 폼의 드롭다운 **둘 다** HTML 에 손으로 적혀 있었고 서버 정본과 갈렸다.

제공자 — 실측:
    화면에 있는데 서버가 422 로 거부   vllm_qwen · vllm_exaone   ← 「온프렘」이라 적힌 둘
    서버는 받는데 화면에 없음          ollama · vllm · local_openai · lm_studio
폐쇄망 프로파일의 기본 제공자가 ollama 인데 콘솔로는 그 값을 지정할 방법이 없었다.
그리고 「온프렘」이라고 적힌 두 선택지가 정확히 안 되는 두 개였다.

도메인 — 실측:
    서버는 받는데 화면에 없음   배터리 · 화학_제약 · 소프트웨어 · 경영정보 · 기타
여기는 422 가 아니라 더 나쁜 것이 있었다. 화면의 「bio (바이오·제약)」이 별칭으로
바이오_농업 에 접혀, **제약을 고르면 품종·종자 문서가 생성됐다**. 별칭은 같은 산업의
두 이름에만 쓴다(generator.DOMAIN_ALIASES 주석 참조).

이 시험이 잠그는 것은 셋이다.
  ① healthz 가 두 목록을 내려준다(콘솔이 채울 근거).
  ② 서버가 광고하는 값은 전부 합성 생성 API 가 받는다(광고와 계약이 갈리지 않는다).
  ③ 콘솔이 목록을 다시 손으로 적지 않는다.

③ 이 핵심이다. ①②만 있으면 다음 사람이 "편의상" 화면에 목록을 다시 박아 넣어도 안 걸린다.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")

from koipa.config import _VALID_LLM_PROVIDER  # noqa: E402
from koipa.schemas.synthesis import LLM_PROVIDER_PATTERN  # noqa: E402

_POC = Path(__file__).resolve().parents[1]
ADMIN_HTML = _POC / "src" / "koipa" / "api" / "static" / "admin.html"

# 종전에 화면에 적혀 있었으나 서버가 거부하던 값. 되살아나면 이 시험이 잡는다.
REJECTED_BY_SERVER = ("vllm_qwen", "vllm_exaone")


@pytest.fixture(scope="module")
def health_payload() -> dict:
    from fastapi.testclient import TestClient

    from koipa.api.app import app

    response = TestClient(app).get("/api/v1/healthz")
    assert response.status_code == 200, response.text
    return response.json()


def test_healthz_advertises_supported_providers(health_payload: dict) -> None:
    """① 콘솔이 드롭다운을 채울 근거를 서버가 준다."""
    advertised = health_payload.get("llm_providers_supported")
    assert isinstance(advertised, list) and advertised, (
        "healthz 가 llm_providers_supported 를 주지 않는다 — 콘솔이 채울 근거가 없어지면 "
        "화면은 다시 목록을 손으로 적게 된다."
    )
    assert advertised == sorted(_VALID_LLM_PROVIDER), (
        "healthz 가 광고하는 목록이 config 정본과 다르다. 정본은 config._VALID_LLM_PROVIDER 다."
    )


def test_every_advertised_provider_is_accepted_by_synth_api(health_payload: dict) -> None:
    """② 광고한 값은 전부 POST /synth/generate 가 받는다 — 이것이 깨졌던 계약이다."""
    pattern = re.compile(LLM_PROVIDER_PATTERN)
    rejected = [p for p in health_payload["llm_providers_supported"] if not pattern.match(p)]
    assert not rejected, (
        f"healthz 가 광고하는데 합성 생성 API 가 거부하는 값: {rejected}. "
        "화면에 띄워 놓고 누르면 422 가 되는 자리다."
    )


def test_console_does_not_hardcode_provider_options() -> None:
    """③ 콘솔이 목록을 다시 손으로 적지 않는다."""
    html = ADMIN_HTML.read_text(encoding="utf-8")

    match = re.search(r'<select[^>]*id="sy-provider"[^>]*>(.*?)</select>', html, re.S)
    assert match, "admin.html 에서 sy-provider 셀렉트를 찾지 못했다 — id 가 바뀌었으면 이 시험도 고칠 것."
    assert "<option" not in match.group(1), (
        "sy-provider 안에 <option> 이 다시 박혔다. 목록은 healthz 의 llm_providers_supported "
        "로 채운다(syncLlmProviders). 손으로 적으면 서버 정본과 갈린다 — 실제로 갈렸었다."
    )

    for name in REJECTED_BY_SERVER:
        assert f'value="{name}"' not in html, (
            f"서버가 422 로 거부하는 {name!r} 이 화면 선택지로 되살아났다."
        )


def test_console_labels_provider_as_the_generator() -> None:
    """라벨이 동작과 반대로 적혀 있었다 — 「비용 추정용」이 아니라 실제 생성기를 고른다.

    종전 라벨은 「비용 추정용 제공자」였고 주석에도 "이 칸은 생성기를 고르지 않는다"고
    적혀 있었다. 지금은 이 값이 워커로 넘어가 생성기를 만든다.
    """
    html = ADMIN_HTML.read_text(encoding="utf-8")
    # 파일 전체가 아니라 **라벨 요소**만 본다. 주석에는 옛 문구가 "이렇게 적혀 있었다" 로
    # 남아 있고, 그 기록까지 금지하면 왜 고쳤는지가 사라진다.
    before = html.split('<select id="sy-provider"', 1)[0]
    labels = re.findall(r'<label class="fld">(.*?)</label>', before, re.S)
    assert labels, "sy-provider 앞에서 라벨을 찾지 못했다 — 마크업이 바뀌었으면 이 시험도 고칠 것."
    label = labels[-1].strip()
    assert "비용 추정" not in label, (
        f"라벨이 옛 문구로 돌아갔다({label!r}). 이 칸은 실제 생성기를 고른다."
    )


# ── 도메인 ─────────────────────────────────────────────────────────────

def test_healthz_advertises_supported_domains(health_payload: dict) -> None:
    """① 도메인 목록도 서버가 준다 — 그리고 **정본으로 접힌** 상태로 준다.

    접기 전 이름을 그대로 내보내면 semiconductor 와 반도체 가 나란히 떠서 고르는
    사람에게는 다른 선택지로 보이는데 결과는 같다.
    """
    from koipa.modules.m1_synthesis.generator import (  # noqa: PLC0415
        DOMAIN_DOC_TYPES,
        canonical_domain,
    )

    advertised = health_payload.get("synth_domains_supported")
    assert isinstance(advertised, list) and advertised, (
        "healthz 가 synth_domains_supported 를 주지 않는다 — 화면이 목록을 다시 손으로 적게 된다."
    )
    folded = [d for d in advertised if canonical_domain(d) != d]
    assert not folded, f"접히는 이름이 그대로 광고됐다(같은 것이 두 줄로 뜬다): {folded}"
    missing = [d for d in advertised if d not in DOMAIN_DOC_TYPES]
    assert not missing, f"프롬프트가 없는 도메인을 광고한다: {missing}"


def test_every_advertised_domain_is_accepted_by_synth_api(health_payload: dict) -> None:
    """② 광고한 도메인은 전부 POST /synth/generate 가 받는다."""
    from koipa.schemas.synthesis import SYNTH_DOMAIN_PATTERN  # noqa: PLC0415

    pattern = re.compile(SYNTH_DOMAIN_PATTERN)
    rejected = [d for d in health_payload["synth_domains_supported"] if not pattern.match(d)]
    assert not rejected, f"광고하는데 API 가 거부하는 도메인: {rejected}"


def test_console_does_not_hardcode_domain_options() -> None:
    """③ 콘솔이 도메인 목록을 다시 손으로 적지 않는다."""
    html = ADMIN_HTML.read_text(encoding="utf-8")
    match = re.search(r'<select[^>]*id="sy-domain"[^>]*>(.*?)</select>', html, re.S)
    assert match, "admin.html 에서 sy-domain 셀렉트를 찾지 못했다 — id 가 바뀌었으면 이 시험도 고칠 것."
    assert "<option" not in match.group(1), (
        "sy-domain 안에 <option> 이 다시 박혔다. 목록은 healthz 의 synth_domains_supported "
        "로 채운다(syncSynthDomains). 손으로 적으면 생성기가 아는 것보다 적어진다 — "
        "실제로 배터리·화학_제약·소프트웨어·경영정보·기타를 고를 수 없었다."
    )


def test_bio_is_pharma_not_agriculture() -> None:
    """별칭은 **같은 산업**의 두 이름에만 쓴다 — bio 는 접지 않는다.

    종전엔 bio -> 바이오_농업 으로 접혀, 화면에서 「바이오·제약」을 고른 사람이
    품종 육성 기록·종자 처리 공정서를 받았다. canonical_domain() 이 프롬프트 직전에
    적용되므로 이 접기는 집계뿐 아니라 생성 내용까지 바꿨다.
    """
    from koipa.modules.m1_synthesis.generator import (  # noqa: PLC0415
        DOMAIN_ALIASES,
        DOMAIN_DOC_TYPES,
        canonical_domain,
    )

    assert "bio" not in DOMAIN_ALIASES, "bio 를 다시 접으면 제약을 고른 사람이 농업 문서를 받는다."
    assert canonical_domain("bio") == "bio"
    assert "신약" in DOMAIN_DOC_TYPES["bio"]
    assert "종자" in DOMAIN_DOC_TYPES["바이오_농업"]

    # 남은 별칭은 전부 같은 산업이어야 한다 — 대상 도메인이 프롬프트를 갖고 있는지까지 본다.
    for alias, canon in DOMAIN_ALIASES.items():
        assert canon in DOMAIN_DOC_TYPES, f"{alias} 가 프롬프트 없는 {canon} 으로 접힌다"
