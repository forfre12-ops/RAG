"""Track B 보안·권한·캐시 하드닝 회귀 테스트 (W2-B).

대상 (배정 파일):
  - api/app.py             — M-schema-perm: PUT /schema/grades admin 전용(GET은 broad)
  - api/rate_limit.py      — M-ratelimit-key: 검증된 신원 우선 키, 미검증 헤더 단독 키 금지
  - schemas/common.py      — M-cache: GradeRegistry/FactorRegistry TTL 재로드(멀티워커)
  - services/guide_service.py — M-guide-tenant: 가이드 이력을 실제 tenant_id로 스코프

DB/ES/네트워크 없이 도는 단위 수준 테스트. (DB가 있으면 더 강한 통합 테스트가 별도로 존재.)
"""

from __future__ import annotations

import asyncio
import time
import types

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# 경량 Fake Request — 의존성/키함수를 직접 호출하기 위한 최소 인터페이스
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self, host: str):
        self.host = host


class _FakeRequest:
    def __init__(self, *, method: str = "GET", headers: dict | None = None,
                 client_host: str = "1.2.3.4", **state_kwargs):
        self.method = method
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.client = _FakeClient(client_host)
        self.state = types.SimpleNamespace(**state_kwargs)


# ===========================================================================
# M-schema-perm — PUT /schema/grades 는 admin 전용, GET 은 broad 역할 허용
# ===========================================================================
def _run_rbac(method: str, auth_context: dict) -> dict:
    from lloydk.api.app import _schema_grades_rbac

    req = _FakeRequest(method=method)
    return asyncio.run(_schema_grades_rbac(request=req, auth_context=auth_context))


def test_schema_get_allows_system_role():
    """GET(읽기)은 공유 api_key 기본역할 'system'으로도 통과."""
    ctx = {"actor_role": "system", "actor_roles": ("system",)}
    assert _run_rbac("GET", ctx) is ctx


@pytest.mark.parametrize("role", ["system", "reviewer", "kl_backend"])
def test_schema_put_blocks_non_admin(role):
    """PUT(전역 파괴적 쓰기)은 admin 외 역할(특히 'system')을 403으로 차단.

    핵심 취약점: 'system'은 공유 api_key 기본역할이라, 과거 broad 데코레이트는
    사실상 무권한으로 전역 등급체계 변경을 허용했다.
    """
    ctx = {"actor_role": role, "actor_roles": (role,)}
    with pytest.raises(HTTPException) as ei:
        _run_rbac("PUT", ctx)
    assert ei.value.status_code == 403


def test_schema_put_allows_admin():
    ctx = {"actor_role": "admin", "actor_roles": ("admin",)}
    assert _run_rbac("PUT", ctx) is ctx


def test_schema_write_methods_fail_closed():
    """GET/HEAD/OPTIONS 외의 메서드(미래 추가 쓰기 포함)는 모두 admin 요구."""
    for method in ("POST", "DELETE", "PATCH"):
        ctx = {"actor_role": "system", "actor_roles": ("system",)}
        with pytest.raises(HTTPException):
            _run_rbac(method, ctx)


def test_schema_put_admin_in_multi_role_passes():
    """복수 역할 중 admin이 하나라도 있으면 PUT 통과."""
    ctx = {"actor_role": "admin", "actor_roles": ("reviewer", "admin")}
    assert _run_rbac("PUT", ctx) is ctx


# ===========================================================================
# M-ratelimit-key — 검증된 신원 우선, 미검증 X-Tenant-Id 헤더 단독 키 금지
# ===========================================================================
def test_key_uses_verified_auth_tenant():
    from lloydk.api.rate_limit import tenant_or_ip_key

    req = _FakeRequest(auth_tenant="acme", auth_actor=None)
    assert tenant_or_ip_key(req) == "tenant:acme"


def test_key_uses_auth_actor_when_no_tenant():
    from lloydk.api.rate_limit import tenant_or_ip_key

    req = _FakeRequest(auth_tenant=None, auth_actor="user-7")
    assert tenant_or_ip_key(req) == "actor:user-7"


def test_key_ignores_unverified_tenant_header():
    """[M-ratelimit-key] 위조 가능한 원시 X-Tenant-Id 헤더는 키에 쓰지 않는다.

    auth_tenant/auth_actor가 비면(미검증) IP 폴백으로만 키가 결정돼야 한다 —
    헤더값 'forged-tenant'가 키에 절대 반영되면 안 됨.
    """
    from lloydk.api.rate_limit import tenant_or_ip_key

    req = _FakeRequest(
        headers={"X-Tenant-Id": "forged-tenant"},
        client_host="9.9.9.9",
        auth_tenant=None,
        auth_actor=None,
    )
    key = tenant_or_ip_key(req)
    assert key == "ip:9.9.9.9"
    assert "forged-tenant" not in key


def test_key_distinct_verified_tenants_get_distinct_buckets():
    """검증된 서로 다른 tenant는 독립 키(=독립 버킷)를 받는다 — 정상 격리는 유지."""
    from lloydk.api.rate_limit import tenant_or_ip_key

    a = tenant_or_ip_key(_FakeRequest(auth_tenant="t-a", auth_actor=None))
    b = tenant_or_ip_key(_FakeRequest(auth_tenant="t-b", auth_actor=None))
    assert a != b


# ===========================================================================
# M-cache — GradeRegistry/FactorRegistry TTL 재로드 (멀티워커 stale 완화)
# ===========================================================================
def test_grade_registry_reloads_after_ttl(monkeypatch):
    """TTL 만료 후 다음 조회는 캐시를 버리고 재계산한다(다른 워커도 새 등급 반영)."""
    from lloydk.schemas import common as common_mod
    from lloydk.schemas.common import GradeRegistry

    GradeRegistry.invalidate()
    # TTL 짧게 — 매우 작은 값으로 만료 강제
    monkeypatch.setattr(common_mod, "_registry_ttl_sec", lambda: 0.01)

    first = GradeRegistry.get_codes()  # DB 미가용 → enum 폴백, 캐시 채움
    assert first == ["TS", "S1", "S2", "S3"]
    assert GradeRegistry._cache is not None

    # TTL 내 — 같은 캐시 객체 freshness 유지
    assert GradeRegistry._cache_fresh() is True

    time.sleep(0.02)  # TTL 경과
    assert GradeRegistry._cache_fresh() is False  # 만료 → 다음 조회 시 재로드
    # 재조회는 정상 동작(폴백 동일)하고 타임스탬프가 갱신됨
    before_ts = GradeRegistry._cache_ts
    again = GradeRegistry.get_codes()
    assert again == first
    assert GradeRegistry._cache_ts >= before_ts
    GradeRegistry.invalidate()


def test_ttl_zero_disables_expiry(monkeypatch):
    """TTL<=0이면 명시 invalidate에만 의존(과거 동작)."""
    from lloydk.schemas import common as common_mod
    from lloydk.schemas.common import GradeRegistry

    GradeRegistry.invalidate()
    monkeypatch.setattr(common_mod, "_registry_ttl_sec", lambda: 0.0)
    GradeRegistry.get_codes()
    # 인위적으로 ts를 과거로 밀어도 TTL 비활성이라 fresh
    GradeRegistry._cache_ts = time.monotonic() - 10_000
    assert GradeRegistry._cache_fresh() is True
    GradeRegistry.invalidate()


def test_factor_registry_invalidate_resets_ts(monkeypatch):
    from lloydk.schemas import common as common_mod
    from lloydk.schemas.common import FactorRegistry

    monkeypatch.setattr(common_mod, "_registry_ttl_sec", lambda: 30.0)
    FactorRegistry.invalidate()
    FactorRegistry.get_field_map()  # 캐시 채움
    assert FactorRegistry._cache is not None
    assert FactorRegistry._cache_ts > 0.0
    FactorRegistry.invalidate()
    assert FactorRegistry._cache is None
    assert FactorRegistry._cache_ts == 0.0


def test_registry_ttl_env_parsing(monkeypatch):
    from lloydk.schemas.common import _registry_ttl_sec

    monkeypatch.setenv("LLOYDK_REGISTRY_CACHE_TTL_SEC", "5")
    assert _registry_ttl_sec() == 5.0
    monkeypatch.setenv("LLOYDK_REGISTRY_CACHE_TTL_SEC", "not-a-number")
    assert _registry_ttl_sec() == 30.0  # 파싱 실패 → 기본값
    monkeypatch.delenv("LLOYDK_REGISTRY_CACHE_TTL_SEC", raising=False)
    assert _registry_ttl_sec() == 30.0


# ===========================================================================
# M-guide-tenant — 가이드 이력을 실제 tenant_id로 스코프 (DB 미가용 in-memory 경로)
# ===========================================================================
class _StubIndexResult:
    def __init__(self):
        self.indexed = True
        self.index_name = "guides_v1"
        self.alias = "guides"
        self.chunk_count = 1
        self.vector_count = 3
        self.model = "stub"
        self.warnings: list[str] = []


class _StubIndexer:
    """index_guide 호출 시 tenant_id를 캡처하는 스텁 — ES 미사용."""

    def __init__(self):
        self.seen_tenants: list[str] = []

    def index_guide(self, *, guide_id, version, tenant_id, text,
                    doc_type=None, effective_date=None, **kw):
        self.seen_tenants.append(tenant_id)
        return _StubIndexResult()


def _svc_with_stub():
    from lloydk.services.guide_service import GuideService

    return GuideService(indexer=_StubIndexer())


def test_persist_uses_real_tenant_not_hardcoded_default():
    """[M-guide-tenant] 업로드가 받은 실제 tenant_id가 인덱서·레코드까지 전달된다.

    과거엔 _persist_guide가 "default"로 하드코딩해 모든 테넌트 이력이 한 행으로 혼합됐다.
    """
    svc = _svc_with_stub()
    svc.upload(
        guide_id="g1", version="v1", effective_date=None, change_summary=None,
        content_bytes=b"hello guide", actor_user_id="u1", tenant_id="tenant-acme",
        filename="g1.txt",
    )
    # 인덱서에 실제 tenant 전달
    assert svc._indexer.seen_tenants == ["tenant-acme"]
    # in-memory 레코드가 실제 tenant로 키됨 (default 아님)
    assert ("tenant-acme", "g1") in svc._guides
    assert ("default", "g1") not in svc._guides
    rec = svc._guides[("tenant-acme", "g1")][0]
    assert rec.tenant_id == "tenant-acme"


def test_list_versions_is_tenant_scoped_in_memory():
    """다른 테넌트가 같은 guide_id를 올려도 이력이 섞이지 않는다(테넌트별 분리 조회)."""
    svc = _svc_with_stub()
    svc.upload(guide_id="shared", version="vA", effective_date=None, change_summary="A",
               content_bytes=b"a", actor_user_id="u", tenant_id="t1", filename="s.txt")
    svc.upload(guide_id="shared", version="vB", effective_date=None, change_summary="B",
               content_bytes=b"b", actor_user_id="u", tenant_id="t2", filename="s.txt")

    res_t1 = svc.list_versions("shared", tenant_id="t1")
    res_t2 = svc.list_versions("shared", tenant_id="t2")
    assert res_t1 is not None and res_t2 is not None
    v_t1 = [v.version for v in res_t1.versions]
    v_t2 = [v.version for v in res_t2.versions]
    assert v_t1 == ["vA"]
    assert v_t2 == ["vB"]
    # 누수 없음: t1 조회에 t2의 vB가 보이면 안 됨
    assert "vB" not in v_t1
    assert "vA" not in v_t2


def test_list_versions_unknown_tenant_returns_none():
    svc = _svc_with_stub()
    svc.upload(guide_id="g", version="v", effective_date=None, change_summary=None,
               content_bytes=b"x", actor_user_id="u", tenant_id="real", filename="g.txt")
    # 존재하지 않는 테넌트로 조회하면 None (다른 테넌트 데이터 노출 금지)
    assert svc.list_versions("g", tenant_id="ghost") is None


def test_guide_record_default_tenant_backward_compat():
    """_GuideRecord.tenant_id 기본값은 'default' — 기존 호출 호환."""
    from lloydk.services.guide_service import _GuideRecord

    rec = _GuideRecord(
        guide_id="g", version="v", effective_date=None, change_summary=None,
        registered_at="now", indexed=False, embedding_vector_count=0,
    )
    assert rec.tenant_id == "default"
