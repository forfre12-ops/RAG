"""교정→라벨 재빌드 테스트 — A2-①.

순수 경로(write_labeled_jsonl, DB 미가용 graceful)는 인프라 없이, 재빌드 본체는
Postgres 있을 때만(@db_backed) 검증. 핵심 계약:
  - 정답 = 문서별 최신 correction의 corrected_level_code
  - 본문 = Chunk.content 우선, 없으면 text_preview, 둘 다 없으면 그 문서 제외(라벨 날조 금지)
  - correction_ids = 실제 학습셋에 포함된 것만(반영 없이 소비 = 유실, 차단)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from lloydk.db import SessionLocal, engine, session_scope
from lloydk.db.models import (
    Chunk,
    Classification,
    ClassificationLevel,
    Correction,
    Document,
)
from lloydk.modules.m6_evaluation.corrections_rebuild import (
    RebuildResult,
    build_labeled_rows_from_corrections,
    merge_into_train_jsonl,
    write_labeled_jsonl,
)


# ── 순수 경로 (DB 불요) ──────────────────────────────────────────────────────


def test_write_labeled_jsonl_roundtrip(tmp_path: Path):
    res = RebuildResult(
        rows=[{"text": "비밀 본문", "label": "TS"}, {"text": "공개 본문", "label": "S3"}],
        correction_ids=[1, 2],
    )
    out = write_labeled_jsonl(res, tmp_path / "sub" / "train.jsonl")
    lines = Path(out).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"text": "비밀 본문", "label": "TS"}
    assert json.loads(lines[1])["label"] == "S3"


def test_write_labeled_jsonl_empty_makes_empty_file(tmp_path: Path):
    out = write_labeled_jsonl(RebuildResult(), tmp_path / "empty.jsonl")
    assert Path(out).read_text(encoding="utf-8") == ""


def test_merge_into_train_jsonl_appends_to_base(tmp_path: Path):
    base = tmp_path / "train.jsonl"
    base.write_text(
        json.dumps({"text": "기존1", "label": "S2"}, ensure_ascii=False) + "\n"
        + json.dumps({"text": "기존2", "label": "S3"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    res = RebuildResult(rows=[{"text": "교정1", "label": "TS"}], correction_ids=[5])
    out = merge_into_train_jsonl(base, res, tmp_path / "merged.jsonl")
    lines = Path(out).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3                       # base 2 + 교정 1
    assert json.loads(lines[-1]) == {"text": "교정1", "label": "TS"}


def test_merge_without_base_uses_corrections_only(tmp_path: Path):
    res = RebuildResult(rows=[{"text": "교정만", "label": "S1"}], correction_ids=[1])
    out = merge_into_train_jsonl(tmp_path / "nonexistent.jsonl", res, tmp_path / "m.jsonl")
    lines = Path(out).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_merge_no_rows_returns_none(tmp_path: Path):
    assert merge_into_train_jsonl(None, RebuildResult(), tmp_path / "x.jsonl") is None


def test_rebuild_db_unavailable_graceful(monkeypatch):
    """session_scope가 SQLAlchemyError를 던지면 빈 결과 + reason."""
    from lloydk.modules.m6_evaluation import corrections_rebuild as mod

    class _Boom:
        def __enter__(self):
            raise OperationalError("x", {}, Exception("db down"))

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod, "session_scope", lambda: _Boom())
    res = build_labeled_rows_from_corrections()
    assert res.row_count == 0
    assert res.reason.startswith("db_unavailable")


# ── DB-backed ────────────────────────────────────────────────────────────────


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def db_backed(obj):
    # PG만 필요(ES 불요) — fullstack 게이트 대신 require_pg로 PG-down만 skip.
    return pytest.mark.usefixtures("require_pg")(obj)


@pytest.fixture
def require_pg():
    if not _pg_ok():
        pytest.skip("Postgres not reachable")


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def levels(db):
    return {lv.level_code: lv.level_id for lv in db.query(ClassificationLevel).all()}


def _seed_doc_with_chunks(db, *, chunks=None, preview=None) -> Document:
    doc = Document(
        filename="x.pdf", source_format="pdf",
        text_preview=preview,
    )
    db.add(doc)
    db.flush()
    for i, content in enumerate(chunks or []):
        db.add(Chunk(
            doc_id=doc.doc_id, chunk_index=i,
            content=content, token_count=len(content.split()), char_count=len(content),
        ))
    db.flush()
    return doc


def _seed_cls(db, levels, doc, code, version) -> Classification:
    cls = Classification(
        doc_id=doc.doc_id, model_version=version,
        predicted_level_id=levels[code], confidence=0.9, alternatives=[],
    )
    db.add(cls)
    db.flush()
    return cls


@db_backed
class TestRebuildLive:
    def test_chunk_text_and_latest_label(self, db, levels):
        version = f"v-reb-{uuid.uuid4().hex[:6]}"
        doc = _seed_doc_with_chunks(db, chunks=["마스터 키: ABCDEF 공정 파라미터 표"])
        cls = _seed_cls(db, levels, doc, "S3", version)
        # 두 교정: 먼저 S2, 나중에 TS — 최신(TS)이 정답
        c1 = Correction(classification_id=cls.classification_id,
                        original_level_id=levels["S3"], corrected_level_id=levels["S2"],
                        direction="underclass", corrected_by="qa1")
        db.add(c1)
        db.flush()
        c2 = Correction(classification_id=cls.classification_id,
                        original_level_id=levels["S3"], corrected_level_id=levels["TS"],
                        direction="underclass", corrected_by="qa2")
        db.add(c2)
        db.flush()
        db.commit()
        try:
            res = build_labeled_rows_from_corrections()
            assert res.row_count == 1
            assert res.rows[0]["label"] == "TS"          # 최신 교정
            assert "마스터 키" in res.rows[0]["text"]      # 본문은 chunk
            # 같은 문서의 unconsumed correction은 전부 소비대상
            assert set(res.correction_ids) == {c1.correction_id, c2.correction_id}
        finally:
            with session_scope() as s:
                s.query(Correction).filter(
                    Correction.correction_id.in_([c1.correction_id, c2.correction_id])
                ).delete(synchronize_session=False)
                s.query(Classification).filter_by(model_version=version).delete()

    def test_text_preview_fallback_and_skip_when_no_text(self, db, levels):
        version = f"v-reb2-{uuid.uuid4().hex[:6]}"
        # 문서 A: chunk 없음, preview 있음 → preview 사용
        doc_a = _seed_doc_with_chunks(db, preview="프리뷰 본문 충분히 김 영업비밀")
        cls_a = _seed_cls(db, levels, doc_a, "S3", version)
        ca = Correction(classification_id=cls_a.classification_id,
                        original_level_id=levels["S3"], corrected_level_id=levels["S1"],
                        direction="underclass", corrected_by="qa")
        db.add(ca)
        db.flush()
        # 문서 B: chunk도 preview도 없음 → 제외 + 소비 안 됨
        doc_b = _seed_doc_with_chunks(db)
        cls_b = _seed_cls(db, levels, doc_b, "S3", version)
        cb = Correction(classification_id=cls_b.classification_id,
                        original_level_id=levels["S3"], corrected_level_id=levels["TS"],
                        direction="underclass", corrected_by="qa")
        db.add(cb)
        db.flush()
        db.commit()
        try:
            res = build_labeled_rows_from_corrections()
            assert res.row_count == 1
            assert res.rows[0]["label"] == "S1"
            assert "프리뷰 본문" in res.rows[0]["text"]
            assert res.skipped_no_text == 1
            assert cb.correction_id not in res.correction_ids   # 본문 없는 문서는 미소비
            assert ca.correction_id in res.correction_ids
        finally:
            with session_scope() as s:
                s.query(Correction).filter(
                    Correction.correction_id.in_([ca.correction_id, cb.correction_id])
                ).delete(synchronize_session=False)
                s.query(Classification).filter_by(model_version=version).delete()
