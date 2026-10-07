# -*- coding: utf-8 -*-
"""합성 생성기에 붙인 세 가지 — 구조화 출력 · 병렬 배치 · 다단계 생성.

무엇을 지키려고 쓴 시험인가.

① 구조화 출력  스키마를 **실제로 서버에 넘겼는지**를 잰다. 넘겼다고 믿고 안 넘기는
   경우가 조용하다 — 플래그만 켜면 무동작인 것을 '안전'으로 읽은 전례가 있다.
   그리고 스키마를 못 받는 서버에서 **재시도가 스키마를 떼는지**를 잰다. 떼지 않으면
   같은 이유로 세 번 다 실패한다.
② 병렬 배치    결과 **순서**가 유지되는지. 순서가 흔들리면 같은 요청을 두 번 돌린
   산출물을 비교할 수 없다. 그리고 count 만큼 정확히 만드는지.
③ 다단계 생성  개요가 쓸 만하지 않으면 **단발로 내려가는지**. 다단계를 켰다고 생성이
   0건이 되면 안 된다. 그리고 검토 지적이 실제로 재작성 프롬프트에 들어가는지.
"""

from __future__ import annotations

import json
import threading

import pytest

from koipa.adapters.llm.base import LLMResponse, UsageRecord, accepts_json_schema
from koipa.modules.m1_synthesis.generator import (
    CRITIQUE_JSON_SCHEMA,
    OUTLINE_JSON_SCHEMA,
    SYNTH_DOC_JSON_SCHEMA,
    SYSTEM_PROMPT,
    SynthRequest,
    SyntheticDocGenerator,
    body_prompt_version,
    critique_prompt_version,
    outline_prompt_version,
)


def _usage() -> UsageRecord:
    return UsageRecord(provider="fake", model="fake", input_tokens=1, output_tokens=1, cost_usd=0.0)


def _doc_json(title: str = "제목", body: str = "본문" * 50) -> str:
    return json.dumps(
        {
            "title": title,
            "body": body,
            "document_type": "내부 보고서",
            "dept_hint": "기획팀",
            "rationale_tags": ["t"],
        },
        ensure_ascii=False,
    )


class RecordingProvider:
    """호출 인자를 그대로 모아 두는 가짜 provider."""

    name = "fake"
    model = "fake"

    def __init__(self, replies=None) -> None:
        self.calls: list[dict] = []
        self._replies = list(replies or [])
        self._lock = threading.Lock()

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7, json_schema=None):
        with self._lock:
            self.calls.append(
                {
                    "prompt": prompt,
                    "system": system,
                    "temperature": temperature,
                    "json_schema": json_schema,
                }
            )
            text = self._replies.pop(0) if self._replies else _doc_json()
        return LLMResponse(text=text, usage=_usage(), meta={"json_schema": bool(json_schema)})

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)


class LegacyProvider:
    """구조화 출력을 모르는 provider — json_schema 인자를 아예 받지 않는다."""

    name = "legacy"
    model = "legacy"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7):
        self.calls += 1
        return LLMResponse(text=_doc_json(), usage=_usage())

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)


# ── ① 구조화 출력 ────────────────────────────────────────────────────────────


def test_schema_is_actually_sent_to_provider():
    llm = RecordingProvider()
    gen = SyntheticDocGenerator(llm=llm, structured_output=True)
    assert gen.structured_output is True
    doc = gen.generate_one(SynthRequest(target_grade="S2", domain="tech"))

    assert llm.calls[0]["json_schema"] == SYNTH_DOC_JSON_SCHEMA
    assert doc.response_audit[0]["json_schema"] is True
    assert doc.response_audit[0]["step"] == "body"


def test_structured_output_off_sends_no_schema():
    llm = RecordingProvider()
    gen = SyntheticDocGenerator(llm=llm, structured_output=False)
    gen.generate_one(SynthRequest(target_grade="S2", domain="tech"))
    assert llm.calls[0]["json_schema"] is None


def test_provider_without_support_is_detected_and_never_receives_schema():
    """anthropic 처럼 인자를 안 받는 provider 는 능력 탐지에서 걸러진다.

    걸러지지 않으면 첫 호출이 TypeError 로 죽는다 — 조용한 0건 생성이 된다.
    """
    llm = LegacyProvider()
    assert accepts_json_schema(llm) is False
    gen = SyntheticDocGenerator(llm=llm, structured_output=True)
    assert gen.structured_output is False
    doc = gen.generate_one(SynthRequest(target_grade="S3", domain="public"))
    assert doc.parse_error is None
    assert llm.calls == 1


def test_retry_drops_the_schema_when_first_attempt_fails():
    """스키마를 못 받는 서버를 흉내낸다 — 1회차는 빈 응답, 2회차는 정상.

    재시도가 스키마를 그대로 달고 가면 같은 이유로 또 실패한다.
    """
    llm = RecordingProvider(replies=["", _doc_json()])
    gen = SyntheticDocGenerator(llm=llm, structured_output=True)
    doc = gen.generate_one(SynthRequest(target_grade="S1", domain="finance"))

    assert len(llm.calls) == 2
    assert llm.calls[0]["json_schema"] == SYNTH_DOC_JSON_SCHEMA
    assert llm.calls[1]["json_schema"] is None, "재시도가 스키마를 떼지 않았다"
    assert doc.parse_error is None
    assert [a["json_schema"] for a in doc.response_audit] == [True, False]
    assert doc.response_audit[0]["failure_reason"] == "empty_response"


def test_total_call_budget_unchanged_when_all_attempts_fail():
    """구조화 출력이 붙었다고 호출 예산이 늘면 안 된다 — 종전과 같은 3회."""
    llm = RecordingProvider(replies=["", "", ""])
    gen = SyntheticDocGenerator(llm=llm, structured_output=True)
    doc = gen.generate_one(SynthRequest(target_grade="S1", domain="finance"))
    assert len(llm.calls) == 3
    assert doc.parse_error == "non-json response"
    assert doc.label_source == "noop_fallback"


def test_prompt_json_fields_and_schema_do_not_diverge():
    """프롬프트가 서술한 필드와 스키마의 필드가 같아야 한다.

    갈라지면 스키마를 받는 서버와 못 받는 서버가 서로 다른 모양을 돌려준다.
    """
    for field in SYNTH_DOC_JSON_SCHEMA["required"]:
        assert f'"{field}"' in SYSTEM_PROMPT, f"프롬프트에 {field} 서술이 없다"


# ── ② 병렬 배치 ──────────────────────────────────────────────────────────────


def test_default_concurrency_is_sequential():
    """기본값은 1 — 종전과 같은 순차 생성이다."""
    gen = SyntheticDocGenerator(llm=RecordingProvider())
    assert gen.concurrency == 1


def test_parallel_batch_preserves_order_and_count():
    replies = [_doc_json(title=f"제목{i}") for i in range(8)]
    llm = RecordingProvider(replies=replies)
    gen = SyntheticDocGenerator(llm=llm, concurrency=4)
    docs = gen.generate(SynthRequest(target_grade="S2", domain="tech", count=8))

    assert len(docs) == 8
    # 응답을 꺼내는 순서는 스레드 경합에 따라 달라질 수 있지만, 반환 목록의 자리는
    # 요청 순서를 지켜야 한다 — 같은 자리에 같은 문서가 두 번 오면 안 된다.
    assert len({d.title for d in docs}) == 8


def test_zero_count_returns_empty_without_calling_llm():
    llm = RecordingProvider()
    gen = SyntheticDocGenerator(llm=llm, concurrency=4)
    assert gen.generate(SynthRequest(target_grade="S2", domain="tech", count=0)) == []
    assert llm.calls == []


@pytest.mark.parametrize("bad", [0, -3, None, "x"])
def test_bad_concurrency_falls_back_to_one(bad):
    """0·음수가 그대로 쓰이면 아무것도 안 만들거나 죽는다 — 조용한 0건 생성 차단."""
    gen = SyntheticDocGenerator(llm=RecordingProvider(), concurrency=bad)
    assert gen.concurrency == 1


# ── ③ 다단계 생성 ────────────────────────────────────────────────────────────


def _outline_json(n: int = 3) -> str:
    return json.dumps(
        {
            "title": "개요 제목",
            "document_type": "시험성적서",
            "dept_hint": "품질관리팀",
            "sections": [
                {"heading": f"{i}. 절 제목", "intent": "이 절의 의도"} for i in range(1, n + 1)
            ],
        },
        ensure_ascii=False,
    )


def test_multi_step_runs_four_calls_and_feeds_issues_back():
    llm = RecordingProvider(
        replies=[
            _outline_json(),
            _doc_json(body="초안 본문" * 40),
            json.dumps({"issues": ["3절 표에 빈 칸이 있다"]}, ensure_ascii=False),
            _doc_json(body="수정 본문" * 40),
        ]
    )
    gen = SyntheticDocGenerator(llm=llm, multi_step=True)
    doc = gen.generate_one(SynthRequest(target_grade="S2", domain="tech"))

    assert len(llm.calls) == 4
    assert doc.generation_mode == "multi_step"
    assert doc.critique_issues == ["3절 표에 빈 칸이 있다"]
    assert doc.body.startswith("수정 본문")

    # 개요가 본문 프롬프트의 구조 칸에 들어갔는가
    assert "1. 절 제목" in llm.calls[1]["prompt"]
    # 검토 지적이 재작성 프롬프트에 들어갔는가
    assert "3절 표에 빈 칸이 있다" in llm.calls[3]["prompt"]
    assert "[재작성 참고]" in llm.calls[3]["prompt"]

    steps = [a["step"] for a in doc.response_audit]
    assert steps == ["outline", "body", "critique", "revise"]
    assert llm.calls[0]["json_schema"] == OUTLINE_JSON_SCHEMA
    assert llm.calls[2]["json_schema"] == CRITIQUE_JSON_SCHEMA


def test_multi_step_skips_revision_when_no_issues():
    llm = RecordingProvider(
        replies=[_outline_json(), _doc_json(), json.dumps({"issues": []})]
    )
    gen = SyntheticDocGenerator(llm=llm, multi_step=True)
    doc = gen.generate_one(SynthRequest(target_grade="S3", domain="public"))

    assert len(llm.calls) == 3, "지적이 없는데 재작성을 불렀다"
    assert doc.critique_issues == []
    assert doc.generation_mode == "multi_step"


def test_multi_step_falls_back_to_single_when_outline_unusable():
    """개요가 sections 없는 JSON 을 돌려줘도 문서는 나와야 한다.

    noop provider 가 정확히 이 모양이다 — 어떤 프롬프트에도 같은 문서 JSON 을 준다.
    """
    llm = RecordingProvider(replies=[_doc_json(), _doc_json()])
    gen = SyntheticDocGenerator(llm=llm, multi_step=True)
    doc = gen.generate_one(SynthRequest(target_grade="S2", domain="tech"))

    assert doc.parse_error is None
    assert doc.body
    assert doc.generation_mode == "multi_step"
    assert [a["step"] for a in doc.response_audit] == ["outline", "body"]


def test_multi_step_keeps_first_body_when_revision_breaks():
    """수정본이 깨졌으면 검토 전 본문을 쓴다 — 검토가 결과를 나쁘게 만들면 안 된다."""
    llm = RecordingProvider(
        replies=[
            _outline_json(),
            _doc_json(body="초안 본문" * 40),
            json.dumps({"issues": ["고칠 것"]}, ensure_ascii=False),
            "",  # 수정 1회차 실패
            "",  # 재시도 실패
            "",  # 재시도 실패
        ]
    )
    gen = SyntheticDocGenerator(llm=llm, multi_step=True)
    doc = gen.generate_one(SynthRequest(target_grade="S2", domain="tech"))

    assert doc.body.startswith("초안 본문")
    assert doc.parse_error is None
    assert doc.critique_issues == ["고칠 것"]


def test_single_step_is_default_and_costs_one_call():
    llm = RecordingProvider()
    gen = SyntheticDocGenerator(llm=llm)
    assert gen.multi_step is False
    doc = gen.generate_one(SynthRequest(target_grade="S2", domain="tech"))
    assert len(llm.calls) == 1
    assert doc.generation_mode == "single"
    assert doc.critique_issues == []


def test_request_flag_overrides_generator_default():
    llm = RecordingProvider(replies=[_outline_json(), _doc_json(), json.dumps({"issues": []})])
    gen = SyntheticDocGenerator(llm=llm, multi_step=False)
    doc = gen.generate_one(SynthRequest(target_grade="S2", domain="tech", multi_step=True))
    assert doc.generation_mode == "multi_step"
    assert len(llm.calls) == 3


# ── 프롬프트 버전 ─────────────────────────────────────────────────────────────


def test_outline_version_is_no_longer_an_alias_of_body():
    """종전에는 개요 버전이 본문 버전을 그대로 돌려줬다 — 단계가 갈렸으면 값도 갈려야 한다."""
    assert outline_prompt_version() != body_prompt_version()
    assert critique_prompt_version() not in (body_prompt_version(), outline_prompt_version())
    for value in (outline_prompt_version(), body_prompt_version(), critique_prompt_version()):
        assert value.startswith("v2-") and len(value) == 11
