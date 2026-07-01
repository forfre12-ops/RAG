"""[P0#1] synthesize_batch 검수큐 적재 배선 검증 — generate→queue→review 루프 마감.

DB 없이 _persist_synth_samples 배선만 검증: session_scope·ClassifyRepo·SynthRepo를
페이크로 대체해 (1) 생성 문서가 create_sample 로 적재되고 (2) 본문 출처 마커
(label_source/parse_error)가 보존되며 (3) 미시드 등급은 스킵되고 (4) DB 오류는
예외 전파 없이 0 을 돌려주는지(재생성 방지) 본다.

이전 워커는 list[dict]만 반환하고 검수큐에 적재하지 않아, 운영학습(유일 자동화 레버)의
입력원이 단절돼 있었다 — 이 테스트가 그 배선의 회귀를 막는다.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import lloydk.db as db_mod
import lloydk.repositories as repos_mod
from lloydk.workers.tasks import _persist_synth_samples

_LEVELS = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


class _FakeClassifyRepo:
    def __init__(self, db):  # noqa: D401
        pass

    def level_id_by_code(self, code):
        return _LEVELS.get(str(code))


class _FakeSynthRepo:
    created: list[dict] = []

    def __init__(self, db):  # noqa: D401
        pass

    def create_sample(self, **kwargs):
        _FakeSynthRepo.created.append(kwargs)
        return SimpleNamespace(sample_id=uuid.uuid4())


@contextmanager
def _fake_scope():
    yield object()


@contextmanager
def _raising_scope():
    raise RuntimeError("db down")
    yield  # pragma: no cover


def _doc(grade: str, *, label_source=None, parse_error=None, domain="finance"):
    return SimpleNamespace(
        target_grade=grade,
        llm_provider="vllm",
        llm_model="Qwen/Qwen3-14B",
        body="가상 사내 문서 본문",
        domain=domain,
        label_source=label_source,
        parse_error=parse_error,
    )


def _patch(monkeypatch, *, scope=_fake_scope):
    _FakeSynthRepo.created = []
    monkeypatch.setattr(db_mod, "session_scope", scope)
    monkeypatch.setattr(repos_mod, "ClassifyRepo", _FakeClassifyRepo)
    monkeypatch.setattr(repos_mod, "SynthRepo", _FakeSynthRepo)


def test_persist_writes_to_review_queue_with_level_mapping(monkeypatch) -> None:
    _patch(monkeypatch)
    docs = [_doc("TS"), _doc("S3")]
    n = _persist_synth_samples(docs, job_id="job-1")
    assert n == 2
    assert len(_FakeSynthRepo.created) == 2
    # 등급 code → target_level_id 매핑이 적용된다.
    assert _FakeSynthRepo.created[0]["target_level_id"] == _LEVELS["TS"]
    assert _FakeSynthRepo.created[1]["target_level_id"] == _LEVELS["S3"]
    # 도메인은 doc_type 으로 보존(큐가 doc_type 을 domain 으로 표면화).
    assert _FakeSynthRepo.created[0]["doc_type"] == "finance"


def test_persist_preserves_label_source_and_parse_error(monkeypatch) -> None:
    _patch(monkeypatch)
    docs = [
        _doc("S1"),  # 정상 생성 → label_source None
        _doc("S2", label_source="noop_fallback"),  # 학습 편입 금지 마커
        _doc("TS", label_source="llm_nonjson", parse_error="non-json response"),
    ]
    n = _persist_synth_samples(docs, job_id="job-2")
    assert n == 3
    sources = [c["label_source"] for c in _FakeSynthRepo.created]
    assert sources == [None, "noop_fallback", "llm_nonjson"]
    # parse_error 도 보존(학습 위생 필터 근거).
    assert _FakeSynthRepo.created[2]["parse_error"] == "non-json response"


def test_persist_skips_unknown_grade(monkeypatch) -> None:
    _patch(monkeypatch)
    docs = [_doc("ZZ"), _doc("S3")]  # ZZ = 미시드 등급 → level_id None → 스킵
    n = _persist_synth_samples(docs, job_id="job-3")
    assert n == 1
    assert len(_FakeSynthRepo.created) == 1
    assert _FakeSynthRepo.created[0]["target_level_id"] == _LEVELS["S3"]


def test_persist_empty_docs_is_noop(monkeypatch) -> None:
    _patch(monkeypatch)
    assert _persist_synth_samples([], job_id="job-4") == 0
    assert _FakeSynthRepo.created == []


def test_persist_db_failure_does_not_raise(monkeypatch) -> None:
    # DB 미가용/오류는 재생성(비용 2배)을 유발하지 않도록 예외를 전파하지 않고 0 반환.
    _patch(monkeypatch, scope=_raising_scope)
    n = _persist_synth_samples([_doc("TS")], job_id="job-5")
    assert n == 0
