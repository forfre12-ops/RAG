"""[C20·C22] health probe — reranker effective 노출 + 추출기 의존성 가시화.

DB/모델 불필요(probe는 import-가능성·캐시만 조회) — 빠르게 도는 단위 테스트.
"""

from __future__ import annotations

import lloydk.config as cfg
from lloydk.api import health


def test_check_extractors_contract() -> None:
    out = health._check_extractors()
    assert out["ok"] is True  # optional 의존 — 서비스 가용성 판단엔 미반영
    assert out["status"] in {"ok", "degraded"}
    assert isinstance(out["unavailable"], list)
    expected = {
        "hwp_body(rhwp)",
        "hwp_table(pyhwp/hwp5html)",
        "xls(xlrd)",
        "xlsx(openpyxl)",
        "docx(python-docx)",
        "pptx(python-pptx)",
        "pdf_text(pdfminer)",
        "pdf_table(pdfplumber)",
        "pdf_render(fitz/pdf2image)",
        "pdf_scan(poppler)",
        "ocr(tesseract)",
        "doc(antiword)",
    }
    assert set(out["probes"].keys()) == expected
    for v in out["probes"].values():
        assert isinstance(v["available"], bool)
    # unavailable 목록은 available=False 인 probe와 정확히 일치해야 한다.
    assert set(out["unavailable"]) == {k for k, v in out["probes"].items() if not v["available"]}


def test_check_reranker_not_initialized(monkeypatch) -> None:
    from lloydk.adapters.reranker import _RERANKER_CACHE

    monkeypatch.setattr(cfg.settings, "reranker_provider", "bge", raising=False)
    saved = dict(_RERANKER_CACHE)
    _RERANKER_CACHE.clear()
    try:
        out = health._check_reranker()
        assert out["ok"] is True
        assert out["status"] == "not_initialized"
        assert out["effective"] is None
    finally:
        _RERANKER_CACHE.clear()
        _RERANKER_CACHE.update(saved)


def test_check_reranker_detects_bge_fallback(monkeypatch) -> None:
    """configured=bge인데 캐시에 NoopReranker → 폴백 가시화(종전 /healthz는 못 봤음)."""
    from lloydk.adapters.reranker import _RERANKER_CACHE, NoopReranker

    monkeypatch.setattr(cfg.settings, "reranker_provider", "bge", raising=False)
    saved = dict(_RERANKER_CACHE)
    _RERANKER_CACHE.clear()
    _RERANKER_CACHE["bge"] = NoopReranker()  # bge 초기화 실패 → noop 폴백 상태 모사
    try:
        out = health._check_reranker()
        assert out["status"] == "fallback"
        assert out["fell_back_to_noop"] is True
        assert out["effective"] == "NoopReranker"
        assert out["ok"] is True  # 폴백은 서비스-다운 아님(품질 보강 소폭)
    finally:
        _RERANKER_CACHE.clear()
        _RERANKER_CACHE.update(saved)


def test_check_reranker_noop_configured_is_ok(monkeypatch) -> None:
    from lloydk.adapters.reranker import _RERANKER_CACHE, NoopReranker

    monkeypatch.setattr(cfg.settings, "reranker_provider", "noop", raising=False)
    saved = dict(_RERANKER_CACHE)
    _RERANKER_CACHE.clear()
    _RERANKER_CACHE["noop"] = NoopReranker()
    try:
        out = health._check_reranker()
        assert out["status"] == "ok"
        assert out["fell_back_to_noop"] is False
    finally:
        _RERANKER_CACHE.clear()
        _RERANKER_CACHE.update(saved)
