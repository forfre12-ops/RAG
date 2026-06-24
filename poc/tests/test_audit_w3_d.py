"""Track D (Wave3) 회귀 — 보안 강화.

대상 수정 (배정 파일):
  [M-audit-hmac]    services/audit_chain.py — 키 없는 sha256 체인을 서버 비밀키 HMAC으로
                    강화해 과거 row 재작성(rewrite) 차단. 비밀키 미설정 dev는 sha256 유지(비파괴).
  [L-secrets-ttl]   services/secrets_manager.py — Vault/AWS 캐시에 TTL/invalidate 추가
                    (무기한 캐싱→키 로테이션 무력 차단). 빈 dict vs 미로드를 None 센티넬로 구분.
  [L-jwt-aud]       api/_jwt_auth.py — 운영 jwt 모드에서 jwt_issuer/jwt_audience 미설정 시
                    confused-deputy fail-fast. dev/test 비파괴.
  [L-audit-noalarm] api/middleware.py — 감사 insert 실패를 prom 카운터로 노출 +
                    고위험 액션 fail-closed 옵션(settings/env 플래그, 기본 off).
  [M-health-storage] api/health.py — _check_storage가 build_storage() 결과 기준으로 점검,
                    MinIO→Local 폴백 시 degraded 노출.

DB·실제 인프라는 돌리지 않는다 — 순수함수·fake·monkeypatch로 로직만 검증.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# ===========================================================================
# [M-audit-hmac] HMAC 체인 — 키 없는 재작성 차단
# ===========================================================================


def test_hmac_changes_digest_vs_plain_sha256(monkeypatch):
    """비밀키 설정 시 링크 다이제스트가 키 없는 sha256과 달라야 한다(키 결속 증명)."""
    import lloydk.services.audit_chain as ac

    payload = '{"x":1}'
    prev = "0" * 64

    # 키 없음 → 평문 sha256 경로
    monkeypatch.delenv("LLOYDK_AUDIT_CHAIN_SECRET", raising=False)
    with patch.object(ac, "_chain_secret", return_value=""):
        packed_plain = ac.build_chained_hash(payload, prev)
    _, full_plain = ac.parse_chained_hash(packed_plain)

    # 키 있음 → HMAC 경로
    with patch.object(ac, "_chain_secret", return_value="super-secret-key"):
        packed_hmac = ac.build_chained_hash(payload, prev)
    _, full_hmac = ac.parse_chained_hash(packed_hmac)

    assert full_plain != full_hmac, "HMAC 링크는 키 없는 sha256과 달라야 함"
    # prev16 패킹은 동일(연결성 형식 유지)
    assert packed_plain.split(":", 1)[0] == packed_hmac.split(":", 1)[0]


def test_hmac_secret_required_to_reproduce_link():
    """비밀키를 모르면 동일 링크를 재생성할 수 없다 = 재작성 차단의 핵심."""
    import lloydk.services.audit_chain as ac

    payload = '{"action":"relabel","grade":"TS"}'
    prev = "ab" * 32

    with patch.object(ac, "_chain_secret", return_value="real-secret"):
        legit = ac.build_chained_hash(payload, prev)

    # 공격자가 키를 모르고(빈 키) 같은 payload/prev로 재서명 시도 → 다이제스트 불일치
    with patch.object(ac, "_chain_secret", return_value="wrong-secret"):
        forged = ac.build_chained_hash(payload, prev)
    with patch.object(ac, "_chain_secret", return_value=""):
        forged_plain = ac.build_chained_hash(payload, prev)

    assert legit != forged
    assert legit != forged_plain


def test_no_secret_falls_back_to_legacy_sha256_nondestructive(monkeypatch):
    """비밀키 미설정 dev: 기존 sha256 동작 유지 — prev16:full32 형식·길이 보존."""
    import lloydk.services.audit_chain as ac

    monkeypatch.delenv("LLOYDK_AUDIT_CHAIN_SECRET", raising=False)
    with patch.object(ac, "_chain_secret", return_value=""):
        packed = ac.build_chained_hash('{"x":1}', "0" * 64)
        # 동일 입력 결정론 — 레거시 평문 sha256과 정확히 일치
        expected = ac.sha256_hex("0" * 64 + "|" + '{"x":1}')[:32]
    prev16, full32 = ac.parse_chained_hash(packed)
    assert full32 == expected
    assert len(full32) == 32
    assert prev16 == ("0" * 64)[:16]


def test_env_secret_is_picked_up(monkeypatch):
    """settings에 필드가 없어도 env LLOYDK_AUDIT_CHAIN_SECRET로 키 주입 가능."""
    import lloydk.services.audit_chain as ac

    monkeypatch.setenv("LLOYDK_AUDIT_CHAIN_SECRET", "env-chain-secret")
    # settings.audit_chain_secret는 미정의 → getattr 폴백 → env 사용
    assert ac._chain_secret() == "env-chain-secret"


# ===========================================================================
# [L-secrets-ttl] Vault/AWS 캐시 TTL + None 센티넬 + invalidate
# ===========================================================================


class _FakeVaultClient:
    def __init__(self, data):
        self._data = data
        self.read_calls = 0

    def is_authenticated(self):
        return True

    class _KvV2:
        def __init__(self, outer):
            self._outer = outer

        def read_secret_version(self, path, mount_point):
            self._outer.read_calls += 1
            return {"data": {"data": dict(self._outer._data)}}

    @property
    def secrets(self):
        kv = type("KV", (), {})()
        kv.v2 = _FakeVaultClient._KvV2(self)
        return type("S", (), {"kv": kv})()


def _make_vault(data, ttl):
    from lloydk.services.secrets_manager import VaultSecretsManager

    sm = VaultSecretsManager(url="http://x", token="t", cache_ttl=ttl)
    fake = _FakeVaultClient(data)
    sm._client = fake  # _ensure 우회 (이미 인증된 클라이언트 주입)
    return sm, fake


def test_vault_caches_within_ttl():
    """TTL 내 반복 조회는 백엔드를 1회만 읽는다(캐시 적중)."""
    sm, fake = _make_vault({"api_key": "v1"}, ttl=300.0)
    assert sm.get("api_key") == "v1"
    assert sm.get("api_key") == "v1"
    assert fake.read_calls == 1


def test_vault_refreshes_after_ttl():
    """TTL 만료 후에는 백엔드를 다시 읽어 로테이션된 값을 반영."""
    import lloydk.services.secrets_manager as sm_mod

    sm, fake = _make_vault({"api_key": "v1"}, ttl=300.0)
    base = [1000.0]
    with patch.object(sm_mod.time, "monotonic", side_effect=lambda: base[0]):
        assert sm.get("api_key") == "v1"
        assert fake.read_calls == 1
        # 값 로테이션 + 시간 경과(>TTL)
        fake._data["api_key"] = "v2"
        base[0] = 1000.0 + 301.0
        assert sm.get("api_key") == "v2"
    assert fake.read_calls == 2


def test_vault_invalidate_forces_reload():
    """invalidate() 후 다음 조회는 무조건 재로드 (로테이션 즉시 반영)."""
    sm, fake = _make_vault({"api_key": "v1"}, ttl=300.0)
    assert sm.get("api_key") == "v1"
    assert fake.read_calls == 1
    fake._data["api_key"] = "v2"
    sm.invalidate()
    assert sm.get("api_key") == "v2"
    assert fake.read_calls == 2


def test_vault_empty_secret_not_refetched_every_call():
    """빈 결과(로드했으나 secret 없음)는 None 센티넬과 구분돼 매번 재조회되지 않는다.

    기존 `if self._cache:` 버그는 빈 dict를 '미로드'로 오인해 매 호출 재조회했다.
    """
    sm, fake = _make_vault({}, ttl=300.0)  # 빈 secret
    assert sm.get("missing", "dflt") == "dflt"
    assert sm.get("missing", "dflt") == "dflt"
    # 빈 결과도 1회만 로드 — 캐시됨
    assert fake.read_calls == 1


def test_vault_ttl_zero_disables_cache():
    """TTL<=0이면 캐시 비활성 — 매번 재조회(명시적 opt-out)."""
    sm, fake = _make_vault({"api_key": "v1"}, ttl=0.0)
    sm.get("api_key")
    sm.get("api_key")
    assert fake.read_calls == 2


def test_invalidate_secrets_cache_global_noop_on_env(monkeypatch):
    """env 백엔드는 invalidate가 no-op(예외 없이 통과)."""
    import lloydk.services.secrets_manager as sm_mod

    monkeypatch.delenv("LLOYDK_SECRETS_BACKEND", raising=False)
    sm_mod._default = None
    sm_mod.get_secrets_manager()  # EnvSecretsManager
    sm_mod.invalidate_secrets_cache()  # 예외 없어야 함
    sm_mod._default = None  # 정리


# ===========================================================================
# [L-jwt-aud] 운영 jwt 모드 iss/aud fail-fast (confused deputy)
# ===========================================================================


def test_assert_production_auth_config_requires_iss_aud_in_jwt_mode():
    import lloydk.api._jwt_auth as ja

    with patch.object(ja, "_is_production", return_value=True), \
         patch.object(ja.settings, "auth_mode", "jwt", create=True), \
         patch.object(ja.settings, "jwt_issuer", "", create=True), \
         patch.object(ja.settings, "jwt_audience", "", create=True):
        with pytest.raises(RuntimeError) as ei:
            ja.assert_production_auth_config()
    msg = str(ei.value)
    assert "JWT_ISSUER" in msg and "JWT_AUDIENCE" in msg


def test_assert_production_auth_config_passes_when_iss_aud_set():
    import lloydk.api._jwt_auth as ja

    with patch.object(ja, "_is_production", return_value=True), \
         patch.object(ja.settings, "auth_mode", "jwt", create=True), \
         patch.object(ja.settings, "jwt_issuer", "https://idp.example.com", create=True), \
         patch.object(ja.settings, "jwt_audience", "lloydk-api", create=True):
        ja.assert_production_auth_config()  # 예외 없어야 함


def test_assert_production_auth_config_noop_in_dev():
    """dev/test(_is_production=False)는 미설정이어도 통과(비파괴)."""
    import lloydk.api._jwt_auth as ja

    with patch.object(ja, "_is_production", return_value=False), \
         patch.object(ja.settings, "auth_mode", "jwt", create=True), \
         patch.object(ja.settings, "jwt_issuer", "", create=True), \
         patch.object(ja.settings, "jwt_audience", "", create=True):
        ja.assert_production_auth_config()  # 비파괴


def test_verify_jwt_rejects_missing_iss_aud_in_production():
    """운영에서 iss/aud 미설정이면 verify_jwt가 토큰을 거부(런타임 confused-deputy 차단)."""
    import lloydk.api._jwt_auth as ja

    # 서명 검증 전에 iss/aud 게이트에 도달하도록 _verify_signature를 no-op로.
    import base64
    import json as _json
    import time as _time

    def _b64(d):
        return base64.urlsafe_b64encode(_json.dumps(d).encode()).rstrip(b"=").decode()

    header = _b64({"alg": "RS256", "kid": "default"})
    payload = _b64({"sub": "u1", "exp": int(_time.time()) + 3600})
    token = f"{header}.{payload}.{_b64({'sig': 'x'})}"

    with patch.object(ja, "_is_production", return_value=True), \
         patch.object(ja, "_load_jwks", return_value={"keys": [{"kid": "default", "pem": "x"}]}), \
         patch.object(ja, "_verify_signature", return_value=None), \
         patch.object(ja.settings, "jwt_issuer", "", create=True), \
         patch.object(ja.settings, "jwt_audience", "", create=True):
        with pytest.raises(ja.JWTError):
            ja.verify_jwt(token)


def test_verify_jwt_dev_skips_iss_aud_nondestructive():
    """dev: iss/aud 미설정이면 기존처럼 검증 skip — 정상 토큰 통과(비파괴)."""
    import base64
    import json as _json
    import time as _time

    import lloydk.api._jwt_auth as ja

    def _b64(d):
        return base64.urlsafe_b64encode(_json.dumps(d).encode()).rstrip(b"=").decode()

    header = _b64({"alg": "RS256", "kid": "default"})
    payload = _b64({"sub": "u1", "exp": int(_time.time()) + 3600})
    token = f"{header}.{payload}.{_b64({'sig': 'x'})}"

    with patch.object(ja, "_is_production", return_value=False), \
         patch.object(ja, "_load_jwks", return_value={"keys": [{"kid": "default", "pem": "x"}]}), \
         patch.object(ja, "_verify_signature", return_value=None), \
         patch.object(ja.settings, "jwt_issuer", "", create=True), \
         patch.object(ja.settings, "jwt_audience", "", create=True):
        claims = ja.verify_jwt(token)
    assert claims.sub == "u1"


def _make_request(headers: dict, path: str = "/api/v1/classify"):
    """경량 fake Request — require_auth/_record가 보는 state/headers/url/client만 채움."""
    from starlette.datastructures import Headers

    class _State:
        pass

    class _URL:
        def __init__(self, p):
            self.path = p

    class _Req:
        def __init__(self):
            self.state = _State()
            self.headers = Headers(headers)
            self.url = _URL(path)
            self.client = None  # _record: request.client.host 분기에서 None 처리

    return _Req()


# ===========================================================================
# [L-audit-noalarm] 감사 실패 카운터 + 고위험 fail-closed 옵션
# ===========================================================================


def test_record_returns_false_and_increments_counter_on_db_error():
    """DB 오류로 audit insert 실패 시 _record가 False 반환 + 카운터 증가."""
    from sqlalchemy.exc import OperationalError

    import lloydk.api.middleware as mw

    req = _make_request({})

    # session_scope() 컨텍스트 진입 시 OperationalError — DB 일시장애 모사.
    def _boom(*a, **k):
        raise OperationalError("stmt", {}, Exception("db down"))

    with patch("lloydk.db.session_scope", _boom), \
         patch.object(mw, "_inc_audit_failure") as inc, \
         patch.dict(os.environ, {"AUDIT_DISABLED": "0"}):
        ok = mw.AuditMiddleware._record(
            request=req, payload_hash="h", success=True, error_code=None
        )
    assert ok is False
    inc.assert_called_once_with("db_error")


def test_record_returns_true_when_audit_disabled():
    """AUDIT_DISABLED=1은 의도적 비활성 — 실패가 아니라 True(fail-closed 미발동)."""
    import lloydk.api.middleware as mw

    req = _make_request({})
    with patch.dict(os.environ, {"AUDIT_DISABLED": "1"}):
        assert mw.AuditMiddleware._record(
            request=req, payload_hash="h", success=True, error_code=None
        ) is True


def test_fail_closed_disabled_by_default():
    import lloydk.api.middleware as mw

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LLOYDK_AUDIT_FAIL_CLOSED_HIGH_RISK", None)
        # settings 필드 미정의 → getattr 폴백 False, env 미설정 → False
        assert mw._fail_closed_enabled() is False


def test_fail_closed_enabled_via_env():
    import lloydk.api.middleware as mw

    with patch.dict(os.environ, {"LLOYDK_AUDIT_FAIL_CLOSED_HIGH_RISK": "1"}):
        assert mw._fail_closed_enabled() is True


def test_high_risk_action_classification():
    import lloydk.api.middleware as mw

    assert mw._is_high_risk(_make_request_for_path("/api/v1/relabel")) is True
    assert mw._is_high_risk(_make_request_for_path("/api/v1/train")) is True
    # 조회성 classify는 고위험 아님 (가용성 우선)
    assert mw._is_high_risk(_make_request_for_path("/api/v1/classify")) is False


def _make_request_for_path(path: str):
    class _URL:
        pass

    u = _URL()
    u.path = path

    class _Req:
        url = u

    return _Req()


def test_fail_closed_high_risk_blocks_with_503_via_dispatch():
    """고위험 액션 + audit 실패 + fail-closed on → dispatch가 503 반환(무감사 성공 차단)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import lloydk.api.middleware as mw

    app = FastAPI()
    app.add_middleware(mw.AuditMiddleware)

    @app.post("/api/v1/relabel")
    def _relabel():
        return {"ok": True}

    # audit 항상 실패(False), fail-closed on. payload_hash 단계가 DB를 건드리지 않도록
    # _try_build_chained_hash도 폴백값으로 스텁.
    with patch.object(mw.AuditMiddleware, "_record", return_value=False), \
         patch.object(mw, "_try_build_chained_hash", return_value=None), \
         patch.object(mw, "_fail_closed_enabled", return_value=True), \
         patch.dict(os.environ, {"AUDIT_DISABLED": "0"}):
        with TestClient(app) as cli:
            r = cli.post("/api/v1/relabel", json={})
    assert r.status_code == 503
    assert "fail-closed" in r.text


def test_fail_closed_off_keeps_serving_on_audit_failure():
    """기본(off): audit 실패해도 고위험 액션 응답은 정상 통과(비파괴)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import lloydk.api.middleware as mw

    app = FastAPI()
    app.add_middleware(mw.AuditMiddleware)

    @app.post("/api/v1/relabel")
    def _relabel():
        return {"ok": True}

    with patch.object(mw.AuditMiddleware, "_record", return_value=False), \
         patch.object(mw, "_try_build_chained_hash", return_value=None), \
         patch.object(mw, "_fail_closed_enabled", return_value=False), \
         patch.dict(os.environ, {"AUDIT_DISABLED": "0"}):
        with TestClient(app) as cli:
            r = cli.post("/api/v1/relabel", json={})
    assert r.status_code == 200


# ===========================================================================
# [M-health-storage] build_storage 기준 점검 + MinIO→Local 폴백 degraded
# ===========================================================================


def test_health_storage_local_backend_skipped():
    """설정상 local 백엔드는 의도된 구성 → skipped(ok)."""
    import lloydk.api.health as h

    with patch.object(h.settings, "storage_backend", "local", create=True):
        res = h._check_storage()
    assert res["ok"] is True
    assert res["status"] == "skipped"


def test_health_storage_minio_to_local_fallback_is_degraded():
    """backend=minio인데 build_storage()가 LocalStorage(_client 없음)로 폴백 → degraded(ok=False).

    기존 버그: 이 경우 _client 부재로 점검을 건너뛰고 ok로 통과했다.
    """
    import lloydk.api.health as h

    class _FellBackLocal:
        name = "local"  # _client 속성 없음

    with patch.object(h.settings, "storage_backend", "minio", create=True), \
         patch("lloydk.adapters.storage.build_storage", return_value=_FellBackLocal()):
        res = h._check_storage()
    assert res["ok"] is False
    assert res["status"] == "degraded"
    assert res["resolved"] == "local"


def test_health_storage_minio_ok_when_client_connects():
    """원격 클라이언트가 list_buckets 응답 → ok."""
    import lloydk.api.health as h

    class _Client:
        def list_buckets(self):
            return []

    class _Remote:
        name = "minio"
        _client = _Client()

    with patch.object(h.settings, "storage_backend", "minio", create=True), \
         patch("lloydk.adapters.storage.build_storage", return_value=_Remote()):
        res = h._check_storage()
    assert res["ok"] is True
    assert res["status"] == "ok"


def test_health_storage_minio_error_when_client_raises():
    """원격 클라이언트 연결 실패(list_buckets raise) → error(ok=False)."""
    import lloydk.api.health as h

    class _Client:
        def list_buckets(self):
            raise ConnectionError("minio down")

    class _Remote:
        name = "minio"
        _client = _Client()

    with patch.object(h.settings, "storage_backend", "minio", create=True), \
         patch("lloydk.adapters.storage.build_storage", return_value=_Remote()):
        res = h._check_storage()
    assert res["ok"] is False
    assert res["status"] == "error"
