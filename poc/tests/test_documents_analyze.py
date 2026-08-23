"""POST /documents/analyze — 업로드 1회로 파싱→검수게이트→분류 진단 엔드포인트.

시연/관리자 콘솔이 "이 문서가 어떻게 파싱됐고, 어떤 게이트를 거쳐, 어떤 등급이 됐는지"를
한 화면에 그리는 백본. 실제 gold 문서(docx)를 TestClient로 업로드해 전 구간 계약을 잠근다.
DB 불요(persist 안 함, content 분류) — conftest inmemory/hash 폴백으로 in-process 동작.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from koipa.api.app import app

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

    def test_oversized_file_rejected(self, client, monkeypatch):
        """max_upload_mb 초과 업로드 → 413 (DoS/자원 보호). 파싱·분류 진입 전 차단."""
        from koipa import config as cfg

        monkeypatch.setattr(cfg.settings, "max_upload_mb", 1, raising=False)
        big = b"x" * (2 * 1024 * 1024)  # 2MB > 1MB 한도
        r = client.post(
            "/api/v1/documents/analyze",
            headers=_HDR,
            files={"file": ("big.txt", big, "text/plain")},
        )
        assert r.status_code == 413, r.text

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


class TestSyncChunkCap:
    """[용량 게이트] 청크 상한 초과 시 조용히 죽지 말고 즉시 이유를 말하고 거절한다.

    실측(2026-08-02 테스트서버) — 304KB .hwp 가 표 47개 회수 후 청크 155개가 되어
    분류에서 60초를 넘겼고, gunicorn 이 워커를 SIGABRT 로 죽였다. 클라이언트는
    설명 없는 빈 응답(curl: (52) Empty reply from server)을 받았고, --workers 1 이라
    동시에 처리 중이던 다른 요청도 함께 끊겼다. 바이트 상한(20MB)으로는 못 막는다 —
    비용 동인은 업로드 크기가 아니라 청크 수다.
    """

    def test_over_cap_rejected_with_actionable_413(self, client, monkeypatch):
        from koipa.config import settings

        monkeypatch.setattr(settings, "analyze_sync_max_chunks", 1, raising=False)
        r = _upload(client, _GOLD_DOCX)

        assert r.status_code == 413, r.text
        d = r.json()["detail"]
        # 진단 가능해야 한다 — 무엇이 얼마나 컸는지와 다음 행동이 함께 있어야 한다.
        assert "chunks" in d
        assert "비동기" in d or "async" in d
        # [2026-08-23] 접두사 없는 이름이 맞다. Settings 는 env_prefix 없이 필드명
        # 그대로 env 에 매핑된다(config.py "Settings는 env_prefix 없이 ..." 주석).
        # 실서버 확인: 223 API 컨테이너는 ANALYZE_SYNC_MAX_CHUNKS=400 으로 떠 있고
        # 그 값이 실제로 먹는다. 종전 단언은 있지도 않은 KOIPA_ 접두사를 요구해 항상 실패했다.
        assert "ANALYZE_SYNC_MAX_CHUNKS" in d

    def test_under_cap_passes(self, client, monkeypatch):
        """상한을 넉넉히 두면 기존 경로가 그대로 동작한다(게이트가 정상 문서를 막지 않는다)."""
        from koipa.config import settings

        monkeypatch.setattr(settings, "analyze_sync_max_chunks", 100_000, raising=False)
        assert _upload(client, _GOLD_DOCX).status_code == 200

    def test_zero_disables_gate(self, client, monkeypatch):
        """0 이하 = 검사 비활성(측정·디버깅용) — 상한 로직이 켜져 있어도 통과해야 한다."""
        from koipa.config import settings

        monkeypatch.setattr(settings, "analyze_sync_max_chunks", 0, raising=False)
        assert _upload(client, _GOLD_DOCX).status_code == 200


class TestClassifyFalse:
    """classify=false — 추출만 회수하고 분류·청크 상한을 건너뛴다.

    왜 필요한가: 상한이 있는 이유는 **분류가 청크 수에 비례**해서지 문서를 처리할 수
    없어서가 아니다. 시연 콘솔은 413 을 받으면 이 스위치로 본문만 받아
    POST /classify/async 로 넘긴다. 그 경로는 doc_id 가 비-UUID 면 영속화를 건너뛰므로
    (classify_service `_try_persist`) 화면 전체에서 DB 적재가 발생하지 않는다 —
    POST /documents 를 타지 않는 것이 요점이다.
    """

    def test_over_cap_passes_when_classify_false(self, client, monkeypatch):
        """상한을 넘겨도 413 이 아니라 200 — 게이트는 분류를 돌릴 때만 건다."""
        from koipa.config import settings

        monkeypatch.setattr(settings, "analyze_sync_max_chunks", 1, raising=False)
        r = _upload(client, _GOLD_DOCX, classify="false", full_text="true")
        assert r.status_code == 200, r.text

    def test_returns_text_without_classification(self, client, monkeypatch):
        """본문은 돌려주고 분류 결과는 없다 — 호출자가 비동기로 분류할 재료를 준다."""
        from koipa.config import settings

        monkeypatch.setattr(settings, "analyze_sync_max_chunks", 1, raising=False)
        j = _upload(client, _GOLD_DOCX, classify="false", full_text="true").json()

        assert j.get("classification") is None
        assert (j.get("text") or "").strip(), "full_text=true 인데 본문이 비었다"
        stage = next((s for s in j["stages"] if s["name"] == "분류"), None)
        assert stage is not None and stage["status"] == "skipped", j["stages"]
        # 추출·청킹 단계는 그대로 나와야 한다 — 화면의 단계 표시가 이걸로 그려진다.
        names = [s["name"] for s in j["stages"]]
        assert "추출" in names and "청킹" in names and "검수게이트" in names, names

    def test_default_still_classifies(self, client, monkeypatch):
        """기본값 True — 기존 호출자 동작 불변(스위치를 안 보내면 그대로 분류한다)."""
        from koipa.config import settings

        monkeypatch.setattr(settings, "analyze_sync_max_chunks", 100_000, raising=False)
        j = _upload(client, _GOLD_DOCX).json()
        assert j.get("classification") is not None
