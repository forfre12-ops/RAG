"""AuditRepo.record 와 해시·INET 정규화 회귀 — 종전 테스트 0건이던 구간.

기존 test_audit_middleware.py 는 미들웨어가 무엇을 기록하는지를 보고(그나마 Postgres 필요라
로컬에서는 skip), 저장소 자체의 정규화 로직은 아무도 안 봤다.

여기서 가장 무거운 것은 **payload_hash 의 안정성**이다. 감사 체인 무결성 경보
(AuditChainBroken · AuditChainNilHash)가 이 값 위에 서 있어서, 같은 payload 가 호출마다 다른
해시를 내면 체인이 깨진 것처럼 보이고 반대로 조용히 같아지면 변조를 놓친다.

DB 를 요구하지 않는다 — record() 는 session.add 만 부르므로 가짜 세션으로 충분하다.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from lloydk.repositories.audit_repo import AuditRepo


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)


@pytest.fixture
def repo():
    session = FakeSession()
    return AuditRepo(session), session


# ── payload 해시 안정성 (감사 체인의 근간) ────────────────────────────────────

def test_hash_is_stable_across_dict_key_order():
    """같은 내용이면 키 순서가 달라도 같은 해시여야 한다 — sort_keys 계약."""
    a = AuditRepo._hash_payload({"b": 2, "a": 1})
    b = AuditRepo._hash_payload({"a": 1, "b": 2})
    assert a == b


def test_hash_changes_when_content_changes():
    """내용이 바뀌면 해시도 바뀌어야 한다 — 안 그러면 변조를 놓친다."""
    assert AuditRepo._hash_payload({"a": 1}) != AuditRepo._hash_payload({"a": 2})


def test_hash_matches_documented_algorithm():
    """SHA-256(정렬 JSON, UTF-8) — 다른 도구로 재현 가능해야 감사에 쓸 수 있다."""
    payload = {"grade": "TS", "doc": "문서"}
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    assert AuditRepo._hash_payload(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"a": 1},
        ["a", "b"],
        "문자열",
        b"raw-bytes",
        123,
        {"nested": {"deep": [1, {"x": None}]}},
    ],
)
def test_hash_accepts_common_payload_types(payload):
    digest = AuditRepo._hash_payload(payload)
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_hash_does_not_raise_on_unserializable_payload():
    """직렬화 불가 객체가 와도 기록은 계속돼야 한다 — 감사가 요청을 죽이면 안 된다."""

    class Odd:
        def __repr__(self) -> str:
            return "<odd>"

    assert len(AuditRepo._hash_payload({"o": Odd()})) == 64
    assert len(AuditRepo._hash_payload(Odd())) == 64


def test_bytes_and_equivalent_str_hash_identically():
    assert AuditRepo._hash_payload("abc") == AuditRepo._hash_payload(b"abc")


# ── INET 정규화 (PG 컬럼 계약) ────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["127.0.0.1", "10.0.0.7", "::1", "2001:db8::1"])
def test_safe_inet_accepts_valid_addresses(value):
    assert AuditRepo._safe_inet(value) == value


@pytest.mark.parametrize("value", ["testclient", "", None, "not-an-ip", "999.1.1.1", "localhost"])
def test_safe_inet_rejects_non_addresses(value):
    """PG INET 이 거부하는 값을 그대로 넣으면 감사 기록 자체가 실패한다."""
    assert AuditRepo._safe_inet(value) is None


# ── record() 정규화 ───────────────────────────────────────────────────────────

def test_record_parses_string_request_id(repo):
    r, session = repo
    rid = uuid.uuid4()
    entry = r.record(action="classify", request_id=str(rid))
    assert entry.request_id == rid
    assert session.added == [entry]


def test_record_drops_invalid_request_id_without_raising(repo):
    """잘못된 request_id 로 감사가 예외를 던지면 요청 경로가 죽는다."""
    r, _ = repo
    assert r.record(action="classify", request_id="not-a-uuid").request_id is None


def test_record_truncates_long_user_agent(repo):
    r, _ = repo
    entry = r.record(action="classify", user_agent="U" * 900)
    assert len(entry.user_agent) == 500


def test_explicit_payload_hash_wins_over_computed(repo):
    """raw bytes 해시를 미리 계산해 넘기는 경로(문서화된 용도)를 덮어쓰면 안 된다."""
    r, _ = repo
    entry = r.record(action="upload", payload={"a": 1}, payload_hash="deadbeef")
    assert entry.payload_hash == "deadbeef"


def test_no_payload_leaves_hash_none(repo):
    """payload 가 없으면 해시도 없다 — AuditChainNilHash 경보가 보는 상태."""
    r, _ = repo
    assert r.record(action="healthz-excluded").payload_hash is None


def test_record_preserves_failure_fields(repo):
    r, _ = repo
    entry = r.record(action="train", success=False, error_code="HTTP_422")
    assert entry.success is False
    assert entry.error_code == "HTTP_422"


def test_record_keeps_actor_none_when_not_given(repo):
    """공유 API 키 모드에서 actor_id 가 NULL 로 남는 것은 사실이고, 그 사실이 보여야 한다."""
    r, _ = repo
    entry = r.record(action="classify")
    assert entry.actor_id is None and entry.actor_role is None
