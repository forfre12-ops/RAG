"""색인과 질의가 **같은 임베더**를 쓰는지 잠근다.

왜(실측 2026-08-17, KL 223). 배포본에서 서비스마다 다른 임베딩 모델이 들어가 있었다.

    api     BAAI/bge-m3          질의를 임베딩한다
    worker  nlpai-lab/KURE-v1    문서를 색인한다

두 모델 다 **1024 차원**이라 차원 검사에 안 걸린다. 예외도 안 난다. 그냥 엉뚱한
이웃이 나온다 - **조용한 오검색**이다. 검색 품질 수치가 왜 안 맞는지 추적할 단서가
응답 어디에도 없다.

원인은 compose 기본값이 파일마다 달랐던 것이다.

    docker-compose.prod.yml       ${EMBEDDING_MODEL:-BAAI/bge-m3}        <- api 만
    docker-compose.airgap.yml     ${EMBEDDING_MODEL:-nlpai-lab/KURE-v1}
    docker-compose.dr-staging.yml ${EMBEDDING_MODEL:-nlpai-lab/KURE-v1}

⚠ `.env` 에 값을 적어도 안 고쳐진다. prod 의 `environment: !override` 가 env_file 을
  덮고, compose 치환 `${...}` 은 **셸과 프로젝트 .env 만** 본다. env_file(.env.jjw)은
  컨테이너로만 들어가고 치환에는 안 쓰인다 - 실제로 .env.jjw 에 KURE-v1 이 적혀
  있었는데도 api 는 bge-m3 로 떴다. 그래서 "설정 파일에 적었으니 됐다" 가 근거가 못 된다.

이 테스트는 **기본값이 파일마다 갈리는 것 자체**를 막는다. 값을 바꿀 때는 전부 같이 바꾼다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PAT = re.compile(r"EMBEDDING_MODEL:\s*[\"']?\$\{EMBEDDING_MODEL:-([^}\"'\s]+)")


def _compose_defaults() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in sorted(_ROOT.glob("docker-compose*.yml")):
        vals = _PAT.findall(f.read_text(encoding="utf-8"))
        if vals:
            out[f.name] = vals
    return out


def test_every_compose_declares_the_same_embedding_default():
    """compose 파일마다 기본값이 다르면 서비스마다 다른 모델이 뜬다."""
    defaults = _compose_defaults()
    assert defaults, "compose 에서 EMBEDDING_MODEL 기본값을 하나도 못 찾았다 - 패턴이 바뀌었나?"
    flat = {v for vals in defaults.values() for v in vals}
    assert len(flat) == 1, (
        f"compose 기본값이 갈렸다: { {k: v for k, v in defaults.items()} } — "
        "색인(worker)과 질의(api)가 다른 모델을 쓰면 1024 차원끼리라 오류 없이 오검색이 난다"
    )


def test_compose_default_matches_code_default():
    """코드 기본값과도 같아야 한다 - compose 없이 뜨는 경로(테스트·스크립트)가 있다."""
    from koipa.config import Settings

    code_default = Settings.model_fields["embedding_model"].default
    flat = {v for vals in _compose_defaults().values() for v in vals}
    assert flat == {code_default}, (
        f"compose 기본값 {flat} != config.py 기본값 {code_default!r}"
    )


@pytest.mark.parametrize("name", ["embedding_model", "rag_operational_embedding_model"])
def test_embedding_settings_point_at_the_same_model(name):
    """설정 키가 둘인데 서로 다르면 어느 쪽이 실제인지 코드마다 갈린다."""
    from koipa.config import Settings

    assert Settings.model_fields[name].default == \
        Settings.model_fields["embedding_model"].default
