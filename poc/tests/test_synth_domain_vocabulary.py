"""합성 API 가 받는 도메인 == 생성기가 프롬프트를 가진 도메인.

실제로 있었던 어긋남(2026-09-05 발견): 생성기는 13개 도메인의 프롬프트를 갖고 있는데
API 정규식은 6개(tech·business·hr·finance·legal·mixed)만 받고 있었다. 그 6개는 학습셋에서
등급별 60건 이상으로 이미 채워진 칸이고, 정작 얇은 칸(TS ai 1건·TS semiconductor 1건·
TS defense 2건)의 도메인은 생성기가 프롬프트를 갖고 있는데도 API 가 거부했다 —
**생성 능력이 필요 없는 칸에만 열려 있었다.**

한쪽만 고치면 다시 벌어지므로 여기서 잠근다.
"""

from __future__ import annotations

import pytest

from koipa.modules.m1_synthesis.generator import DOMAIN_DOC_TYPES
from koipa.schemas.common import Actor, Grade
from koipa.schemas.synthesis import SynthGenerateRequest


def _req(domain: str) -> SynthGenerateRequest:
    return SynthGenerateRequest(
        target_grade=Grade.TS,
        domain=domain,
        count=1,
        actor=Actor(user_id="u", role="admin"),
    )


@pytest.mark.parametrize("domain", sorted(DOMAIN_DOC_TYPES))
def test_every_generator_domain_is_accepted_by_api(domain):
    """생성기가 프롬프트를 가진 도메인은 전부 API 로 부를 수 있어야 한다."""
    assert _req(domain).domain == domain


def test_unknown_domain_is_rejected():
    """어휘 밖 값은 막는다 — 프롬프트가 없으면 mixed 로 조용히 대체돼 의도와 달라진다."""
    with pytest.raises(Exception):
        _req("존재하지-않는-도메인")


def test_thin_cell_domains_are_reachable():
    """빈 칸 분석(scripts/synth_coverage_gaps.py)이 지목한 도메인에 실제로 생성 요청이 되나.

    이 셋은 2026-09-05 실측에서 TS 가 1~2건뿐이던 칸이다. 여기에 요청이 안 되면
    합성으로 빈 칸을 채운다는 전략 자체가 성립하지 않는다.
    """
    for domain in ("ai", "semiconductor", "defense"):
        assert _req(domain).domain == domain
