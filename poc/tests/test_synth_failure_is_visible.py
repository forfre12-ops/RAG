# -*- coding: utf-8 -*-
"""LLM 응답 실패가 화면·API 에 보이는가.

무엇을 지키려고 쓴 시험인가.

2026-09-10 211 실측: 합성 생성이 LLM 없이도 '성공'했다. 잡은 done · 검수 큐에 1건 ·
겉으로는 전부 정상인데 **본문이 자리표시**였다. 사람이 그것을 검수하게 된다.
실패 사실은 로그에만 있었다.

세 자리를 잰다.
  ① 잡 상태     전부 대체본이면 failed · 섞였으면 partial · 정상이면 done
  ② 잡 집계     몇 건 중 몇 건이 대체본인지 API 로 나오는가
  ③ 검수 큐     행마다 본문 출처(label_source)가 나오는가
그리고 화면이 그 값을 실제로 읽는지(콘솔 HTML)를 함께 잠근다 — 서버가 내보내도
화면이 안 읽으면 사람은 여전히 모른다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from koipa.schemas.synthesis import SynthJobStatus, SyntheticDocItem
from koipa.workers import tasks as worker_tasks

CONSOLE = (
    Path(__file__).resolve().parents[1] / "src" / "koipa" / "api" / "static" / "admin.html"
)


class _Doc:
    def __init__(self, label_source=None):
        self.label_source = label_source
        self.target_grade = "S2"
        self.domain = "tech"
        self.body = "본문"
        self.title = "제목"
        self.llm_provider = "ollama"
        self.llm_model = "qwen3:14b"
        self.usage = None
        self.parse_error = None
        self.generation_mode = "single"


@pytest.fixture()
def captured(monkeypatch):
    """_record_job_done 이 받은 인자를 그대로 잡아 둔다."""
    seen: dict = {}

    def _fake(job_id, *, results, completed=None, extra=None, status="done"):
        seen.update(
            {"job_id": job_id, "results": results, "completed": completed,
             "extra": extra or {}, "status": status}
        )

    monkeypatch.setattr(worker_tasks, "_record_job_done", _fake)
    monkeypatch.setattr(
        worker_tasks, "_persist_synth_samples", lambda docs, **kw: len(docs)
    )
    return seen


def _run(monkeypatch, docs):
    """synthesize_batch 본문을 태운다 — 생성기와 게이트만 갈아 끼운다."""
    class _Gen:
        def __init__(self, llm=None):
            self.structured_output = True
            self.concurrency = 1
            self.multi_step = False

        def generate(self, req):
            return docs

    import koipa.modules.m1_synthesis.generator as gen_mod

    monkeypatch.setattr(gen_mod, "SyntheticDocGenerator", _Gen)

    import koipa.services.synth_quality as sq

    monkeypatch.setattr(
        sq, "screen_batch",
        lambda pairs: {
            "admit": list(range(len(pairs))), "flagged": [],
            "batch_verdict": "ok", "metrics": {},
        },
    )
    return worker_tasks.synthesize_batch.run(
        grade="S2", count=len(docs), domain="tech", job_id="00000000-0000-0000-0000-000000000001"
    )


# ── ① 잡 상태 ────────────────────────────────────────────────────────────────


def test_all_fallback_marks_job_failed(monkeypatch, captured):
    _run(monkeypatch, [_Doc("noop_fallback"), _Doc("llm_nonjson")])
    assert captured["status"] == "failed", "LLM 이 다 실패했는데 잡이 성공으로 끝났다"


def test_some_fallback_marks_job_partial(monkeypatch, captured):
    _run(monkeypatch, [_Doc(), _Doc("llm_nonjson"), _Doc()])
    assert captured["status"] == "partial"


def test_clean_batch_still_marks_done(monkeypatch, captured):
    """정상 배치는 종전과 같아야 한다 — 없는 실패를 만들지 않는다."""
    _run(monkeypatch, [_Doc(), _Doc()])
    assert captured["status"] == "done"
    assert captured["extra"]["llm_fallback"]["fallback"] == 0


# ── ② 잡 집계 ────────────────────────────────────────────────────────────────


def test_fallback_counts_are_recorded_by_source(monkeypatch, captured):
    _run(monkeypatch, [_Doc("noop_fallback"), _Doc("llm_nonjson"), _Doc("llm_nonjson"), _Doc()])
    fb = captured["extra"]["llm_fallback"]
    assert fb["total"] == 4
    assert fb["fallback"] == 3
    assert fb["by_source"] == {"noop_fallback": 1, "llm_nonjson": 2}


def test_documents_are_not_discarded_on_fallback(monkeypatch, captured):
    """상태만 사실대로 적는다 — 만든 문서를 지우면 무엇이 잘못됐는지 볼 수 없다."""
    _run(monkeypatch, [_Doc("noop_fallback"), _Doc("noop_fallback")])
    assert captured["completed"] == 2
    assert len(captured["results"]) == 2


def test_job_status_schema_carries_fallback():
    st = SynthJobStatus(
        synth_job_id="00000000-0000-0000-0000-000000000001",
        status="partial",
        llm_fallback={"total": 3, "fallback": 1, "by_source": {"llm_nonjson": 1}},
    )
    assert st.llm_fallback["fallback"] == 1


# ── ③ 검수 큐 ────────────────────────────────────────────────────────────────


def test_queue_item_exposes_label_source():
    item = SyntheticDocItem(
        synth_id="00000000-0000-0000-0000-000000000002",
        target_grade="S2",
        llm_provider="ollama",
        llm_model="qwen3:14b",
        review_status="pending_review",
        label_source="llm_nonjson",
    )
    assert item.label_source == "llm_nonjson"


def test_queue_item_label_source_defaults_to_none():
    """정상 생성은 표식이 없다 — 없는 경고를 띄우면 안 된다."""
    item = SyntheticDocItem(
        synth_id="00000000-0000-0000-0000-000000000003",
        target_grade="S3",
        llm_provider="ollama",
        llm_model="qwen3:14b",
        review_status="pending_review",
    )
    assert item.label_source is None


# ── 화면이 그 값을 읽는가 ─────────────────────────────────────────────────────


def test_console_polls_the_job_after_generate():
    """생성 요청이 접수 통보로 끝나면 사람은 결과를 모른다."""
    html = CONSOLE.read_text(encoding="utf-8")
    assert "pollSynthJob" in html
    assert "/synth/jobs/" in html


def test_console_reads_fallback_count_not_provider_name():
    html = CONSOLE.read_text(encoding="utf-8")
    assert "llm_fallback" in html, "화면이 대체본 집계를 읽지 않는다"
    assert "대체본" in html


def test_console_marks_fallback_rows_in_review_queue():
    html = CONSOLE.read_text(encoding="utf-8")
    assert "it.label_source" in html, "검수 큐가 본문 출처를 표시하지 않는다"


def test_console_banner_no_longer_claims_zero_documents():
    """noop 서버에서도 문서는 만들어져 큐에 쌓인다 — '결과 0건' 은 사실이 아니었다."""
    html = CONSOLE.read_text(encoding="utf-8")
    assert "합성 생성 결과는 0건으로 나옵니다" not in html
    assert "학습에 쓰이는 행은 0건" in html


def test_console_has_no_inline_handler_added_by_this_change():
    """인라인 핸들러는 정적 검사기가 막는다 — 새로 넣지 않았는지 확인."""
    html = CONSOLE.read_text(encoding="utf-8")
    block = html[html.index("async function pollSynthJob"):]
    block = block[: block.index("\n}\n")]
    assert not re.search(r"\son\w+\s*=", block)
