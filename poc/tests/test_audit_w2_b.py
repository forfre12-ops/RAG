"""Track B 보안·권한·캐시 하드닝 회귀 테스트 (W2-B).

대상 (배정 파일):
  - api/app.py             — M-schema-perm: PUT /schema/grades admin 전용(GET은 broad)
  - api/rate_limit.py      — M-ratelimit-key: 검증된 신원(KL cred/actor) 우선 키, 미검증 헤더 단독 키 금지
  - schemas/common.py      — M-cache: GradeRegistry/FactorRegistry TTL 재로드(멀티워커)
  - services/guide_service.py — 가이드 이력 in-memory/DB 영속

tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진). per-tenant 스코프 검증 케이스는
기능 제거로 삭제·전역(global)으로 단순화됨.

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
# M-ratelimit-key — 검증된 KL cred(actor) 우선, 미검증 헤더 단독 키 금지
# (tenant 제거: 단일 KL 인증이라 버킷은 actor/IP 기준; 격리는 KL 포털 전담)
# ===========================================================================
def test_key_uses_auth_actor():
    from lloydk.api.rate_limit import cred_or_ip_key

    req = _FakeRequest(auth_actor="user-7")
    assert cred_or_ip_key(req) == "actor:user-7"


def test_key_ignores_unverified_header():
    """[M-ratelimit-key] 위조 가능한 원시 헤더는 키에 쓰지 않는다.

    auth_actor가 비면(미검증) IP 폴백으로만 키가 결정돼야 한다 —
    헤더값 'forged'가 키에 절대 반영되면 안 됨.
    """
    from lloydk.api.rate_limit import cred_or_ip_key

    req = _FakeRequest(
        headers={"X-Tenant-Id": "forged"},
        client_host="9.9.9.9",
        auth_actor=None,
    )
    key = cred_or_ip_key(req)
    assert key == "ip:9.9.9.9"
    assert "forged" not in key


def test_key_distinct_verified_actors_get_distinct_buckets():
    """검증된 서로 다른 actor는 독립 키(=독립 버킷)를 받는다."""
    from lloydk.api.rate_limit import cred_or_ip_key

    a = cred_or_ip_key(_FakeRequest(auth_actor="a-1"))
    b = cred_or_ip_key(_FakeRequest(auth_actor="a-2"))
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
# Guide 이력 — 전역(global) 네임스페이스 (DB 미가용 in-memory 경로)
# tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진). guide_id 단독으로 키.
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
    """index_guide 호출을 캡처하는 스텁 — ES 미사용."""

    def __init__(self):
        self.seen_guides: list[str] = []

    def index_guide(self, *, guide_id, version, text,
                    doc_type=None, effective_date=None, **kw):
        self.seen_guides.append(guide_id)
        return _StubIndexResult()


def _svc_with_stub():
    from lloydk.services.guide_service import GuideService

    return GuideService(indexer=_StubIndexer())


def test_persist_records_guide_in_memory():
    """업로드가 인덱서·in-memory 레코드까지 guide_id로 전달된다(전역 네임스페이스)."""
    svc = _svc_with_stub()
    svc.upload(
        guide_id="g1", version="v1", effective_date=None, change_summary=None,
        content_bytes=b"hello guide", actor_user_id="u1",
        filename="g1.txt",
    )
    # 인덱서에 guide_id 전달
    assert svc._indexer.seen_guides == ["g1"]
    # in-memory 레코드가 guide_id로 키됨
    assert "g1" in svc._guides
    rec = svc._guides["g1"][0]
    assert rec.guide_id == "g1"
    assert rec.version == "v1"


def test_list_versions_accumulates_history_in_memory():
    """같은 guide_id의 여러 버전이 이력으로 누적된다(전역 조회)."""
    svc = _svc_with_stub()
    svc.upload(guide_id="shared", version="vA", effective_date=None, change_summary="A",
               content_bytes=b"a", actor_user_id="u", filename="s.txt")
    svc.upload(guide_id="shared", version="vB", effective_date=None, change_summary="B",
               content_bytes=b"b", actor_user_id="u", filename="s.txt")

    res = svc.list_versions("shared")
    assert res is not None
    versions = [v.version for v in res.versions]
    assert versions == ["vA", "vB"]


def test_list_versions_unknown_guide_returns_none():
    svc = _svc_with_stub()
    svc.upload(guide_id="g", version="v", effective_date=None, change_summary=None,
               content_bytes=b"x", actor_user_id="u", filename="g.txt")
    # 존재하지 않는 guide_id 조회하면 None
    assert svc.list_versions("ghost") is None


def test_guide_record_fields():
    """_GuideRecord 기본 생성 — tenant 필드 없음(전역 네임스페이스)."""
    from lloydk.services.guide_service import _GuideRecord

    rec = _GuideRecord(
        guide_id="g", version="v", effective_date=None, change_summary=None,
        registered_at="now", indexed=False, embedding_vector_count=0,
    )
    assert rec.guide_id == "g"
    assert not hasattr(rec, "tenant_id")
