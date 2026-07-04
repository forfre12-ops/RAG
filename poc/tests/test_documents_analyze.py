"""POST /documents/analyze — 업로드 1회로 파싱→검수게이트→분류 진단 엔드포인트.

시연/관리자 콘솔이 "이 문서가 어떻게 파싱됐고, 어떤 게이트를 거쳐, 어떤 등급이 됐는지"를
한 화면에 그리는 백본. 실제 gold 문서(docx)를 TestClient로 업로드해 전 구간 계약을 잠근다.
DB 불요(persist 안 함, content 분류) — conftest inmemory/hash 폴백으로 in-process 동작.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lloydk.api.app import app

pytestmark = pytest.mark.slow  # classify 모델 로드

_POC = Path(__file__).resolve().parents[1]
_HDR = {"X-API-Key": "test-key", "X-Actor-Role": "admin"}
_GOLD_DOCX = _POC / "datasets" / "gold" / "formats" / "docx" / "G50-TS-00.docx"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _upload(client, path: Path, **form):
    data = path.read_bytes()
    return client.post(
        "/api/v1/documents/analyze",
        headers=_HDR,
        files={"file": (path.name, data, "application/octet-stream")},
        data=form or None,
    )


class TestAnalyzeContract:
    def test_real_docx_full_journey(self, client):
        if not _GOLD_DOCX.exists():
            pytest.skip("gold docx 코퍼스 없음")
        r = _upload(client, _GOLD_DOCX)
        assert r.status_code == 200, r.text
        j = r.json()

        # --- 파싱 ---
        p = j["parse"]
        assert p["source_format"] == "docx"
        assert p["extraction_method"] == "parser"
        assert p["char_count"] > 100
        assert p["chunk_count"] >= 1
        assert p["extraction_quality"] > 0.5

        # --- 게이트 (깨끗한 docx → 무오탐) ---
        assert j["gate"]["requires_review"] is False
        assert j["gate"]["reasons"] == []

        # --- 분류 (실제 서빙 경로) ---
        c = j["classification"]
        assert c is not None
        assert c["label"] in ("TS", "S1", "S2", "S3")
        assert 0.0 <= c["confidence"] <= 1.0
        assert c["status"] in ("staging", "needs_review")
        assert c["model_version"]
        assert isinstance(c["factors"], dict)

        # --- 단계 타임라인 (시연 스텝퍼 원천) ---
        names = [s["name"] for s in j["stages"]]
        for expected in ["업로드", "추출", "정규화·PII마스킹", "청킹", "검수게이트", "분류", "결과"]:
            assert expected in names, f"단계 누락: {expected} (있음: {names})"
        assert all(s["status"] in ("done", "review", "skipped", "fail") for s in j["stages"])
        # 깨끗한 문서는 추출/청킹이 fail이면 안 됨
        by_name = {s["name"]: s for s in j["stages"]}
        assert by_name["추출"]["status"] == "done"
        assert by_name["청킹"]["status"] == "done"

        # --- 미리보기 ---
        assert len(j["text_preview"]) > 0

    def test_empty_file_rejected(self, client):
        r = client.post(
            "/api/v1/documents/analyze",
            headers=_HDR,
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert r.status_code == 422

    def test_requires_auth(self, client):
        if not _GOLD_DOCX.exists():
            pytest.skip("gold docx 코퍼스 없음")
        data = _GOLD_DOCX.read_bytes()
        r = client.post(
            "/api/v1/documents/analyze",
            files={"file": ("G50-TS-00.docx", data, "application/octet-stream")},
        )
        assert r.status_code in (401, 403)

    def test_corrupt_file_gate_fires(self, client):
        """손상 PDF → 게이트 발동(extract_error/low_quality) 또는 빈 본문 격리."""
        r = client.post(
            "/api/v1/documents/analyze",
            headers=_HDR,
            files={"file": ("corrupt.pdf", b"%PDF-1.4 not a real pdf \x00\x01", "application/pdf")},
        )
        assert r.status_code == 200, r.text
        j = r.json()
        # 손상 문서는 자동 확정으로 조용히 통과하면 안 된다 — 게이트 또는 빈본문 격리
        gate_fired = j["gate"]["requires_review"]
        empty_body = j["parse"]["char_count"] == 0
        assert gate_fired or empty_body, f"손상 문서 미격리: {j['gate']}, chars={j['parse']['char_count']}"
