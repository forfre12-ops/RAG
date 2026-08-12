"""GoldenBuildService E2E 단위 테스트 (in-proc, fake label_fn — DB/Celery/LLM 불요).

submit → run_build(in-proc) → run-스코프 후보 파일 출력 + JobStore 상태를 검증한다.
"""
import json
import uuid

from koipa.golden_builder import LabelPair
from koipa.schemas.common import Actor
from koipa.schemas.golden import GoldenBuildRequest
from koipa.services.golden_build_service import GoldenBuildService

_ACTOR = Actor(user_id="t1", role="admin")


def _fake_label_fn(text: str) -> LabelPair:
    # "gold" 포함 → 합의+근거(gold_candidate), 아니면 불일치(needs_review)
    if "gold" in text:
        return LabelPair("S1", 0.8, "S1", 0.9, has_real_evidence=True)
    return LabelPair("S2", 0.6, "S1", 0.9, has_real_evidence=True)


def test_submit_inproc_writes_outputs_and_status(tmp_path):
    req = GoldenBuildRequest(
        source_type="inline",
        docs=[
            {"doc_id": "d1", "text": "gold 문서", "source": "판례", "domain": "legal"},
            {"doc_id": "d2", "text": "검수대상 문서"},
        ],
        out_dir=str(tmp_path),
        actor=_ACTOR,
    )
    svc = GoldenBuildService()
    resp = svc.submit(req, label_fn=_fake_label_fn)

    assert isinstance(resp.golden_job_id, uuid.UUID)
    assert resp.status_url.endswith(str(resp.golden_job_id))

    st = svc.get_status(resp.golden_job_id)
    assert st is not None and st.status == "done"
    assert st.gold_count == 1 and st.uncertain_count == 1
    assert st.stats["gold_by_grade"] == {"S1": 1}

    gold_path = tmp_path / f"build_{resp.golden_job_id}.jsonl"
    assert gold_path.exists()
    gold = [json.loads(l) for l in gold_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(gold) == 1
    assert gold[0]["doc_id"] == "d1" and gold[0]["label"] == "S1"
    assert gold[0]["label_source"] == "rule_llm_agreement" and gold[0]["source"] == "판례"

    unc_path = tmp_path / f"uncertain_{resp.golden_job_id}.jsonl"
    unc = [json.loads(l) for l in unc_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(unc) == 1 and unc[0]["doc_id"] == "d2" and unc[0]["label"] is None


def test_get_status_unknown_job_is_none():
    svc = GoldenBuildService()
    assert svc.get_status(uuid.uuid4()) is None


def test_corpus_source_jsonl(tmp_path):
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(
        json.dumps({"doc_id": "x", "title": "제목", "body": "gold 본문", "source": "판례"}) + "\n",
        encoding="utf-8",
    )
    req = GoldenBuildRequest(
        source_type="corpus", corpus_dir=str(corpus), out_dir=str(tmp_path / "out"), actor=_ACTOR,
    )
    svc = GoldenBuildService()
    resp = svc.submit(req, label_fn=_fake_label_fn)
    st = svc.get_status(resp.golden_job_id)
    assert st.status == "done"
    assert (st.gold_count + st.uncertain_count) == 1
    assert st.gold_count == 1  # "제목\n\ngold 본문" → gold


def test_render_review_returns_html_for_completed_job(tmp_path):
    req = GoldenBuildRequest(
        source_type="inline",
        docs=[
            {"doc_id": "d1", "text": "gold 문서", "domain": "legal"},
            {"doc_id": "d2", "text": "검수대상 문서"},
        ],
        out_dir=str(tmp_path),
        actor=_ACTOR,
    )
    svc = GoldenBuildService()
    resp = svc.submit(req, label_fn=_fake_label_fn)
    html = svc.render_review(resp.golden_job_id)
    assert html is not None
    assert html.startswith("<!doctype html>")
    assert "d1" in html and "d2" in html        # gold + uncertain 둘 다 포함
    assert f"job {resp.golden_job_id}" in html   # subtitle


def test_render_review_unknown_job_is_none():
    svc = GoldenBuildService()
    assert svc.render_review(uuid.uuid4()) is None


def test_holdout_leakage_excluded(tmp_path):
    holdout = tmp_path / "holdout.jsonl"
    holdout.write_text(json.dumps({"text": "누출문서"}) + "\n", encoding="utf-8")
    req = GoldenBuildRequest(
        source_type="inline",
        docs=[
            {"doc_id": "d1", "text": "누출 문서 gold"},   # holdout "누출문서"와… 공백+gold라 해시 다름
            {"doc_id": "d2", "text": "누출문서"},          # holdout과 동일 → 드롭
        ],
        holdout_path=str(holdout),
        out_dir=str(tmp_path / "out"),
        actor=_ACTOR,
    )
    svc = GoldenBuildService()
    resp = svc.submit(req, label_fn=_fake_label_fn)
    st = svc.get_status(resp.golden_job_id)
    assert st.stats["dropped_leaked"] == 1
    assert st.gold_count == 1  # d1만 남음(d2는 누출 드롭)


def _agree_no_evidence_label_fn(text: str) -> LabelPair:
    # 합의(rule==llm=S2)지만 룰 시드 근거 없음 — require_evidence에 따라 admission 갈림.
    return LabelPair("S2", 0.6, "S2", 0.9, has_real_evidence=False)


def test_require_evidence_flag_flows_to_gate(tmp_path):
    docs = [{"doc_id": "d1", "text": "근거없는 합의 문서"}]
    svc = GoldenBuildService()
    # 기본(require_evidence=True): 근거 없으면 needs_review(운영 게이트).
    req_t = GoldenBuildRequest(source_type="inline", docs=docs, out_dir=str(tmp_path / "t"), actor=_ACTOR)
    st_t = svc.get_status(svc.submit(req_t, label_fn=_agree_no_evidence_label_fn).golden_job_id)
    assert st_t.gold_count == 0 and st_t.uncertain_count == 1
    # 합성모드(require_evidence=False): 합의+self-consistency면 gold_candidate.
    req_f = GoldenBuildRequest(source_type="inline", docs=docs, require_evidence=False,
                               out_dir=str(tmp_path / "f"), actor=_ACTOR)
    st_f = svc.get_status(svc.submit(req_f, label_fn=_agree_no_evidence_label_fn).golden_job_id)
    assert st_f.gold_count == 1 and st_f.uncertain_count == 0
