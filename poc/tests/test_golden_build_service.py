"""GoldenBuildService E2E 단위 테스트 (in-proc, fake label_fn — DB/Celery/LLM 불요).

submit → run_build(in-proc) → run-스코프 후보 파일 출력 + JobStore 상태를 검증한다.
"""
import json
import uuid

from lloydk.golden_builder import LabelPair
from lloydk.schemas.common import Actor
from lloydk.schemas.golden import GoldenBuildRequest
from lloydk.services.golden_build_service import GoldenBuildService

_ACTOR = Actor(user_id="t1", role="admin")


def _fake_label_fn(text: str) -> LabelPair:
    # "gold" 포함 → 동의·고신뢰(gold_consensus), 아니면 불일치(uncertain)
    if "gold" in text:
        return LabelPair("S1", 0.8, "S1", 0.9)
    return LabelPair("S2", 0.6, "S1", 0.9)


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
    assert gold[0]["label_source"] == "llm_judge_consensus" and gold[0]["source"] == "판례"

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
