"""[P0#3 후속] ingestion 열화추출 격리를 서빙 진입에서 존중 — 격리문서 자동분류 방지.

배경: ingestion 이 OCR/저품질 추출을 processing_status='needs_review'로 격리하지만, 서빙
경로(_fetch_content_by_doc_id)가 그 상태를 읽지 않아 격리문서도 POST /classify?doc_id 로
자동확정될 수 있었다(하프와이어링). 저장된 processing_status 를 서빙 진입에서 존중해 needs_review
로 라우팅(등급은 산출하되 자동확정만 격리).

DB/모델 불요 — 순수 판정 + rule-fallback classify(_fetch_content_by_doc_id 스텁).
"""
from __future__ import annotations

import uuid

from lloydk.schemas.classify import ClassifyRequest
from lloydk.services.classify_service import ClassifyService

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
