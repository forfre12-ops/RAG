"""§5: classify_repo evidence bulk insert (add_all) 검증.

분류당 evidence 7~11개 환경에서 단건 add() 루프 → add_all 1회로
DB 라운드트립 N→1 감소. add_all 호출 여부와 카운트만 검증 (mock db).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from lloydk.repositories.classify_repo import ClassifyRepo
from lloydk.schemas.classify import EvidenceSpan, RagContextHit


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.add_all = MagicMock()
    return db


def test_evidence_from_spans_uses_add_all_once() -> None:
    db = _mock_db()
    repo = ClassifyRepo(db)
    spans = [
        EvidenceSpan(text=f"키워드{i}", start=i, end=i + 3, weight=0.5, tag="keyword_match")
        for i in range(8)
    ]
    n = repo.add_evidence_from_spans(
        uuid.uuid4(),
        spans=spans,
        default_chunk_id=uuid.uuid4(),
    )
    assert n == 8
    # 단건 add()는 호출 안 됨 — add_all 1회만
    assert db.add.call_count == 0, "단건 add는 호출되면 안 됨"
    assert db.add_all.call_count == 1, "add_all 1회만 호출되어야 함"
    objs_arg = db.add_all.call_args[0][0]
    assert len(objs_arg) == 8


def test_rag_evidence_from_hits_uses_add_all_once() -> None:
    db = _mock_db()
    repo = ClassifyRepo(db)
    hits = [
        RagContextHit(source_doc=str(uuid.uuid4()), chunk_id=str(uuid.uuid4()), score=0.7)
        for _ in range(11)
    ]
    n = repo.add_rag_evidence_from_hits(
        uuid.uuid4(),
        hits=hits,
        default_chunk_id=uuid.uuid4(),
    )
    assert n == 11
    assert db.add.call_count == 0
    assert db.add_all.call_count == 1
    assert len(db.add_all.call_args[0][0]) == 11


def test_empty_inputs_skip_add_all() -> None:
    """spans/hits가 비면 add_all도 호출되지 않음."""
    db = _mock_db()
    repo = ClassifyRepo(db)
    assert repo.add_evidence_from_spans(
        uuid.uuid4(), spans=[], default_chunk_id=uuid.uuid4()
    ) == 0
    assert repo.add_rag_evidence_from_hits(
        uuid.uuid4(), hits=[], default_chunk_id=uuid.uuid4()
    ) == 0
    assert db.add_all.call_count == 0
