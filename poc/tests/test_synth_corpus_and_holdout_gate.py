"""합쳐진 학습 코퍼스를 재는가 · 홀드아웃과 독립인지 보는가.

왜 이 시험이 있는가(2026-09-05). 누출 게이트가 두 자리에 있는 줄 알았는데 실제로는
**한 자리**만 있었다.

  · 생성 시점  synth_quality.screen_batch — 요청 한 건(docs 1~500)을 잰다.
               24건 미만이면 코퍼스 지표를 **아예 계산하지 않는다**
               (MIN_DOCS_FOR_CORPUS_METRICS · 작은 배치의 거짓 양성 방지).
  · 학습셋 방출 build_training_rows — 여러 배치의 승인분을 **합친다.** 이 합집합은
               지금까지 아무도 재지 않았다.

그래서 20건씩 나눠 요청하면 게이트는 매번 'too_small_for_corpus_metrics' 로 통과시키고,
합쳐진 학습셋은 검사받은 적이 없는 채로 나간다. 실측(2026-09-05): 리포의 학습셋 **23개**가
20건 배치로는 전부 '표본 부족'인데 합치면 'corpus_leak' 이다.

그리고 세 번째 축이 아예 없었다 — **학습셋과 평가셋이 같은 문서·문장을 공유하는가.**
어느 셋 하나만 봐서는 원리상 잡히지 않는다. 짝을 줘야 잴 수 있다.
"""

from __future__ import annotations

import io
import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import koipa.services.synthesis_service as ss
from koipa.services.synth_quality import MIN_DOCS_FOR_CORPUS_METRICS, screen_batch
from koipa.services.synthesis_service import SynthesisService

_LEVELS = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


class _FakeClassifyRepo:
    def __init__(self, db):  # noqa: D401
        pass

    def level_id_by_code(self, code):
        return _LEVELS.get(str(code))


class _FakeSynthRepo:
    samples: list = []

    def __init__(self, db):  # noqa: D401
        pass

    def count_by_status(self, status):
        return sum(1 for s in _FakeSynthRepo.samples if s.review_status == status)

    def list_by_status(self, status, *, limit=50, offset=0):
        rows = [s for s in _FakeSynthRepo.samples if s.review_status == status]
        return rows[offset:offset + limit]

    def record_dataset_membership(self, sample_ids, version):
        return len(sample_ids)


@contextmanager
def _fake_scope():
    yield object()


def _sample(level_code, content):
    return SimpleNamespace(
        sample_id=uuid.uuid4(),
        target_level_id=_LEVELS[level_code],
        corrected_level_id=None,
        generated_content=content,
        doc_type="finance",
        label_source=None,
        review_status="approved",
    )


def _patch(monkeypatch, samples):
    _FakeSynthRepo.samples = samples
    monkeypatch.setattr(ss, "session_scope", _fake_scope)
    monkeypatch.setattr(ss, "SynthRepo", _FakeSynthRepo)
    monkeypatch.setattr(ss, "ClassifyRepo", _FakeClassifyRepo)


def _leaky_corpus(n_per_grade: int = 12):
    """등급이 길이로 갈리는 코퍼스 — 길이 누출이 확실히 잡히도록 만든다."""
    out = []
    for grade, size in (("TS", 4000), ("S1", 2000), ("S2", 800), ("S3", 200)):
        for i in range(n_per_grade):
            body = ("사업 검토 내용 %d. " % i) * (size // 14)
            out.append((grade, body[:size]))
    return out


# ── ① 배치로는 통과, 합치면 샌다 ────────────────────────────────────────────
def test_small_batches_never_measure_the_corpus():
    """게이트가 재지 않는 구간이 실재한다 — 이 시험이 그 사실을 고정한다."""
    docs = _leaky_corpus()
    batch = 20
    assert batch < MIN_DOCS_FOR_CORPUS_METRICS, "전제: 정상 요청 크기가 문턱 미만일 수 있다"

    verdicts = {screen_batch(docs[i:i + batch])["batch_verdict"]
                for i in range(0, len(docs), batch)}
    assert verdicts == {"too_small_for_corpus_metrics"}, verdicts

    whole = screen_batch(docs)
    assert whole["batch_verdict"] == "corpus_leak", whole["metrics"]


# ── ② 그 합집합을 build_training_rows 가 잰다 ────────────────────────────────
def test_build_training_rows_reports_corpus_leakage(monkeypatch):
    _patch(monkeypatch, [_sample(g, t) for g, t in _leaky_corpus()])
    res = SynthesisService().build_training_rows()

    assert res["included"] == 48
    leak = res["corpus_leakage"]
    assert leak["documents"] == 48
    # 길이로 등급이 갈리는 코퍼스라 길이 누출이 높게 나온다.
    assert leak["length_only_1nn"] > 0.55, leak
    # 지표 키가 모두 나온다 — 빌더가 조건 없이 읽는다.
    for key in ("length_theils_u", "tell_coverage", "grade_token_exposed"):
        assert key in leak


def test_clean_corpus_reports_low_leakage(monkeypatch):
    """길이가 섞인 코퍼스는 낮게 나온다 — 지표가 늘 높은 것이 아님을 고정."""
    docs = []
    for i in range(48):
        grade = ("TS", "S1", "S2", "S3")[i % 4]
        docs.append((grade, ("업무 검토 기록 %d 항목입니다. " % i) * (30 + (i * 7) % 40)))
    _patch(monkeypatch, [_sample(g, t) for g, t in docs])
    res = SynthesisService().build_training_rows()
    assert res["corpus_leakage"]["length_only_1nn"] <= 0.55, res["corpus_leakage"]


# ── ③ DB 오류 경로에도 키가 있다 ────────────────────────────────────────────
def test_db_error_path_carries_corpus_leakage_key(monkeypatch):
    """오류 경로에서 키가 빠지면 호출부가 KeyError 로 끝난다(어제 같은 실수를 반복하지 않는다)."""
    from sqlalchemy.exc import SQLAlchemyError

    @contextmanager
    def _boom():
        raise SQLAlchemyError("DB 없음")
        yield  # pragma: no cover

    monkeypatch.setattr(ss, "session_scope", _boom)
    res = SynthesisService().build_training_rows()
    for key in ("rows", "dataset_version", "corpus_leakage", "excluded_gate_error",
                "grade_corrected", "approved_total", "included",
                "excluded_noise", "excluded_empty"):
        assert key in res, key
    assert res["corpus_leakage"]["documents"] == 0


def test_corpus_leakage_failure_is_visible_not_silent(monkeypatch):
    """계량이 깨져도 방출은 계속하되, '재지 못했다'가 결과에 남는다(fail-open 가시화)."""
    _patch(monkeypatch, [_sample("TS", "본문")])

    def _boom(_docs):
        raise RuntimeError("계량 붕괴")

    monkeypatch.setattr("koipa.dataset_leakage.audit", _boom)
    res = SynthesisService().build_training_rows()
    assert res["included"] == 1                      # 방출은 막지 않는다
    assert "error" in res["corpus_leakage"]          # 통과로 읽히지 않는다


# ── ④ 빌더 스크립트 — 홀드아웃 독립 + --strict ──────────────────────────────
def _run_builder(monkeypatch, tmp_path, samples, extra_argv):
    from scripts.build_synth_training_set import main

    _patch(monkeypatch, samples)
    out = tmp_path / "train.jsonl"
    rep = tmp_path / "report.json"
    code = main(["--out", str(out), "--report", str(rep)] + extra_argv)
    return code, json.loads(io.open(rep, encoding="utf-8").read())


def test_builder_reports_gate_error_count(monkeypatch, tmp_path):
    """어제 만들어 놓고 인쇄하지 않던 카운트가 요약에 나온다."""
    _code, summary = _run_builder(monkeypatch, tmp_path, [_sample("TS", "본문 하나")], [])
    assert "excluded_gate_error" in summary


def test_builder_says_when_holdout_was_not_checked(monkeypatch, tmp_path):
    """미검사를 '통과'로 읽히게 두지 않는다 — 키를 빼지 않고 사실을 적는다."""
    _code, summary = _run_builder(monkeypatch, tmp_path, [_sample("TS", "본문 하나")], [])
    assert summary["holdout_independence"] == "미검사 — --holdout 미지정"


def test_builder_flags_holdout_sharing_sentences(monkeypatch, tmp_path):
    """학습셋 문장을 그대로 품은 홀드아웃은 '비교 불가'로 잡힌다."""
    shared = "이 문서는 사업 제휴 조건과 단가 산정 근거를 정리한 내부 검토 자료이다."
    samples = [_sample("TS", shared + " 추가 본문 %d." % i) for i in range(6)]

    hold = tmp_path / "holdout.jsonl"
    with io.open(hold, "w", encoding="utf-8") as f:
        for i in range(6):
            f.write(json.dumps(
                {"label": "TS", "text": shared + " 평가용 꼬리 %d." % i},
                ensure_ascii=False) + "\n")

    code, summary = _run_builder(
        monkeypatch, tmp_path, samples, ["--holdout", str(hold), "--strict"]
    )
    rep = summary["holdout_independence"][str(hold)]
    assert rep["usable_for_comparison"] is False
    assert rep["shared_sentence_coverage"] > 0.02
    assert summary["problems"]
    assert code == 1, "--strict 는 exit 1 이어야 한다"


def test_builder_passes_on_independent_holdout(monkeypatch, tmp_path):
    """겹치지 않는 홀드아웃은 통과한다 — 늘 막는 게이트가 아님을 고정."""
    samples = [_sample(("TS", "S1", "S2", "S3")[i % 4],
                       "사내 검토 기록 %d 번 문서입니다. " % i * (20 + i))
               for i in range(28)]

    hold = tmp_path / "holdout.jsonl"
    with io.open(hold, "w", encoding="utf-8") as f:
        for i in range(28):
            f.write(json.dumps(
                {"label": ("TS", "S1", "S2", "S3")[i % 4],
                 "text": "별도로 작성한 평가용 지문 %d 입니다. " % i * (20 + i)},
                ensure_ascii=False) + "\n")

    code, summary = _run_builder(
        monkeypatch, tmp_path, samples, ["--holdout", str(hold), "--strict"]
    )
    rep = summary["holdout_independence"][str(hold)]
    assert rep["shared_document_text"] == 0
    assert rep["shared_sentence_coverage"] <= 0.02, rep
    assert code == 0, summary["problems"]


def test_builder_strict_flags_missing_holdout_file(monkeypatch, tmp_path):
    """경로를 잘못 줘도 조용히 지나가지 않는다 — 오타가 '검사 통과'가 되면 안 된다."""
    code, summary = _run_builder(
        monkeypatch, tmp_path, [_sample("TS", "본문")],
        ["--holdout", str(tmp_path / "없는파일.jsonl"), "--strict"],
    )
    assert code == 1
    assert any("파일 없음" in p for p in summary["problems"])
