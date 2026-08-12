"""[P0#1] 합성 생성기 마커 파생 + 실 PG 검수큐 적재(round-trip) 검증.

배선(session_scope·repo 페이크) 단위 검증은 test_synth_batch_persist.py 가 담당한다.
본 파일은 그 보완층 — (1) **실 생성기**가 clean/noop_fallback/llm_nonjson 마커와 llm_model 을
SynthDoc 에 실제로 파생시키는지, (2) **실 PG** 에 적재 시 신규 컬럼(label_source/parse_error)이
마이그레이션대로 round-trip 되고 검수큐(SynthesisService.queue)에 노출되는지 본다.
(2)는 실 PG 필요 — 미가용 시 graceful skip(repo 관례). CI test-lite 는 postgres 서비스로 실행.
"""

from __future__ import annotations

import socket
import uuid

import pytest

from koipa.adapters.llm.base import LLMResponse, UsageRecord
from koipa.adapters.llm.noop_provider import NoopProvider
from koipa.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator
from koipa.workers.tasks import _persist_synth_samples


class _FakeProvider:
    """결정론 더미 — 지정한 raw text 를 그대로 반환(생성기 fallback 경로 유도용)."""

    def __init__(self, text: str, *, name: str = "fake", model: str = "fake-1") -> None:
        self._text = text
        self.name = name
        self.model = model

    def count_tokens(self, s: str) -> int:
        return max(1, len(s) // 4)

    def generate(self, prompt: str, *, system=None, max_tokens=1024, temperature=0.7) -> LLMResponse:
        return LLMResponse(
            text=self._text,
            usage=UsageRecord(
                provider=self.name, model=self.model,
                input_tokens=1, output_tokens=1, cost_usd=0.0,
            ),
        )


def _pg_up() -> bool:
    try:
        s = socket.create_connection(("localhost", 5432), timeout=0.5)
        s.close()
        return True
    except OSError:
        return False


def test_generator_clean_path_threads_model():
    """NoopProvider = 유효 JSON → clean 경로: label_source=None, parse_error=None, llm_model 채워짐."""
    gen = SyntheticDocGenerator(llm=NoopProvider())
    docs = gen.generate(SynthRequest(target_grade="S2", domain="mixed", count=2))
    assert len(docs) == 2
    for d in docs:
        assert d.label_source is None            # 정상 JSON 생성 = 마커 없음
        assert d.parse_error is None
        assert d.llm_model == "noop"             # 모델명 실려옴(빈문자 아님)


def test_generator_fallback_markers_carry():
    """빈 응답 → noop_fallback / 비-JSON 텍스트 → llm_nonjson (마커가 SynthDoc 에 실림)."""
    empty = SyntheticDocGenerator(llm=_FakeProvider("", model="m-empty"))
    d0 = empty.generate(SynthRequest(target_grade="S1", domain="mixed", count=1))[0]
    assert d0.label_source == "noop_fallback"
    assert d0.parse_error == "non-json response"
    assert d0.llm_model == "m-empty"

    nonjson = SyntheticDocGenerator(llm=_FakeProvider("이건 JSON 아님 그냥 텍스트", model="m-raw"))
    d1 = nonjson.generate(SynthRequest(target_grade="S1", domain="mixed", count=1))[0]
    assert d1.label_source == "llm_nonjson"
    assert d1.parse_error == "non-json response"
    assert d1.llm_model == "m-raw"


@pytest.mark.skipif(not _pg_up(), reason="postgres not available (5432)")
def test_persist_to_review_queue_preserves_markers():
    """워커 적재 헬퍼가 검수큐에 pending_review 로 쌓고 label_source/parse_error 를 보존한다."""
    from sqlalchemy import select

    from koipa.db import session_scope
    from koipa.db.models import SampleDocument

    token = f"p0persist-{uuid.uuid4().hex[:8]}"  # 이 실행분만 격리 조회할 고유 doc_type
    gen = SyntheticDocGenerator(llm=_FakeProvider(""))  # 빈 응답 → 전부 noop_fallback
    docs = gen.generate(SynthRequest(target_grade="S1", domain="mixed", count=3))
    for d in docs:
        d.domain = token                     # 적재 시 doc_type 으로 저장 → 격리 조회 키
    docs[0].label_source = "llm_nonjson"     # 한 건은 다른 마커로 → 보존 구분 검증

    persisted = _persist_synth_samples(docs, job_id=None)
    assert persisted == 3

    with session_scope() as db:
        rows = list(
            db.execute(
                select(SampleDocument).where(SampleDocument.doc_type == token)
            ).scalars()
        )
    assert len(rows) == 3
    assert all(r.review_status == "pending_review" for r in rows)          # 검수 대기로 적재
    assert all(r.target_level_id is not None for r in rows)                 # 등급→레벨 매핑 성공
    assert all(r.llm_model for r in rows)                                   # 모델명 보존
    sources = sorted(r.label_source for r in rows)
    assert sources == ["llm_nonjson", "noop_fallback", "noop_fallback"]     # 마커 보존
    assert all(r.parse_error == "non-json response" for r in rows)          # parse_error 보존


@pytest.mark.skipif(not _pg_up(), reason="postgres not available (5432)")
def test_persisted_samples_visible_in_synth_queue():
    """적재분이 SynthesisService.queue(검수큐 조회)에 실제로 노출되는지 — 루프 연결 E2E."""
    from koipa.services.synthesis_service import SynthesisService

    gen = SyntheticDocGenerator(llm=_FakeProvider(""))
    docs = gen.generate(SynthRequest(target_grade="TS", domain="mixed", count=2))
    before = SynthesisService().queue(status="pending", limit=1).total
    assert _persist_synth_samples(docs, job_id=None) == 2
    after = SynthesisService().queue(status="pending", limit=1).total
    assert after >= before + 2   # 검수 대기 큐 total 이 적재분만큼 증가
