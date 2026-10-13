"""§5: classify_repo evidence bulk insert (add_all) 검증.

분류당 evidence 7~11개 환경에서 단건 add() 루프 → add_all 1회로
DB 라운드트립 N→1 감소. add_all 호출 여부와 카운트만 검증 (mock db).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from koipa.repositories.classify_repo import ClassifyRepo
from koipa.schemas.classify import EvidenceSpan


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


def test_empty_inputs_skip_add_all() -> None:
    """spans가 비면 add_all도 호출되지 않음."""
    db = _mock_db()
    repo = ClassifyRepo(db)
    assert repo.add_evidence_from_spans(
        uuid.uuid4(), spans=[], default_chunk_id=uuid.uuid4()
    ) == 0
    assert db.add_all.call_count == 0


def test_contribution_over_column_max_is_clamped_not_overflowed(caplog) -> None:
    """2026-10-12: rule_engine의 score=count*weight는 상한이 없어 tad_cm_clsf_bss_mng.cndg_nvl
    (Numeric(4,3), 최대 9.999)을 넘으면 psycopg.errors.NumericValueOutOfRange로 INSERT 전체가
    실패해 근거 행이 통째로 사라졌다(KL 실운영 로그로 발견). count=2 * weight=9.99 처럼 쉽게
    재현되는 값으로, 클램프돼 저장은 되고 경고 로그가 남는지 확인한다.
    """
    db = _mock_db()
    repo = ClassifyRepo(db)
    spans = [EvidenceSpan(text="과다매칭키워드", start=0, end=7, weight=19.98, tag="keyword_match")]

    with caplog.at_level("WARNING"):
        n = repo.add_evidence_from_spans(
            uuid.uuid4(), spans=spans, default_chunk_id=uuid.uuid4()
        )

    assert n == 1
    objs_arg = db.add_all.call_args[0][0]
    assert objs_arg[0].contribution == 9.999
    assert any("clamped" in rec.message for rec in caplog.records)


def test_contribution_within_range_is_not_clamped() -> None:
    """정상 범위 값은 그대로 저장된다 — 클램프가 모든 값을 9.999로 깎는 게 아님을 확인."""
    db = _mock_db()
    repo = ClassifyRepo(db)
    spans = [EvidenceSpan(text="정상", start=0, end=2, weight=0.8, tag="keyword_match")]
    repo.add_evidence_from_spans(uuid.uuid4(), spans=spans, default_chunk_id=uuid.uuid4())
    objs_arg = db.add_all.call_args[0][0]
    assert objs_arg[0].contribution == 0.8
