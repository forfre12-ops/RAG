# -*- coding: utf-8 -*-
"""콘솔 경로(synthesize_batch)가 게이트 결과를 실제로 쓰는가 — 2026-09-29.

두 가지를 잠근다.
  ① 개인정보 정규식에 걸린 문서는 검수 큐 적재 대상(admit)에서 빠진다. 스크립트 경로는 이미
     그렇게 버렸는데 콘솔 경로만 걸린 문서를 그대로 올렸다.
  ② 품질 하한에 걸려 **표시만 된** 문서가 잡 결과(leakage_gate.low_quality)에 남는다.
     종전에는 건수만 남고 어느 문서가 무엇에 걸렸는지 버려졌다. 떨어뜨리지는 않는다.

screen_batch 는 가짜로 바꾸지 않는다 — 진짜 게이트가 콘솔 경로에서 어떻게 쓰이는지가 시험 대상이다.
"""

from __future__ import annotations

import pytest

from koipa.workers import tasks as worker_tasks

_GOOD = "\n\n".join(
    f"{i}. 점검 항목\n{i}차 점검에서 확인한 수치는 {i * 7}건이며 기준치 {i * 11} 대비"
    f" 차이는 {i * 4} 이다. 담당 역할과 기한을 함께 적는다."
    for i in range(1, 7)
)


class _Doc:
    def __init__(self, body: str, grade: str = "S2"):
        self.label_source = None
        self.target_grade = grade
        self.domain = "tech"
        self.body = body
        self.title = "제목"
        self.llm_provider = "ollama"
        self.llm_model = "qwen3:14b"
        self.usage = None
        self.parse_error = None
        self.generation_mode = "single"
        self.playbook_version = ""


@pytest.fixture()
def captured(monkeypatch):
    seen: dict = {"persisted_bodies": []}

    def _fake_done(job_id, *, results, completed=None, extra=None, status="done"):
        seen.update({"extra": extra or {}, "status": status})

    def _fake_persist(docs, **kw):
        seen["persisted_bodies"] = [d.body for d in docs]
        return len(docs)

    monkeypatch.setattr(worker_tasks, "_record_job_done", _fake_done)
    monkeypatch.setattr(worker_tasks, "_persist_synth_samples", _fake_persist)
    return seen


def _run(monkeypatch, docs):
    class _Gen:
        def __init__(self, llm=None):
            self.structured_output = True
            self.concurrency = 1
            self.multi_step = False

        def generate(self, req):
            return docs

    import koipa.modules.m1_synthesis.generator as gen_mod

    monkeypatch.setattr(gen_mod, "SyntheticDocGenerator", _Gen)
    return worker_tasks.synthesize_batch.run(
        grade="S2", count=len(docs), domain="tech", job_id="00000000-0000-0000-0000-000000000002"
    )


def test_pii_document_is_not_persisted_to_review_queue(monkeypatch, captured):
    clean = _Doc(_GOOD)
    leaky = _Doc(_GOOD + "\n문의는 hong.gd@corp-x.example 로 한다.")
    _run(monkeypatch, [leaky, clean])

    assert captured["persisted_bodies"] == [clean.body], "개인정보가 걸린 문서가 검수 큐로 갔다"
    gate = captured["extra"]["leakage_gate"]
    assert gate["generated"] == 2 and gate["admitted"] == 1
    assert [f["reason"] for f in gate["flagged"]] == ["pii_detected"]


def test_low_quality_document_is_recorded_but_still_admitted(monkeypatch, captured):
    thin = _Doc("한 문단짜리 짧은 메모다. " * 12)            # 문단 1개 · 수치 0개
    good = _Doc(_GOOD)
    _run(monkeypatch, [thin, good])

    assert captured["persisted_bodies"] == [thin.body, good.body], "얇다고 검수자에게서 감추면 안 된다"
    gate = captured["extra"]["leakage_gate"]
    assert gate["metrics"]["low_quality_documents"] == 1
    assert [q["index"] for q in gate["low_quality"]] == [0]
    assert gate["low_quality"][0]["reason"].startswith("low_quality:quality:")
    assert gate["flagged"] == [], "품질은 '걸림'이 아니라 '표시'다"


def test_clean_batch_records_empty_low_quality(monkeypatch, captured):
    _run(monkeypatch, [_Doc(_GOOD), _Doc(_GOOD + "\n추가 확인 1건.")])

    gate = captured["extra"]["leakage_gate"]
    assert gate["low_quality"] == []
    assert gate["admitted"] == 2
