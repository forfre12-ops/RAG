"""검수자 신뢰도 추적 — C-cons 고도화.

순수(db-unavailable)는 인프라 없이, 집계는 Postgres 있을 때(@db_backed). 신뢰도 = 검수자의
교정이 분류의 최신 합의와 일치한 비율. 번복되는 검수자↓, 결정 유지 검수자↑.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from koipa.db import SessionLocal, engine, session_scope
from koipa.db.models import Classification, ClassificationLevel, Correction, Document
from koipa.modules.m6_evaluation.reviewer_trust import (
    compute_reviewer_reliability,
    reviewer_reliability,
)


# ── 순수 경로 ────────────────────────────────────────────────────────────────


def test_reliability_db_unavailable_graceful(monkeypatch):
    import koipa.modules.m6_evaluation.reviewer_trust as mod

    class _Boom:
        def __enter__(self):
            raise OperationalError("x", {}, Exception("down"))

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod, "session_scope", lambda: _Boom())
    assert compute_reviewer_reliability() == []
    assert reviewer_reliability("anyone") is None


# ── DB-backed ────────────────────────────────────────────────────────────────


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def db_backed(obj):
    return pytest.mark.usefixtures("require_pg")(obj)


@pytest.fixture
def require_pg():
    if not _pg_ok():
        pytest.skip("Postgres not reachable")


@db_backed
class TestReviewerReliabilityLive:
    def test_overturned_reviewer_scores_lower(self):
        tag = uuid.uuid4().hex[:6]
        good, good2, bad = f"good-{tag}", f"good2-{tag}", f"bad-{tag}"
        s = SessionLocal()
        try:
            lv = {x.level_code: x.level_id for x in s.query(ClassificationLevel).all()}

            def _cls():
                d = Document(filename="x.pdf", source_format="pdf")
                s.add(d)
                s.flush()
                c = Classification(doc_id=d.doc_id, model_version=f"v-{tag}",
                                   predicted_level_id=lv["S3"], confidence=0.9, alternatives=[])
                s.add(c)
                s.flush()
                return c

            clsA, clsB = _cls(), _cls()
            # 삽입 순서 = correction_id 순서(최신 판정 tiebreak). 같은 트랜잭션 corrected_at 동일.
            # A: good→TS, good2→TS(최신)  → good·good2 모두 최신과 일치(agreed)
            # B: bad→S1, good→TS(최신)    → bad는 번복(overturned), good은 일치(agreed)
            for cls_, lvl, rv in [
                (clsA, "TS", good), (clsA, "TS", good2),
                (clsB, "S1", bad), (clsB, "TS", good),
            ]:
                s.add(Correction(classification_id=cls_.classification_id,
                                 original_level_id=lv["S3"], corrected_level_id=lv[lvl],
                                 direction="underclass", corrected_by=rv))
                s.flush()
            s.commit()

            res = {r["reviewer_id"]: r for r in compute_reviewer_reliability()}
            assert res[good]["reliability"] == 1.0 and res[good]["total"] == 2
            assert res[good2]["reliability"] == 1.0
            assert res[bad]["reliability"] == 0.0 and res[bad]["overturned"] == 1
            # 단일 조회
            assert reviewer_reliability(bad)["reliability"] == 0.0
            # 정렬: 고신뢰가 저신뢰보다 앞
            order = [r["reviewer_id"] for r in compute_reviewer_reliability()]
            assert order.index(good) < order.index(bad)
        finally:
            with session_scope() as db:
                db.query(Correction).filter(Correction.corrected_by.in_([good, good2, bad])).delete(
                    synchronize_session=False)
                db.query(Classification).filter_by(model_version=f"v-{tag}").delete()
            s.close()
