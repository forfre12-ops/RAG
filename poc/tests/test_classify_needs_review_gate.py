"""[P0#3 후속] ingestion 열화추출 격리를 서빙 진입에서 존중 — 격리문서 자동분류 방지.

배경: ingestion 이 OCR/저품질 추출을 processing_status='needs_review'로 격리하지만, 서빙
경로(_fetch_content_by_doc_id)가 그 상태를 읽지 않아 격리문서도 POST /classify?doc_id 로
자동확정될 수 있었다(하프와이어링). 저장된 processing_status 를 서빙 진입에서 존중해 needs_review
로 라우팅(등급은 산출하되 자동확정만 격리).

DB/모델 불요 — 순수 판정 + rule-fallback classify(_fetch_content_by_doc_id 스텁).
"""
from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace

from koipa.schemas.classify import ClassifyRequest
from koipa.services.classify_service import ClassifyService

_BODY = "영업비밀 등급 분류 대상 본문입니다. 반도체 공정 레시피와 조성 비율. " * 5
_FLAG_WARN = "flagged at ingestion"


# ── 순수 판정 ────────────────────────────────────────────────────────────────

def test_ingestion_review_flagged_predicate():
    f = ClassifyService._ingestion_review_flagged
    assert f("needs_review") is True
    assert f("failed") is True
    assert f("processed") is False
    assert f(None) is False          # 상태 미상 = 격리 아님(과차단 방지)
    assert f("staging") is False


# ── 서빙 라우팅 (rule-fallback classify, _fetch 스텁) ─────────────────────────

def _classify_with_status(monkeypatch, status):
    svc = ClassifyService.get_instance()
    # doc_id 경로: content 없이 → _fetch_content_by_doc_id 가 (본문, status) 반환하도록 스텁.
    monkeypatch.setattr(svc, "_fetch_content_by_doc_id", lambda doc_id: (_BODY, status))
    req = ClassifyRequest(doc_id=str(uuid.uuid4()), use_rag=False)
    return svc.classify(req)


def test_flagged_doc_routes_to_needs_review(monkeypatch):
    res = _classify_with_status(monkeypatch, "needs_review")
    assert res.status == "needs_review"
    assert any(_FLAG_WARN in w for w in res.warnings)
    assert res.automation_assessment is not None
    # low-confidence가 먼저 성립하면 인과 사유는 그 게이트 하나지만, 적재 격리 신호도 보존한다.
    assert "ingestion-degraded" in res.automation_assessment.review_gate_hits
    assert res.automation_assessment.current_policy_eligible is False


def test_failed_doc_routes_to_needs_review(monkeypatch):
    res = _classify_with_status(monkeypatch, "failed")
    assert res.status == "needs_review"
    assert any(_FLAG_WARN in w for w in res.warnings)


def test_processed_doc_not_flagged_by_this_gate(monkeypatch):
    # processed 문서는 이 게이트로 needs_review 강제 안 함(다른 게이트는 걸 수 있음 → warning 부재만 단언).
    res = _classify_with_status(monkeypatch, "processed")
    assert not any(_FLAG_WARN in w for w in res.warnings)


def test_none_status_not_flagged(monkeypatch):
    res = _classify_with_status(monkeypatch, None)
    assert not any(_FLAG_WARN in w for w in res.warnings)


# ── content-provided 경로: _ingestion_flagged_for_doc (UUID 적재문서 격리 존중) ──────
# 본문을 직접 넘겨 분류할 때(스토리지 재읽기 우회)도 이미 적재된 UUID 문서의 needs_review
# 격리를 존중하는 배선. 기존 테스트는 doc_id-fetch 경로만 커버했고, 이 DB 조회 분기는
# TESTING+무PG서 _skip_optional_db_work()=True 로 死코드였다 — monkeypatch 로 강제 커버.

def test_ingestion_flagged_for_doc_non_uuid_skips_db(monkeypatch):
    """비-UUID doc_id(적재문서 아님: /analyze 파일명·콘솔 샘플)는 조회 없이 조기 False."""
    svc = ClassifyService.get_instance()

    def _boom():
        raise AssertionError("비-UUID 는 DB 경로에 도달하면 안 됨")

    # _parse_doc_uuid 실패로 _skip_optional_db_work 호출 전에 반환돼야 함 → 함정이 안 터짐.
    monkeypatch.setattr("koipa.services.classify_service._skip_optional_db_work", _boom)
    assert svc._ingestion_flagged_for_doc("guide.txt") is False
    assert svc._ingestion_flagged_for_doc("경계-영업-주간공유") is False


def _patch_doc_lookup(monkeypatch, *, status, doc_exists=True, raise_exc=None):
    monkeypatch.setattr(
        "koipa.services.classify_service._skip_optional_db_work", lambda: False
    )

    @contextlib.contextmanager
    def _fake_scope():
        yield object()

    monkeypatch.setattr("koipa.db.session_scope", _fake_scope)

    class _FakeRepo:
        def __init__(self, _db):
            pass

        def get(self, _doc_uuid):
            if raise_exc is not None:
                raise raise_exc
            return SimpleNamespace(processing_status=status) if doc_exists else None

    monkeypatch.setattr("koipa.repositories.document_repo.DocumentRepo", _FakeRepo)


def test_ingestion_flagged_for_doc_reads_persisted_status(monkeypatch):
    """UUID 적재문서: DB의 processing_status 를 조회해 격리 여부 판정(DB 분기 커버)."""
    svc = ClassifyService.get_instance()
    _patch_doc_lookup(monkeypatch, status="needs_review")
    assert svc._ingestion_flagged_for_doc(str(uuid.uuid4())) is True
    _patch_doc_lookup(monkeypatch, status="ready")
    assert svc._ingestion_flagged_for_doc(str(uuid.uuid4())) is False
    _patch_doc_lookup(monkeypatch, status=None, doc_exists=False)  # 미존재 = 격리 아님
    assert svc._ingestion_flagged_for_doc(str(uuid.uuid4())) is False


def test_ingestion_flagged_for_doc_db_error_is_false(monkeypatch):
    """DB 조회 예외는 best-effort False(과차단 방지·서빙 핫패스 안전)."""
    svc = ClassifyService.get_instance()
    _patch_doc_lookup(monkeypatch, status=None, raise_exc=RuntimeError("db down"))
    assert svc._ingestion_flagged_for_doc(str(uuid.uuid4())) is False


def test_content_path_respects_ingestion_isolation(monkeypatch):
    """본문 직접 제공(content) + UUID 적재문서가 needs_review 격리면 서빙도 격리 존중."""
    svc = ClassifyService.get_instance()
    monkeypatch.setattr(svc, "_ingestion_flagged_for_doc", lambda doc_id: True)
    req = ClassifyRequest(doc_id=str(uuid.uuid4()), content=_BODY, use_rag=False)
    res = svc.classify(req)
    assert res.status == "needs_review"
    assert any(_FLAG_WARN in w for w in res.warnings)
