"""[C22] health probe — 추출기 의존성 가시화.

DB/모델 불필요(probe는 import-가능성·캐시만 조회) — 빠르게 도는 단위 테스트.
"""

from __future__ import annotations

from koipa.api import health


def test_check_extractors_contract() -> None:
    out = health._check_extractors()
    assert out["ok"] is True  # optional 의존 — 서비스 가용성 판단엔 미반영
    assert out["status"] in {"ok", "degraded"}
    assert isinstance(out["unavailable"], list)
    expected = {
        "hwp_body(rhwp)",
        "hwp_table(unhwp)",
        "xls(xlrd)",
        "xlsx(openpyxl)",
        "docx(python-docx)",
        "pptx(python-pptx)",
        "pdf_text(pdfminer)",
        "pdf_table(pdfplumber)",
        "pdf_fallback(fitz)",
        "doc(antiword)",
    }
    assert set(out["probes"].keys()) == expected
    for v in out["probes"].values():
        assert isinstance(v["available"], bool)
    # unavailable 목록은 available=False 인 probe와 정확히 일치해야 한다.
    assert set(out["unavailable"]) == {k for k, v in out["probes"].items() if not v["available"]}


def test_operational_config_states_regulation_reference_mode(monkeypatch) -> None:
    monkeypatch.setattr(health.settings, "regulation_reference_enabled", True)
    monkeypatch.setattr(health.settings, "regulation_llm_summary_enabled", False)
    status = health._operational_config()["regulation_reference"]
    assert status == {
        "enabled": True,
        "summary_enabled": False,
        "mode": "reference_only",
        "summary_requires_explicit_request": True,
    }

    monkeypatch.setattr(health.settings, "regulation_llm_summary_enabled", True)
    assert health._operational_config()["regulation_reference"]["mode"] == "local_summary_on_request"
