"""배포 이미지에 상용 LLM SDK(anthropic·openai)가 실려 있는지 — 콘솔에서 고르는 공급자가 실제로 도는지.

배경(2026-09-25). 관리자 콘솔의 합성 생성 카드는 공급자 드롭다운을 healthz 의 `llm_providers_supported`
(anthropic·openai·google·gemini·로컬 계열 9개)로 채운다. 그런데 이미지가 설치하는 extras 에 `llm` 이 없어
`anthropic` 패키지가 이미지에 없었다 — 워커 이미지에서 `build_provider("anthropic")` 이
`ModuleNotFoundError: No module named 'anthropic'` 로 죽었다(실측). 콘솔은 Claude 를 고르게 해 놓고
실제로는 못 부르는 상태였다. (openai 는 기본 의존성이라 살아 있었고, OPENAI_API_KEY 만 있으면 됐다.)

이 시험이 지키는 것:
  · 이미지 3종(api·api.prod·worker)이 `llm` extra 를 설치한다
  · `llm` extra 가 anthropic·openai 를 선언한다
  · 공급자 어댑터가 가져오는 외부 SDK 가 전부 기본 의존성이거나 `llm` extra 에 있다(새 공급자를 더하고 SDK 를 빼먹지 않게)
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
IMAGES = ("Dockerfile.api", "Dockerfile.api.prod", "Dockerfile.worker")


def _installer_extras(dockerfile: str) -> list[str]:
    """`docker_install_locked.sh` 에 넘기는 extras 이름들(줄이음 `\\` 을 이어서 읽는다)."""
    text = (POC / dockerfile).read_text(encoding="utf-8").replace("\r\n", "\n")
    joined = re.sub(r"\\\n\s*", " ", text)
    # 주석에도 스크립트 이름이 나온다 — 실제 호출(`bash ./scripts/docker_install_locked.sh …`)만 본다.
    m = re.search(r"bash\s+\S*docker_install_locked\.sh\s+([^\n&]+)", joined)
    assert m, f"{dockerfile} 에서 docker_install_locked.sh 호출을 못 찾았다"
    return m.group(1).split()


def _pyproject() -> dict:
    return tomllib.loads((POC / "pyproject.toml").read_text(encoding="utf-8"))


def _names(requirements: list[str]) -> set[str]:
    return {re.split(r"[<>=!~\[ ;]", r, maxsplit=1)[0].strip().lower() for r in requirements}


@pytest.mark.parametrize("dockerfile", IMAGES)
def test_the_image_installs_the_llm_extra(dockerfile):
    extras = _installer_extras(dockerfile)
    assert "llm" in extras, (
        f"{dockerfile} 가 설치하는 extras {extras} 에 llm 이 없다 — 콘솔에서 anthropic 을 골라도 "
        "ModuleNotFoundError 로 실패한다"
    )


def test_the_llm_extra_declares_both_commercial_sdks():
    py = _pyproject()
    assert {"anthropic", "openai"} <= _names(py["project"]["optional-dependencies"]["llm"])


def test_every_third_party_sdk_a_provider_imports_is_installed_by_the_images():
    py = _pyproject()
    base = _names(py["project"]["dependencies"])
    llm = _names(py["project"]["optional-dependencies"]["llm"])
    sdks = {"anthropic", "openai"}                     # 어댑터가 부르는 원격 SDK
    seen: set[str] = set()
    for f in (POC / "src" / "koipa" / "adapters" / "llm").glob("*_provider.py"):
        for m in re.finditer(r"^\s*(?:from|import)\s+(\w+)", f.read_text(encoding="utf-8"), re.M):
            if m.group(1) in sdks:
                seen.add(m.group(1))
    assert seen, "공급자 어댑터가 어떤 SDK 도 안 가져온다 — 이 시험이 아무것도 보고 있지 않다"
    missing = sorted(s for s in seen if s not in base and s not in llm)
    assert not missing, f"어댑터가 가져오는데 기본 의존성에도 llm extra 에도 없는 SDK: {missing}"
