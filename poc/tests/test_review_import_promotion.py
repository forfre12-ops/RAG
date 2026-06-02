"""human_review 검수 결과가 기존 pseudo 라벨 gold 문서를 SKIP하지 않고
human_review로 '승격'하는지 검증한다.

이 동작이 깨지면(예전 append-only + SKIP), 큐가 기존 gold 문서로 구성되는
탓에 human_review 레코드가 0건만 생겨 운영 readiness 게이트가 영원히
BLOCKED 상태로 남는다.
"""
import json

from scripts import import_review_corrections as iric


def _seed_gold(path, records):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _correction(doc_id, model_label, human_label, text="full text"):
    return {
        "doc_id": doc_id,
        "model_label": model_label,
        "human_label": human_label,
        "review_decision": "corrected" if model_label != human_label else "correct",
        "reviewer_id": "reviewer-1",
        "text": text,
        "domain": "",
        "document_type": "",
        "reason_code": "trade_secret",
    }


def test_existing_pseudo_doc_is_promoted_not_skipped(tmp_path, monkeypatch):
    gold = tmp_path / "classification_gold.jsonl"
    _seed_gold(
        gold,
        [
            {"doc_id": "d1", "text": "t", "label": "S3", "label_source": "llm_judge_primary"},
            {"doc_id": "d2", "text": "t", "label": "S2", "label_source": "llm_judge_consensus"},
        ],
    )
    monkeypatch.setattr(iric, "GOLD_REAL_PATH", gold)

    # human says d1 is actually S2 (model had said S3 -> high-risk underclass)
    n = iric.merge_into_gold([_correction("d1", "S3", "S2")], dry_run=False)
    assert n == 1

    rows = [json.loads(l) for l in gold.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 2  # promoted in place, not appended
    by_id = {r["doc_id"]: r for r in rows}
    assert by_id["d1"]["label_source"] == "human_review"
    assert by_id["d1"]["label"] == "S2"
    assert by_id["d1"]["model_label"] == "S3"  # needed for the agreement gate
    assert by_id["d2"]["label_source"] == "llm_judge_consensus"  # untouched


def test_unknown_doc_is_appended_as_human_review(tmp_path, monkeypatch):
    gold = tmp_path / "classification_gold.jsonl"
    _seed_gold(gold, [{"doc_id": "d1", "text": "t", "label": "S3", "label_source": "llm_judge_primary"}])
    monkeypatch.setattr(iric, "GOLD_REAL_PATH", gold)

    n = iric.merge_into_gold([_correction("new1", "S2", "S1")], dry_run=False)
    assert n == 1

    rows = [json.loads(l) for l in gold.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_id = {r["doc_id"]: r for r in rows}
    assert len(rows) == 2
    assert by_id["new1"]["label_source"] == "human_review"
    assert by_id["new1"]["label"] == "S1"
