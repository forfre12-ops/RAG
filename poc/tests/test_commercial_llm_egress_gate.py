# -*- coding: utf-8 -*-
"""상용 LLM(공개 클라우드) 반출 게이트 — 판단 근거가 출처인지 검증.

2026-08-24 이전 상태: 반출을 막는 것은 요청 본문의 ``sensitive`` 플래그 하나뿐이었고
기본값이 False, 콘솔은 그 값을 아예 보내지 않았다(admin.html grep 0건). 즉 상용 키를
꽂는 순간 출처를 모르는 문서가 Anthropic/Gemini/OpenAI 로 나갈 수 있었다.

여기서 잠그는 것은 세 가지다.
  1) 반출 가능 여부를 **출처 허용목록**으로 판정한다(모르면 차단).
  2) 한 건이라도 반출 불가가 섞이면 실행분 전체가 airgap 이 된다.
  3) provider 미지정 기본값이 상용이 아니다.
"""
from __future__ import annotations

import pytest

from koipa.golden_tiers import may_send_to_commercial_llm
from koipa.schemas.golden import GoldenBuildRequest
from koipa.services.golden_build_service import GoldenBuildService

_ACTOR = {"user_id": "tester", "role": "admin"}


def _req(**kw) -> GoldenBuildRequest:
    return GoldenBuildRequest(actor=_ACTOR, **kw)


# ── 1. 허용목록 자체 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "record",
    [
        {"document_origin": "public_real"},   # 이미 공개된 실문서
        {"document_origin": "synthetic"},     # 우리가 지어낸 본문 — 실제 비밀 아님
        {"source": "판례_공개문서"},           # source 에서 public_real 로 유도
    ],
)
def test_cloud_eligible_origins_may_be_sent(record):
    assert may_send_to_commercial_llm(record) is True


@pytest.mark.parametrize(
    "record",
    [
        {"document_origin": "customer_real"},        # 고객사 실문서
        {"document_origin": "organization_real"},    # 조직 보유 실문서(콘솔 어휘)
        {"document_origin": "unknown"},              # 출처 미상
        {},                                          # 아무 표식 없음
        {"document_origin": ""},                     # 빈 값
        {"source": "내부문서_출처불명"},               # 유도 실패 → unknown
    ],
)
def test_unknown_or_internal_origins_are_blocked(record):
    """모르는 것은 막는다 — '괜찮을 것'은 근거가 아니다."""
    assert may_send_to_commercial_llm(record) is False


def test_blocklist_would_have_missed_the_second_vocabulary():
    """출처 어휘가 두 벌이라는 사실을 시험으로 고정한다.

    golden_tiers 는 ``customer_real``, proxy_gold_candidate_service 는
    ``organization_real`` 을 쓴다. 차단목록으로 짰다면 한쪽이 그물을 빠져나간다.
    허용목록이라 둘 다 막힌다.
    """
    assert may_send_to_commercial_llm({"document_origin": "customer_real"}) is False
    assert may_send_to_commercial_llm({"document_origin": "organization_real"}) is False


# ── 2. 실행분 라우팅 ─────────────────────────────────────────────────────────
def test_one_ineligible_document_airgaps_the_whole_run():
    """섞이면 전체를 잠근다 — '일부만 보낸다'는 안전한 쪽이 아니다."""
    docs = [
        {"document_origin": "public_real", "text": "공개"},
        {"document_origin": "public_real", "text": "공개"},
        {"document_origin": "organization_real", "text": "내부"},
    ]
    airgap, audit = GoldenBuildService._egress_decision(docs, _req(sensitive=False))
    assert airgap is True
    assert audit["reason"] == "origin_not_cloud_eligible"
    assert audit["blocked_by_origin"] == {"unknown": 1}  # organization_real → unknown(fail-closed)


def test_all_eligible_and_not_declared_sensitive_is_allowed():
    docs = [{"document_origin": "public_real"}, {"document_origin": "synthetic"}]
    airgap, audit = GoldenBuildService._egress_decision(docs, _req(sensitive=False))
    assert airgap is False
    assert audit["reason"] == "allowed"
    assert audit["blocked_by_origin"] == {}


def test_caller_flag_can_only_tighten():
    """자칭 sensitive 는 더 잠글 수만 있고 풀 수는 없다."""
    eligible = [{"document_origin": "public_real"}]
    airgap, audit = GoldenBuildService._egress_decision(eligible, _req(sensitive=True))
    assert airgap is True
    assert audit["reason"] == "caller_declared_sensitive"

    # 반대 방향: sensitive=False 로 꺼도 출처가 허용하지 않으면 여전히 막힌다.
    ineligible = [{"document_origin": "customer_real"}]
    airgap, _ = GoldenBuildService._egress_decision(ineligible, _req(sensitive=False))
    assert airgap is True


def test_empty_document_set_does_not_open_the_gate():
    airgap, audit = GoldenBuildService._egress_decision([], _req(sensitive=False))
    assert airgap is False
    assert audit["blocked_by_origin"] == {}


# ── 3. 기본값 ────────────────────────────────────────────────────────────────
def test_default_judge_provider_is_not_commercial():
    """설정을 비워 둔 것이 '상용으로 보내라'는 뜻이 되면 안 된다."""
    from koipa.config import Settings
    from koipa.modules.m3_labeling.judge import COMMERCIAL

    assert Settings().judge_primary_provider not in COMMERCIAL
