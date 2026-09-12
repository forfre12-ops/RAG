"""[#14a] 골든 검수/서명 HTML 서명 URL 토큰 — 무인증 full-text 노출 차단.

비밀키(golden_html_url_secret 우선, 없으면 api_key) 설정 시 review.html/signoff.html 이 job_id
바인딩 HMAC 토큰(?t=)을 요구하고, 인증된 상태조회(GET /golden/jobs/{id})가 서명 URL 을 발급한다.
비밀키 미설정이면 미강제 — 기존 브라우저 네비게이션·dev/test 무변경(backward-compat).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from koipa.api.app import app
from koipa.api.golden import _mint_html_token, _signed_html_urls, _verify_html_token
from koipa.config import settings
from koipa.golden_builder import LabelPair
from koipa.schemas.common import Actor
from koipa.schemas.golden import GoldenBuildRequest
from koipa.services.golden_build_service import GoldenBuildService

client = TestClient(app)
API = "/api/v1"
_ACTOR = Actor(user_id="builder1", role="admin")


def _make_job(tmp_path) -> str:
    req = GoldenBuildRequest(
        source_type="inline",
        docs=[{"doc_id": "a", "text": "s2문서 내용", "source": "판례"}],
        out_dir=str(tmp_path), actor=_ACTOR,
    )
    resp = GoldenBuildService().submit(
        req, label_fn=lambda _t: LabelPair("S2", 0.8, "S2", 0.9, has_real_evidence=True)
    )
    return str(resp.golden_job_id)


def test_token_unenforced_when_no_secret(monkeypatch):
    monkeypatch.setattr(settings, "golden_html_url_secret", "")
    monkeypatch.setattr(settings, "api_key", "")
    assert _mint_html_token("job-1") is None
    assert _verify_html_token("job-1", None) is True          # 미강제(부재 통과)
    r, s = _signed_html_urls("job-1")
    assert "?t=" not in r and "?t=" not in s                  # 평문 경로


def test_token_mint_verify_when_secret_set(monkeypatch):
    monkeypatch.setattr(settings, "golden_html_url_secret", "s3cr3t")
    tok = _mint_html_token("job-1")
    assert tok and len(tok) == 24
    assert _verify_html_token("job-1", tok) is True
    assert _verify_html_token("job-1", None) is False         # 부재 거부
    assert _verify_html_token("job-1", "deadbeefdeadbeefdeadbeef") is False  # 위조 거부
    assert _mint_html_token("job-2") != tok                   # job 바인딩(다른 job=다른 토큰)


def test_review_and_signoff_html_403_without_token_ok_with(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "golden_html_url_secret", "s3cr3t")
    job_id = _make_job(tmp_path)
    # 무토큰 → 403 (무인증 full-text 노출 차단)
    assert client.get(f"{API}/golden/jobs/{job_id}/review.html").status_code == 403
    assert client.get(f"{API}/golden/jobs/{job_id}/signoff.html").status_code == 403
    # 위조 토큰 → 403
    assert client.get(f"{API}/golden/jobs/{job_id}/review.html?t=deadbeef").status_code == 403
    # 유효 토큰 → 통과(403 아님; 200/202/404 렌더상태 무관)
    tok = _mint_html_token(job_id)
    assert client.get(f"{API}/golden/jobs/{job_id}/review.html?t={tok}").status_code != 403
    assert client.get(f"{API}/golden/jobs/{job_id}/signoff.html?t={tok}").status_code != 403


def test_review_html_open_when_no_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "golden_html_url_secret", "")
    monkeypatch.setattr(settings, "api_key", "")
    job_id = _make_job(tmp_path)
    # 비밀키 미설정 → 토큰 없이도 열림(기존 브라우저 네비게이션 보존)
    assert client.get(f"{API}/golden/jobs/{job_id}/review.html").status_code != 403


def test_status_endpoint_issues_signed_urls(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "golden_html_url_secret", "s3cr3t")
    job_id = _make_job(tmp_path)
    r = client.get(
        f"{API}/golden/jobs/{job_id}",
        headers={"X-API-Key": settings.api_key, "X-Actor-Role": "admin"},
    )
    assert r.status_code == 200
    body = r.json()
    tok = _mint_html_token(job_id)
    assert body.get("review_url", "").endswith(f"review.html?t={tok}")
    assert body.get("signoff_url", "").endswith(f"signoff.html?t={tok}")
