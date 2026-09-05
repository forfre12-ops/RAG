"""R3 — 가이드 업로드 한도 시험은 **없어졌다**(2026-09-05).

가이드 API 가 파일을 받지 않는다. 종전에는 multipart 로 파일을 필수로 받고 max_upload_mb
한도까지 검사한 뒤 **버렸다**(services/guide_service.py 머리말: "받되 버린다").
발주처 원문이 우리 서버 메모리를 한 번 지나는 경로였고 RTM 요건도 아니라 걷었다.

한도 검사 자체는 남아 있다 — 분류 대상 문서 업로드(POST /documents)가 그것이고,
tests/test_document_ingestion.py 가 그 자리를 지킨다. 여기서는 **파일을 안 받는다**는
것만 확인한다.
"""
from __future__ import annotations

import io
import json
import uuid

from fastapi.testclient import TestClient

from koipa.api.app import app

client = TestClient(app)
API = "/api/v1"
_AUTH = {"X-API-Key": "test-key", "X-Actor-Role": "admin"}


def _actor() -> dict:
    return {"user_id": "guide-admin", "role": "admin"}


def test_file_upload_is_no_longer_accepted():
    """파일을 보내면 422 — 원문이 서버로 들어오는 경로를 남기지 않는다."""
    gid = f"g-{uuid.uuid4().hex[:6]}"
    r = client.post(
        f"{API}/guide/documents",
        headers=_AUTH,
        data={"guide_id": gid, "version": "v1.0", "actor": json.dumps(_actor())},
        files={"file": ("g.txt", io.BytesIO(b"x" * 4096), "text/plain")},
    )
    assert r.status_code == 422, r.text


def test_version_register_needs_no_file():
    """버전 메타만으로 등록된다 — 한도 검사가 낄 자리가 없다."""
    gid = f"g-{uuid.uuid4().hex[:6]}"
    r = client.post(
        f"{API}/guide/documents",
        headers=_AUTH,
        json={"guide_id": gid, "version": "v1.0", "actor": _actor()},
    )
    assert r.status_code == 201, r.text
    assert r.json()["guide_id"] == gid
