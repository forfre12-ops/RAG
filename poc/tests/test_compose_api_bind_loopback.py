"""앱은 루프백에만 선다 — 외부 노출은 프록시가 한다.

왜(2026-09-08). 2026-08-19 에 KL 서버 223 에서 실측된 것:

    docker ps → 0.0.0.0:8000->8000/tcp   앱이 평문 HTTP 로 외부에 직접 붙어 있었다
    :443 닫힘 · 보안 헤더 0개 · /docs·/openapi.json 무인증 200
    무인증 login.html 본문에 admin JWT(roles=[admin]) → KL 망 밖에서 그대로 수신됨

그때 prod.yml·dual.yml·dr-staging.yml 은 127.0.0.1 로 고쳤는데 **폐쇄망 배포본
(airgap.yml)만 빠져 있었다.** 영업비밀 문서를 다루는 바로 그 배포본이다.

이 시험이 잠그는 것:

  ① 배포용 compose 의 api 포트는 루프백에 묶인다(주소 없는 0.0.0.0 금지)
  ② 기본값이 안전하다 — .env 를 안 건드려도 외부에 열리지 않는다
  ③ 번들 .env 템플릿이 그 사실을 알려 준다

⚠ 개발용 docker-compose.yml 은 대상이 아니다. 로컬에서 컨테이너 밖 접근이 필요하고
  거기엔 영업비밀 문서가 없다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]

# 배포에 쓰이는 compose — 여기서 앱이 외부에 직접 서면 안 된다.
_DEPLOY_COMPOSES = (
    "docker-compose.airgap.yml",
    "docker-compose.prod.yml",
    "docker-compose.dual.yml",
    "docker-compose.dr-staging.yml",
)

# "127.0.0.1:8000:8000" 또는 "${API_BIND:-127.0.0.1}:${API_PORT:-8000}:8000"
_LOOPBACK_DEFAULT = re.compile(r'"(?:127\.0\.0\.1|\$\{API_BIND:-127\.0\.0\.1\}):')


def _api_port_lines(text: str) -> list[str]:
    """api 컨테이너의 8000 을 호스트로 내보내는 줄만 고른다."""
    return [
        line.strip()
        for line in text.splitlines()
        # 줄 끝 주석을 허용한다 — dr-staging.yml 이 그 형태다.
        if re.search(r'^\s+-\s+"[^"]*:8000"\s*(#.*)?$', line)
    ]


@pytest.mark.parametrize("name", _DEPLOY_COMPOSES)
def test_deploy_compose_binds_api_to_loopback(name):
    path = _POC / name
    if not path.exists():
        pytest.skip(f"{name} 없음")
    lines = _api_port_lines(path.read_text(encoding="utf-8"))
    assert lines, f"{name} 에서 8000 포트 매핑을 못 찾았다 — 시험이 낡았을 수 있다"
    for line in lines:
        assert _LOOPBACK_DEFAULT.search(line), (
            f"{name} 의 api 포트가 루프백에 묶여 있지 않다: {line}\n"
            "앱을 외부에 직접 세우면 평문 HTTP 로 붙는다. 노출은 프록시가 한다 "
            "(nginx-mtls --profile mtls 또는 docker-compose.console-proxy.yml)."
        )


def test_airgap_exposure_requires_an_explicit_decision():
    """폐쇄망 배포본의 노출은 기본값이 아니라 결정이어야 한다.

    API_BIND 를 변수로 두되 **기본값이 루프백**이라, 외부로 열려면 .env 에 명시해야 한다.
    """
    text = (_POC / "docker-compose.airgap.yml").read_text(encoding="utf-8")
    assert "${API_BIND:-127.0.0.1}" in text, (
        "airgap 배포본이 바인드 주소를 변수로 두지 않았다 — 열려면 YAML 을 고쳐야 하고, "
        "그렇게 고친 파일은 다음 배포에 덮여 되돌아간다."
    )
    assert '- "${API_PORT:-8000}:8000"' not in text, (
        "주소 없는 포트 매핑이 남아 있다 — docker 는 그것을 0.0.0.0 으로 연다."
    )


def test_bundle_env_template_documents_the_bind():
    """번들을 받는 운영자가 이 값을 알아야 한다 — 템플릿에 없으면 존재를 모른다."""
    import sys

    sys.path.insert(0, str(_POC / "scripts"))
    try:
        import build_offline_bundle as bundle  # noqa: PLC0415
    finally:
        sys.path.remove(str(_POC / "scripts"))

    src = Path(bundle.__file__).read_text(encoding="utf-8")
    assert "API_BIND=127.0.0.1" in src, ".env 템플릿에 API_BIND 기본값이 없다"
