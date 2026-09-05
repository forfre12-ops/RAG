# -*- coding: utf-8 -*-
"""합성 경로의 계약 결함 다섯 가지를 지키는 시험 (2026-09-05 지적 대응).

무엇을 지키나 — 전부 "고쳤다"가 아니라 **다시 나면 잡히는가**를 본다.

  ⑧ noop 산출물이 학습셋에 들어가지 않는다
     실측으로 확인한 구멍이었다: NoopProvider 는 파싱되는 JSON 을 주므로 label_source 가
     None 으로 남아, 마커 기반 게이트를 그대로 통과했다. provider 축으로 막는다.
  ④ API 가 받는 provider 목록이 팩토리와 같다
     종전 API 는 vllm_qwen·vllm_exaone 을 받았는데 build_provider 는 그 이름을 모른다.
  ③ seed_documents 는 없다 — 요건(RTM FUN-003)에 없고 구현도 없었다. 되살리면 발주처
     원문을 합성 프롬프트로 보내는 경로가 생긴다.
  ⑤ 발사 여부가 응답에 담긴다 — 종전에는 등록만 되고 생성이 안 돼도 202 였다.
  ② 요청한 provider 가 태스크까지 전달된다.
"""
from __future__ import annotations

import inspect
import re

import pytest

from koipa.adapters.llm import build_provider
from koipa.config import _VALID_LLM_PROVIDER
from koipa.schemas.synthesis import (
    LLM_PROVIDER_PATTERN,
    SynthGenerateRequest,
    SynthGenerateResponse,
)
from koipa.services.synthesis_service import _is_training_admissible


# ── ⑧ noop 더미의 학습 편입 차단 ─────────────────────────────────────
@pytest.mark.parametrize(
    ("label_source", "provider", "expected"),
    [
        (None, "noop", False),        # ← 구멍이었던 자리: 파싱 성공한 noop 산출물
        (None, "NOOP", False),        # 대소문자 무관
        (None, " noop ", False),      # 공백 무관
        (None, "anthropic", True),
        ("noop_fallback", "anthropic", False),
        ("llm_nonjson", "anthropic", False),
        (None, None, True),           # provider 미상은 종전 동작 유지
    ],
)
def test_training_admission_gate(label_source, provider, expected):
    assert _is_training_admissible(label_source, provider) is expected


def test_noop_generated_doc_is_refused(monkeypatch):
    """생성기를 실제로 돌려 확인한다 — 패턴이 아니라 동작을 본다."""
    monkeypatch.setenv("LLM_PROVIDER", "noop")
    from koipa.modules.m1_synthesis.generator import (  # noqa: PLC0415
        SynthRequest, SyntheticDocGenerator,
    )

    docs = SyntheticDocGenerator().generate(
        SynthRequest(target_grade="TS", domain="tech", count=1)
    )
    d = docs[0]
    assert d.llm_provider == "noop"
    assert not _is_training_admissible(d.label_source, d.llm_provider), (
        "noop 산출물이 학습 편입 가능으로 나왔다 — 게이트가 뚫렸다"
    )


# ── ④ provider 목록이 팩토리와 같다 ──────────────────────────────────
def test_api_provider_names_are_all_buildable():
    names = re.fullmatch(r"\^\((.+)\)\$", LLM_PROVIDER_PATTERN).group(1).split("|")
    assert set(names) == set(_VALID_LLM_PROVIDER), (
        "API 가 받는 이름과 설정 정본이 갈렸다"
    )
    # 팩토리가 모르는 이름이 하나도 없어야 한다(네트워크 없이 생성만 확인).
    for n in names:
        if n == "noop":
            assert build_provider(n) is not None
            continue
        try:
            build_provider(n)
        except ValueError as exc:  # 이름을 모른다 = 계약 불일치
            pytest.fail(f"팩토리가 모르는 이름이 API 에 열려 있다: {n} ({exc})")
        except Exception:  # noqa: BLE001  # 자격증명·endpoint 부재는 여기 관심 밖
            pass


# ── ③ seed_documents 는 없다 ─────────────────────────────────────────
def test_seed_documents_is_gone():
    assert "seed_documents" not in SynthGenerateRequest.model_fields, (
        "발주처 원문을 합성 프롬프트로 보내는 경로다 — 요건(FUN-003)에도 없다"
    )


# ── ⑤ 발사 여부가 응답에 있다 ────────────────────────────────────────
def test_response_reports_dispatch():
    for f in ("dispatched", "dispatch_note"):
        assert f in SynthGenerateResponse.model_fields, (
            "등록만 되고 생성이 안 된 것을 호출자가 알 수 있어야 한다"
        )


# ── ② provider 가 태스크까지 간다 ────────────────────────────────────
def test_task_accepts_llm_provider():
    from koipa.workers.tasks import synthesize_batch  # noqa: PLC0415

    fn = getattr(synthesize_batch, "run", synthesize_batch)
    assert "llm_provider" in inspect.signature(fn).parameters, (
        "요청자가 고른 모델과 실제로 쓴 모델이 갈린다"
    )
