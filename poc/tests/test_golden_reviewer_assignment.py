"""검수자 배정 — 전문가별로 볼 수 있는 문서를 서버가 가른다 (2026-09-21).

왜. 2026-09-20 보호원 협의에서 골든셋을 외부 전문가(변호사·교수·포렌식)가 우리 검수 사이트로
검수하기로 확정됐다. 종전에는 reviewer 역할이면 **모든 문서**의 목록·상세·결정에 닿았다(목록
필터 review_batch 는 화면용이라 URL 로 우회된다). 두 가지를 잠근다.

  1. 배정 원장(candidate_assignments.jsonl) — append-only, 관리자 전용 API, 신원은 JWT sub 뿐.
  2. 배정 강제(golden_reviewer_assignment_enforced) — 켜면 reviewer 는 배정된 문서만 본다.
     꺼져 있으면(기본) 종전 동작 그대로다.

이 시험이 정말 격리를 잠그는지는 test_isolation_probe_detects_disabled_isolation 이 확인한다:
격리 코드를 monkeypatch 로 무력화하면 같은 탐침이 새는 것을 **잡아내야** 한다(초록불이 곧 검증은
아니다 — 잠그지 못하는 시험은 무력화해도 초록불이다).

시나리오 (fixture 풀 7건):
    batch-A  A1 A2 A3          expert-a 에게 배치 단위로 배정
    batch-B  B1 B2 B3(공개 실문서)   expert-b 에게 B1·B2 를 문서 단위로 배정, **A3 도 함께**(공유 문서)
    (배치 없음) N1             아무에게도 배정 안 됨 — B3 도 마찬가지
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from koipa.api import _jwt_auth
from koipa.api._jwt_auth import JWTClaims, require_auth
from koipa.api.app import app
from koipa.config import Settings, settings
from koipa.services import golden_reviewer_access as access
from koipa.services import proxy_gold_candidate_service as pgs

API = "/api/v1"

# doc_id → (제안 등급 intended_label, 출처, 배치). doc_id 에 등급을 박은 것은 실제 풀(988/1,067건)이
# 그렇게 생겼기 때문이다 — 숨김 검수는 이 doc_id 를 별칭으로 바꿔 내보낸다(tests/test_golden_alias_ids.py).
POOL: dict[str, tuple[str | None, str, str | None]] = {
    "GOLD-T-TS-001": ("TS", "synthetic", "batch-A"),      # A1
    "GOLD-T-S1-002": ("S1", "synthetic", "batch-A"),      # A2
    "GOLD-T-S2-003": ("S2", "synthetic", "batch-A"),      # A3 (expert-b 와 공유)
    "GOLD-T-S3-004": ("S3", "synthetic", "batch-B"),      # B1
    "GOLD-T-TS-005": ("TS", "synthetic", "batch-B"),      # B2
    "RPUB0000000001": (None, "public_real", "batch-B"),   # B3 — 제안 등급이 없어 S3 로 채워지는 공개 실문서
    "GOLD-T-S1-006": ("S1", "synthetic", None),           # N1 — 배치 없음
}
A1, A2, A3, B1, B2, B3, N1 = list(POOL)

ADMIN = ("admin", "admin-kim")
KL_BACKEND = ("kl_backend", "kl-ops")
SYSTEM = ("system", "system-svc")
EXPERT_A = ("reviewer", "expert-a")
EXPERT_B = ("reviewer", "expert-b")
EXPERT_C = ("reviewer", "expert-c")      # 배정이 하나도 없는 검수자


def make_pool(root: Path) -> None:
    for doc_id, (label, origin, batch) in POOL.items():
        meta = {
            "doc_id": doc_id, "document_origin": origin, "document_type": f"제목 {doc_id}",
            "candidate_status": "proposed", "claim_scope": "fictional test candidate",
            "requires_manual_audit": True,
        }
        if label:
            meta["intended_label"] = label
        if batch:
            meta["review_batch"] = batch
        (root / f"{doc_id}.metadata.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        (root / f"{doc_id}_문서.md").write_text(
            f"본문 표식 BODY-{doc_id} " + "내용 " * 60, encoding="utf-8")


def make_auth(role: str, sub: str) -> dict:
    claims = JWTClaims(sub=sub, roles=(role,), exp=9999999999)
    return {"mode": "jwt", "claims": claims, "actor_role": role, "actor_roles": [role]}


def set_flags(monkeypatch, *, assignment: bool = False, blind: bool = False) -> None:
    monkeypatch.setattr(settings, "golden_reviewer_assignment_enforced", assignment)
    monkeypatch.setattr(settings, "golden_review_blind_enforced", blind)


class Harness:
    """임시 후보 폴더 + 역할별 호출. 서비스의 기본 루트를 임시 폴더로 돌린다."""

    def __init__(self, client: TestClient, root: Path) -> None:
        self.client = client
        self.root = root

    def _as(self, who: tuple[str, str] | dict) -> None:
        auth = who if isinstance(who, dict) else make_auth(*who)
        app.dependency_overrides[require_auth] = lambda: auth

    # 별칭 ID(2026-09-22) -------------------------------------------------
    # 숨김 손잡이가 켜지면 검수자는 실 doc_id 대신 별칭으로만 문서를 가리킨다. 이 파일과 test_golden_blind_review 의 시험은
    # 문서를 POOL 의 실 doc_id 로 적어 두었으므로, Harness 가 **검수자 요청**의 경로 안 실 doc_id 를 별칭으로 바꿔 보내고
    # (alias) 응답의 별칭을 실 doc_id 로 되돌린다(real). 별칭 계층 자체는 tests/test_golden_alias_ids.py 가 이 도움 없이
    # 시험한다.
    def alias(self, doc_id: str) -> str:
        """검수자에게 보이는 이 문서의 별칭(서버가 후보 폴더에 둔 솔트로 계산)."""
        return access.alias_for(access.load_or_create_salt(self.root), doc_id)

    def real(self, doc_or_alias: str) -> str:
        """응답에서 받은 문서 번호 → 실 doc_id. 별칭이 아니거나 솔트가 아직 없으면 그대로."""
        salt_path = self.root / access.ALIAS_SALT_NAME
        if doc_or_alias in POOL or not salt_path.exists():
            return doc_or_alias
        table = {self.alias(d): d for d in POOL}
        return table.get(doc_or_alias, doc_or_alias)

    @staticmethod
    def _role(who) -> str:
        return who.get("actor_role") if isinstance(who, dict) else who[0]

    def _send(self, method: str, who, path: str, **kw):
        """검수자 + 숨김 켜짐이면 별칭 철자로 먼저 보낸다. 서버가 404 로 거절하면 실 철자로도 한 번 더 보낸다 —
        별칭 계층이 꺼진 서버에서는 실 철자가 열려 **시험이 새는 것으로 잡고**, 켜진 서버에서는 어느 철자도 배정 밖
        문서를 열지 못한다(둘 다 404)."""
        self._as(who)
        send = getattr(self.client, method)
        if settings.golden_review_blind_enforced and self._role(who) not in access.PRIVILEGED_ROLES:
            wired = path
            for doc in POOL:
                if doc in wired:
                    wired = wired.replace(doc, self.alias(doc))
            if wired != path:
                r = send(API + wired, **kw)
                if r.status_code != 404:
                    return r
        return send(API + path, **kw)

    def get(self, who, path: str, **kw):
        return self._send("get", who, path, **kw)

    def post(self, who, path: str, **kw):
        return self._send("post", who, path, **kw)

    # 배정 ---------------------------------------------------------------
    def assign(self, reviewer_id: str, *, doc_ids=None, review_batch=None, who=ADMIN, reason=""):
        body: dict = {"reviewer_id": reviewer_id, "reason": reason}
        if doc_ids is not None:
            body["doc_ids"] = doc_ids
        if review_batch is not None:
            body["review_batch"] = review_batch
        return self.post(who, "/golden/assignments", json=body)

    def revoke(self, reviewer_id: str, *, doc_ids=None, review_batch=None, who=ADMIN, reason=""):
        body: dict = {"reviewer_id": reviewer_id, "reason": reason}
        if doc_ids is not None:
            body["doc_ids"] = doc_ids
        if review_batch is not None:
            body["review_batch"] = review_batch
        return self.post(who, "/golden/assignments/revoke", json=body)

    def standard_assignments(self) -> None:
        assert self.assign(EXPERT_A[1], review_batch="batch-A").status_code == 200
        assert self.assign(EXPERT_B[1], doc_ids=[B1, B2, A3]).status_code == 200

    # 원장 ---------------------------------------------------------------
    @property
    def ledger_path(self) -> Path:
        return self.root / access.ASSIGNMENT_LEDGER_NAME

    def ledger_lines(self) -> list[str]:
        return self.ledger_path.read_text(encoding="utf-8").splitlines() if self.ledger_path.exists() else []

    def decision_lines(self) -> list[str]:
        p = self.root / pgs._LEDGER_NAME
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    # 응답에서 doc_id 뽑기 -------------------------------------------------
    def listed_ids(self, who, query: str = "") -> set[str]:
        r = self.get(who, "/golden/candidates" + query)
        assert r.status_code == 200, r.text[:300]
        return {self.real(c["doc_id"]) for c in r.json()["candidates"]}


@pytest.fixture
def h(tmp_path, monkeypatch):
    """기본은 두 손잡이 모두 꺼진 상태 — 켜는 것은 각 시험의 몫이다."""
    make_pool(tmp_path)
    monkeypatch.setattr(pgs, "_DEFAULT_ROOT", tmp_path)
    set_flags(monkeypatch)
    client = TestClient(app)
    yield Harness(client, tmp_path)
    app.dependency_overrides.pop(require_auth, None)


ALL_DOCS = set(POOL)
A_VISIBLE = {A1, A2, A3}
B_VISIBLE = {B1, B2, A3}


# ══ 1. 배정 원장 · 관리자 API ═══════════════════════════════════════════════


def test_settings_default_to_off():
    """기본 False = 종전 동작. 이 기본이 뒤집히면 배포 즉시 모든 검수자가 빈 화면을 본다."""
    for name in ("golden_reviewer_assignment_enforced", "golden_review_blind_enforced"):
        assert Settings.model_fields[name].default is False, name
        assert Settings.model_fields[name].annotation is bool, name


def test_assign_by_batch_and_by_docs_appends_events_with_server_side_actor(h):
    r1 = h.assign("expert-a", review_batch="batch-A", reason="1차 검수")
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["event"] == "assign" and body["actor_id"] == ADMIN[1]
    assert body["applied"] == [{"review_batch": "batch-A"}] and body["skipped"] == []
    assert body["reviewer_visible_doc_count"] == 3

    r2 = h.assign("expert-b", doc_ids=[B1, B2], who=KL_BACKEND)
    assert r2.status_code == 200 and r2.json()["actor_id"] == KL_BACKEND[1]

    rows = [json.loads(x) for x in h.ledger_lines()]
    assert [r["event"] for r in rows] == ["assign", "assign", "assign"]
    first = rows[0]
    # 스펙 필드: doc_id 또는 review_batch, reviewer_id, actor_id, at, reason
    assert first["review_batch"] == "batch-A" and "doc_id" not in first
    assert first["reviewer_id"] == "expert-a" and first["reason"] == "1차 검수"
    assert first["actor_id"] == ADMIN[1] and first["at"].endswith("+00:00")
    assert [r.get("doc_id") for r in rows[1:]] == [B1, B2]
    assert all(r["actor_id"] == KL_BACKEND[1] for r in rows[1:])
    assert all(bool(r.get("doc_id")) != bool(r.get("review_batch")) for r in rows), "대상은 정확히 하나"


def test_actor_can_not_be_claimed_in_the_request_body(h):
    """배정한 관리자는 서버가 확정한 JWT sub 뿐이다 — 본문의 자칭은 조용히 무시되지 않고 거절된다."""
    for forged in ("actor_id", "actor", "assigned_by"):
        r = h.post(ADMIN, "/golden/assignments",
                   json={"reviewer_id": "expert-a", "review_batch": "batch-A", forged: "someone-else"})
        assert r.status_code == 422, (forged, r.text[:200])
    assert h.ledger_lines() == [], "거절된 요청이 원장에 흔적을 남겼다"


def test_assignment_api_requires_a_portal_jwt_not_a_shared_key(h):
    """공유 API Key(sub 없음)로는 '누가 배정했나'가 남지 않는다."""
    key_auth = {"mode": "api_key", "actor_role": "admin", "actor_roles": ["admin"]}
    r = h.post(key_auth, "/golden/assignments", json={"reviewer_id": "expert-a", "review_batch": "batch-A"})
    assert r.status_code == 403 and "portal JWT" in r.text
    assert h.ledger_lines() == []


@pytest.mark.parametrize("who", [EXPERT_A, SYSTEM])
def test_assignment_routes_are_admin_and_kl_backend_only(h, who):
    body = {"reviewer_id": "expert-a", "review_batch": "batch-A"}
    assert h.post(who, "/golden/assignments", json=body).status_code == 403
    assert h.post(who, "/golden/assignments/revoke", json=body).status_code == 403
    assert h.get(who, "/golden/assignments").status_code == 403
    assert h.ledger_lines() == []


@pytest.mark.parametrize("body", [
    {"reviewer_id": "expert-a"},                                                      # 대상 없음
    {"reviewer_id": "expert-a", "doc_ids": [A1], "review_batch": "batch-A"},          # 둘 다
    {"reviewer_id": "  ", "review_batch": "batch-A"},                                 # 빈 검수자
    {"reviewer_id": "expert-a", "doc_ids": []},                                       # 빈 목록
    {"reviewer_id": "expert-a", "doc_ids": ["  "]},                                   # 빈 문서 id
    {"reviewer_id": "expert-a", "review_batch": " "},                                 # 빈 배치
])
def test_assign_request_validation(h, body):
    assert h.post(ADMIN, "/golden/assignments", json=body).status_code == 422
    assert h.ledger_lines() == []


def test_assigning_unknown_targets_is_rejected_but_revoking_them_is_allowed(h):
    """오타 난 doc_id 를 조용히 배정하면 그 검수자는 빈 화면을 보는데 아무도 모른다.
    반대로 이미 사라진 문서의 배정은 정리할 수 있어야 한다."""
    r = h.assign("expert-a", doc_ids=[A1, "GOLD-NO-SUCH-999"])
    assert r.status_code == 422 and "GOLD-NO-SUCH-999" in r.text
    assert h.assign("expert-a", review_batch="no-such-batch").status_code == 422
    assert h.ledger_lines() == [], "검증에 실패한 요청은 하나도 적히면 안 된다(부분 적용 금지)"
    assert h.revoke("expert-a", doc_ids=["GOLD-NO-SUCH-999"]).status_code == 200


def test_delivered_doc_ids_are_normalized_to_console_ids(h):
    """전달본 id(GOLD-…_제목)를 그대로 넣어도 콘솔 id 로 맞춰 적는다(normalize_doc_id)."""
    r = h.assign("expert-a", doc_ids=[f"{A1}_적층공정_공정조건표"])
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == [{"doc_id": A1}]


def test_assign_is_idempotent_and_does_not_write_noop_lines(h):
    assert h.assign("expert-a", review_batch="batch-A").json()["events_written"] == 1
    again = h.assign("expert-a", review_batch="batch-A").json()
    assert again["events_written"] == 0 and again["skipped"] == [{"review_batch": "batch-A"}]
    dup = h.assign("expert-a", doc_ids=[B1, B1, B1]).json()
    assert dup["events_written"] == 1, "같은 요청 안의 중복은 한 번만"
    assert len(h.ledger_lines()) == 2
    # 배정 안 된 것을 해제 — 적지 않고 건너뜀
    r = h.revoke("expert-a", doc_ids=[B2]).json()
    assert r["events_written"] == 0 and r["skipped"] == [{"doc_id": B2}]


def test_revoke_is_appended_never_deleted_and_removes_access(h, monkeypatch):
    set_flags(monkeypatch, assignment=True)
    h.assign("expert-a", review_batch="batch-A")
    assert h.listed_ids(EXPERT_A) == A_VISIBLE
    before = h.ledger_lines()

    r = h.revoke("expert-a", review_batch="batch-A", reason="이해상충")
    assert r.status_code == 200 and r.json()["event"] == "unassign"
    after = h.ledger_lines()
    assert after[: len(before)] == before, "원장은 append-only 다 — 앞선 줄이 바뀌거나 사라졌다"
    assert len(after) == len(before) + 1
    last = json.loads(after[-1])
    assert last["event"] == "unassign" and last["review_batch"] == "batch-A"
    assert last["actor_id"] == ADMIN[1] and last["reason"] == "이해상충"
    assert h.listed_ids(EXPERT_A) == set(), "해제했는데 아직 보인다"
    # 다시 배정하면 돌아온다 — 재생(replay)이 이벤트 순서를 따른다
    h.assign("expert-a", review_batch="batch-A")
    assert h.listed_ids(EXPERT_A) == A_VISIBLE


def test_revoke_must_use_the_same_unit_as_the_assignment(h, monkeypatch):
    """배치로 배정한 검수자에게서 문서 하나만 빼는 것은 표현하지 않는다 — 문서 단위 해제는
    이미 배정되지 않은 것을 해제하는 셈이라 건너뛰고, 그 문서는 배치 배정으로 계속 보인다."""
    set_flags(monkeypatch, assignment=True)
    h.assign("expert-a", review_batch="batch-A")
    r = h.revoke("expert-a", doc_ids=[A1]).json()
    assert r["events_written"] == 0 and r["skipped"] == [{"doc_id": A1}]
    assert A1 in h.listed_ids(EXPERT_A)


def test_status_reports_who_has_what_and_what_nobody_has(h):
    h.standard_assignments()
    st = h.get(ADMIN, "/golden/assignments").json()
    assert st["total_candidates"] == 7
    assert st["assigned_candidate_count"] == 5 and st["unassigned_candidate_count"] == 2, \
        "A1 A2 A3 B1 B2 가 배정됨 — B3·N1 은 아무에게도 안 배정"
    assert st["enforced"] == {"assignment": False, "blind": False}
    by = {r["reviewer_id"]: r for r in st["reviewers"]}
    assert by["expert-a"]["review_batches"] == ["batch-A"] and by["expert-a"]["visible_doc_count"] == 3
    assert by["expert-b"]["doc_ids"] == sorted([A3, B1, B2]) and by["expert-b"]["visible_doc_count"] == 3
    # 필터
    only_b = h.get(ADMIN, "/golden/assignments", params={"reviewer_id": "expert-b"}).json()
    assert [r["reviewer_id"] for r in only_b["reviewers"]] == ["expert-b"]
    shared = h.get(ADMIN, "/golden/assignments", params={"doc_id": A3}).json()
    assert {r["reviewer_id"] for r in shared["reviewers"]} == {"expert-a", "expert-b"}, \
        "A3 는 배치로(a)·문서로(b) 두 사람에게 보인다"
    by_batch = h.get(ADMIN, "/golden/assignments", params={"review_batch": "batch-A"}).json()
    assert [r["reviewer_id"] for r in by_batch["reviewers"]] == ["expert-a"]
    ev = h.get(ADMIN, "/golden/assignments", params={"include_events": "true", "limit": 2}).json()
    assert len(ev["events"]) == 2 and ev["ledger_events_total"] == 4 and ev["by_event"] == {"assign": 4}
    # 해제한 검수자는 현황에서 빠지지만 이력(원장)에는 남는다
    h.revoke("expert-a", review_batch="batch-A")
    st2 = h.get(ADMIN, "/golden/assignments").json()
    assert [r["reviewer_id"] for r in st2["reviewers"]] == ["expert-b"]
    assert st2["ledger_events_total"] == 5


def test_current_state_is_read_after_the_lock_is_taken(h, monkeypatch):
    """현재 상태를 읽는 것부터 적는 것까지가 **한 잠금 안**이다 — 잠금을 잡기 전에 읽으면 그 사이에
    끼어든 같은 배정이 줄을 두 번 적히게 만든다.

    스레드로 경합을 재현하지 않는다: 기존 잠금(proxy_gold_candidate_service._exclusive_ledger_lock)이
    Windows 에서는 스레드 6개만 돌려도 PermissionError 로 죽는다(이 시험을 처음 스레드로 썼을 때
    실측 — 운영 Linux 는 flock 이라 해당 없음). 대신 **경합 결과를 결정적으로 심는다**: 잠금에 들어서는
    순간 다른 관리자가 같은 배정을 이미 적어 둔 상태를 만든다. 상태를 잠금 안에서 읽는 구현만
    그 줄을 보고 건너뛴다.
    """
    ledger = access.AssignmentLedger(h.root)
    real_lock = access._exclusive_ledger_lock
    rival = json.dumps({
        "schema_version": 1, "event_id": "rival", "event": "assign", "reviewer_id": "expert-a",
        "review_batch": "batch-A", "actor_id": "other-admin", "at": "2026-09-21T00:00:00+00:00", "reason": "",
    }, ensure_ascii=False, sort_keys=True)

    class RacingLock:
        def __init__(self, path):
            self._inner = real_lock(path)

        def __enter__(self):
            self._inner.__enter__()
            with ledger.path.open("a", encoding="utf-8", newline="\n") as f:   # 경합 상대가 방금 적음
                f.write(rival + "\n")
            return None

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

    monkeypatch.setattr(access, "_exclusive_ledger_lock", RacingLock)
    written, skipped = ledger.apply(event="assign", reviewer_id="expert-a", actor_id="admin-kim",
                                    targets=[("review_batch", "batch-A")])
    assert written == [] and skipped == [{"review_batch": "batch-A"}], "잠금 밖에서 읽은 낡은 상태로 줄을 또 적었다"
    assert len(h.ledger_lines()) == 1


def test_reviewer_id_matching_ignores_unicode_normalization_form(h, monkeypatch):
    """한글 sub 는 입력 경로에 따라 NFC/NFD 로 갈린다 — 눈에 같은 이름이 안 맞으면 검수자는 빈 화면을 본다."""
    set_flags(monkeypatch, assignment=True)
    nfd = unicodedata.normalize("NFD", "지재원관리자")
    assert nfd != unicodedata.normalize("NFC", "지재원관리자")
    assert h.assign(nfd, review_batch="batch-A").status_code == 200
    assert h.listed_ids(("reviewer", unicodedata.normalize("NFC", "지재원관리자"))) == A_VISIBLE


def test_ledger_skips_corrupt_lines_and_unreadable_ledger_grants_nothing(h, monkeypatch, caplog):
    set_flags(monkeypatch, assignment=True)
    h.assign("expert-a", review_batch="batch-A")
    with h.ledger_path.open("a", encoding="utf-8", newline="\n") as f:
        f.write("이건 JSON 이 아니다\n")
        f.write(json.dumps({"event": "assign", "reviewer_id": "expert-a"}) + "\n")            # 대상 없음
        f.write(json.dumps({"event": "assign", "reviewer_id": "x", "doc_id": "a",
                            "review_batch": "b"}) + "\n")                                     # 대상 둘
        f.write(json.dumps({"event": "grant", "reviewer_id": "expert-a", "doc_id": N1}) + "\n")  # 모르는 종류
    with caplog.at_level("WARNING"):
        assert h.listed_ids(EXPERT_A) == A_VISIBLE, "깨진 줄이 유효한 배정을 흔들었다"
    assert sum("건너뜀" in r.message for r in caplog.records) >= 4, "깨진 줄을 조용히 넘겼다"

    # 원장을 읽지 못하면(여기서는 파일 자리에 폴더) 아무도 배정받지 못한 것 — 새지 않고 빈 화면이 된다
    h.ledger_path.unlink()
    h.ledger_path.mkdir()
    assert h.listed_ids(EXPERT_A) == set()


# ══ 2. 배정 강제 — 역할 3종 × 라우트 ═════════════════════════════════════════


def test_default_state_reviewer_still_sees_everything_even_with_assignments_on_file(h):
    """두 손잡이가 꺼진 기본 상태 = 종전 동작. 배정을 적어 뒀어도 강제 전에는 아무것도 안 바뀐다."""
    h.standard_assignments()
    assert h.listed_ids(EXPERT_A) == ALL_DOCS
    assert h.listed_ids(EXPERT_B) == ALL_DOCS
    assert h.listed_ids(EXPERT_C) == ALL_DOCS
    assert h.get(EXPERT_C, f"/golden/candidates/{N1}").status_code == 200
    assert h.get(EXPERT_C, "/golden/candidates/summary").json()["total"] == 7
    r = h.post(EXPERT_C, f"/golden/candidates/{N1}/decision", json={"action": "approve"})
    assert r.status_code == 200 and r.json()["final_grade"] == "S1"
    # 제안 등급도 그대로 보인다
    row = next(c for c in h.get(EXPERT_C, "/golden/candidates").json()["candidates"] if c["doc_id"] == A1)
    assert row["proposed_grade"] == "TS"


@pytest.mark.parametrize("who", [ADMIN, KL_BACKEND, SYSTEM])
def test_privileged_roles_always_see_everything_when_enforced(h, monkeypatch, who):
    h.standard_assignments()
    set_flags(monkeypatch, assignment=True, blind=True)
    assert h.listed_ids(who) == ALL_DOCS
    row = h.get(who, f"/golden/candidates/{B3}").json()
    assert row["proposed_grade"] == "S3" and row["proposed_grade_basis"], "관리자는 제안 등급을 본다"
    assert h.get(who, "/golden/candidates/summary").json()["total"] == 7
    assert "quality" in h.get(who, "/golden/candidates/summary").json()


def test_each_expert_sees_only_the_assigned_documents(h, monkeypatch):
    h.standard_assignments()
    set_flags(monkeypatch, assignment=True)
    assert h.listed_ids(EXPERT_A) == A_VISIBLE
    assert h.listed_ids(EXPERT_B) == B_VISIBLE
    assert h.listed_ids(EXPERT_C) == set(), "배정이 하나도 없는 문서·검수자는 아무것도 못 본다"
    # 필터·검색으로 배정 밖을 끌어올 수 없다
    assert h.listed_ids(EXPERT_A, "?review_batch=batch-B") == set()
    assert h.listed_ids(EXPERT_A, f"?query={N1}") == set()
    assert h.listed_ids(EXPERT_A, "?origin=public_real") == set()
    assert h.listed_ids(EXPERT_B, "?status=proposed") == B_VISIBLE
    # 아무에게도 배정 안 된 문서는 관리자에게만 보인다
    for orphan in (N1, B3):
        assert orphan not in h.listed_ids(EXPERT_A) | h.listed_ids(EXPERT_B)
        assert orphan in h.listed_ids(ADMIN)


def test_detail_of_unassigned_document_is_indistinguishable_from_a_missing_one(h, monkeypatch):
    """403 이 아니라 없는 문서와 **같은** 404 다 — 403 은 '있는데 네 것이 아니다'를 알려 준다."""
    h.standard_assignments()
    set_flags(monkeypatch, assignment=True)
    missing = h.get(EXPERT_A, "/golden/candidates/GOLD-NO-SUCH-999")
    assert missing.status_code == 404
    for foreign in (B1, N1, B3):
        r = h.get(EXPERT_A, f"/golden/candidates/{foreign}")
        assert (r.status_code, r.json()) == (missing.status_code, missing.json()), foreign
        assert foreign not in r.text and "BODY-" not in r.text
    assert h.get(EXPERT_A, f"/golden/candidates/{A1}").status_code == 200
    assert h.get(EXPERT_B, f"/golden/candidates/{A3}").status_code == 200, "공유 문서는 둘 다 본다"


def test_decision_on_unassigned_document_is_refused_and_leaves_no_trace(h, monkeypatch):
    h.standard_assignments()
    set_flags(monkeypatch, assignment=True)
    before = h.decision_lines()
    body = {"action": "change", "grade": "S2", "reason": "본문을 읽고 판단"}
    for foreign in (B1, N1, "GOLD-NO-SUCH-999"):
        r = h.post(EXPERT_A, f"/golden/candidates/{foreign}/decision", json=body)
        assert r.status_code == 404, (foreign, r.status_code)
    assert h.decision_lines() == before, "배정 밖 결정이 원장에 남았다"
    # 본문 검증(422)보다 배정 검사가 **먼저**다 — 순서가 뒤바뀌면 422 사유가 '이 문서는 있다'를 알린다.
    # B3(공개 실문서)에 approve 는 서비스가 ValueError(422)로 거절하는 조합이다.
    assert h.post(EXPERT_A, f"/golden/candidates/{B3}/decision", json={"action": "approve"}).status_code == 404
    assert h.post(ADMIN, f"/golden/candidates/{B3}/decision", json={"action": "approve"}).status_code == 422, \
        "이 조합이 정말 422 인지(=위 시험이 순서를 재는지) 관리자로 확인"
    # 배정된 문서는 결정된다
    ok = h.post(EXPERT_A, f"/golden/candidates/{A1}/decision", json=body)
    assert ok.status_code == 200 and ok.json()["final_grade"] == "S2"
    assert json.loads(h.decision_lines()[-1])["actor_id"] == EXPERT_A[1]


def test_summary_and_available_batches_do_not_leak_unassigned_documents(h, monkeypatch):
    """집계가 전량 기준이면 배정 안 된 문서의 등급 분포·배치 이름·건수가 새어 나간다."""
    h.standard_assignments()
    set_flags(monkeypatch, assignment=True)
    full = h.get(ADMIN, "/golden/candidates").json()
    assert full["summary"]["total"] == 7

    listed = h.get(EXPERT_A, "/golden/candidates").json()
    s = listed["summary"]
    assert s["total"] == 3 and s["by_origin"] == {"synthetic": 3}
    assert s["scope"] == "assigned" and full["summary"]["scope"] == "all", "집계 기준 표기가 거짓이 되면 안 된다"
    assert s["by_proposed_grade"] == {"S1": 1, "S2": 1, "TS": 1}, "A 의 세 문서 분포여야 한다"
    assert s["quality"]["documents"] == 3
    assert listed["available_batches"] == [{"review_batch": "batch-A", "total": 3}], "batch-B 이름이 새었다"
    assert listed["batch_summary"]["total"] == 3

    only = h.get(EXPERT_A, "/golden/candidates/summary").json()
    assert only["total"] == 3 and only["by_proposed_grade"] == {"S1": 1, "S2": 1, "TS": 1}
    assert only["quality"]["documents"] == 3
    b = h.get(EXPERT_B, "/golden/candidates").json()
    assert b["summary"]["total"] == 3 and b["summary"]["by_origin"] == {"synthetic": 3}, "B3 는 B 에게도 안 보인다"
    assert b["available_batches"] == [{"review_batch": "batch-A", "total": 1}, {"review_batch": "batch-B", "total": 2}]
    empty = h.get(EXPERT_C, "/golden/candidates/summary").json()
    assert empty["total"] == 0 and empty["by_origin"] == {}
    # 관리자는 그대로 전량
    assert h.get(ADMIN, "/golden/candidates/summary").json()["by_origin"] == {"public_real": 1, "synthetic": 6}


def test_decision_ledger_view_is_scoped_and_counts_are_recomputed(h, monkeypatch):
    h.standard_assignments()
    # 배정 전에 관리자가 배정 밖 문서에 남긴 결정 + 각자의 결정
    h.post(ADMIN, f"/golden/candidates/{N1}/decision", json={"action": "discard", "reason": "범위 밖 사본"})
    h.post(ADMIN, f"/golden/candidates/{B1}/decision", json={"action": "defer", "reason": "출처 확인"})
    set_flags(monkeypatch, assignment=True)
    h.post(EXPERT_A, f"/golden/candidates/{A1}/decision", json={"action": "change", "grade": "TS", "reason": "본문 근거"})
    h.post(EXPERT_B, f"/golden/candidates/{B2}/decision", json={"action": "change", "grade": "S1", "reason": "본문 근거"})

    full = h.get(ADMIN, "/golden/candidates/decisions").json()
    assert full["total"] == 4
    a = h.get(EXPERT_A, "/golden/candidates/decisions").json()
    assert {e["doc_id"] for e in a["events"]} == {A1}
    assert a["total"] == 1 and a["by_action"] == {"change": 1}, "걸러내기 **전** 합계가 실렸다"
    b = h.get(EXPERT_B, "/golden/candidates/decisions").json()
    assert {e["doc_id"] for e in b["events"]} == {B1, B2}, "배정된 문서의 이벤트는(관리자 것도) 보인다"
    assert b["total"] == 2 and b["by_action"] == {"change": 1, "defer": 1}
    assert h.get(EXPERT_C, "/golden/candidates/decisions").json() == {"total": 0, "by_action": {}, "events": []}
    # limit 은 걸러낸 뒤에 자른다 — 남의 이벤트가 내 이벤트를 밀어내면 안 된다
    for i in range(3):
        h.post(ADMIN, f"/golden/candidates/{N1}/decision", json={"action": "defer", "reason": f"관리자 메모 {i}"})
    assert [e["doc_id"] for e in h.get(EXPERT_A, "/golden/candidates/decisions", params={"limit": 1}).json()["events"]] == [A1]


def test_shared_document_decisions_of_the_other_expert_are_visible_without_blind(h, monkeypatch):
    """배정 강제만 켠 상태(숨김 꺼짐)는 종전 그대로 — 같은 문서를 배정받은 사람의 결정이 보인다.
    독립 판정이 필요하면 숨김 손잡이를 켠다(test_golden_blind_review)."""
    h.standard_assignments()
    set_flags(monkeypatch, assignment=True)
    h.post(EXPERT_A, f"/golden/candidates/{A3}/decision", json={"action": "change", "grade": "S3", "reason": "판단"})
    seen = h.get(EXPERT_B, f"/golden/candidates/{A3}").json()
    assert seen["final_grade"] == "S3" and seen["decision_history"][0]["actor_id"] == EXPERT_A[1]


def test_reviewer_without_portal_identity_is_refused_when_enforced(h, monkeypatch):
    """공유 API Key 로 들어온 reviewer 는 sub 가 없다 — 누구의 배정인지 정할 수 없으니 통과시키지 않는다."""
    keyed = {"mode": "api_key", "actor_role": "reviewer", "actor_roles": ["reviewer"]}
    assert h.get(keyed, "/golden/candidates").status_code == 200, "꺼진 상태는 종전 동작"
    set_flags(monkeypatch, assignment=True)
    for path in ("/golden/candidates", "/golden/candidates/summary", "/golden/candidates/decisions",
                 f"/golden/candidates/{A1}"):
        r = h.get(keyed, path)
        assert r.status_code == 403 and "portal JWT" in r.text, path


def test_session_reveals_only_the_caller(h, monkeypatch):
    set_flags(monkeypatch, assignment=True, blind=True)
    r = h.get(EXPERT_A, "/golden/candidates/session")
    assert r.status_code == 200 and r.json()["actor_id"] == "expert-a" and set(r.json()) == {
        "actor_id", "auth_mode", "actor_role"}


# ══ 3. 잡 단위 화면·API — 배정으로 걸러낼 수 없어 손잡이가 켜진 동안 검수자에게 닫는다 ════════════

JOB = "11111111-2222-4333-8444-555555555555"
JOB_API_ROUTES = [
    ("get", "/golden/jobs"),
    ("get", f"/golden/jobs/{JOB}"),
    ("get", "/golden/summary"),
    ("get", f"/golden/jobs/{JOB}/signoff/preflight"),
    ("post", f"/golden/jobs/{JOB}/signoff"),
]
_SIGNOFF_BODY = {"decisions": [], "actor": {"user_id": "x", "role": "reviewer"}}


def _job_call(h, who, method, path):
    kw = {"json": _SIGNOFF_BODY} if method == "post" else {}
    return getattr(h, method)(who, path, **kw)


@pytest.mark.parametrize("flags", [dict(assignment=True), dict(blind=True), dict(assignment=True, blind=True)])
@pytest.mark.parametrize(("method", "path"), JOB_API_ROUTES)
def test_job_level_apis_are_closed_to_reviewers_while_any_flag_is_on(h, monkeypatch, flags, method, path):
    set_flags(monkeypatch, **flags)
    r = _job_call(h, EXPERT_A, method, path)
    assert r.status_code == 403 and "job-level" in r.text, (path, r.status_code, r.text[:120])
    for who in (ADMIN, KL_BACKEND):
        r = _job_call(h, who, method, path)
        assert not (r.status_code == 403 and "job-level" in r.text), (path, who)


@pytest.mark.parametrize(("method", "path"), JOB_API_ROUTES)
def test_job_level_apis_are_unchanged_by_default(h, method, path):
    """꺼진 기본 상태 — reviewer 가 이 라우트들에서 종전과 같은 응답(존재하지 않는 잡이라 404)을 받는다."""
    r = _job_call(h, EXPERT_A, method, path)
    assert not (r.status_code == 403 and "job-level" in r.text), path
    if "jobs/" in path:
        assert r.status_code in (404, 200, 422), (path, r.status_code)


@pytest.fixture
def cookie_jwt(monkeypatch):
    """잡 HTML 은 무인증 라우터라 dependency_overrides 가 안 닿는다 — 실제 쿠키·JWT 경로를 쓴다."""
    people = {
        "tok-admin": JWTClaims(sub="admin-kim", roles=("admin",), exp=9999999999),
        "tok-a": JWTClaims(sub="expert-a", roles=("reviewer",), exp=9999999999),
    }

    def fake_verify(token):
        if token in people:
            return people[token]
        raise _jwt_auth.JWTError("unknown token")

    monkeypatch.setattr(settings, "auth_mode", "jwt")
    monkeypatch.setattr(_jwt_auth, "verify_jwt", fake_verify)


JOB_HTML = [f"/golden/jobs/{JOB}/review.html", f"/golden/jobs/{JOB}/signoff.html"]


@pytest.mark.parametrize("flags", [dict(assignment=True), dict(blind=True)])
@pytest.mark.parametrize("path", JOB_HTML)
def test_job_html_requires_login_and_closes_to_reviewers_when_enforced(h, monkeypatch, cookie_jwt, flags, path):
    set_flags(monkeypatch, **flags)
    app.dependency_overrides.pop(require_auth, None)
    c = h.client
    anon = c.get(API + path)
    assert anon.status_code == 401, "손잡이가 켜졌는데 로그인 없이 잡 화면이 열린다(쿠키를 빼면 우회)"
    rev = c.get(API + path, headers={"Cookie": "koipa_access_token=tok-a"})
    assert rev.status_code == 403 and "job-level" in rev.text
    adm = c.get(API + path, headers={"Cookie": "koipa_access_token=tok-admin"})
    assert adm.status_code == 404, "관리자는 통과해 잡 조회까지 간다(없는 잡이라 404)"


@pytest.mark.parametrize("path", JOB_HTML)
def test_job_html_is_unchanged_by_default(h, cookie_jwt, path):
    """꺼진 기본 상태 — 로그인 없이도 서명 URL 토큰 검사만 거쳐 종전대로 열린다(없는 잡 → 404 안내 화면)."""
    app.dependency_overrides.pop(require_auth, None)
    r = h.client.get(API + path)
    assert r.status_code == 404 and "이 검수 잡이 서버에 없습니다" in r.text


# ══ 4. 우회 경로 전수 — golden.py 의 모든 라우트를 분류하고, 분류에 없으면 실패한다 ═══════════════

ADMIN_ONLY = "admin_only"                  # require_role("admin", "kl_backend"[, "system"]) — reviewer 는 403
REVIEWER_SCOPED = "reviewer_scoped"        # reviewer 가 닿고 배정·숨김이 적용된다
REVIEWER_CLOSED = "reviewer_closed"        # reviewer 가 닿지만 손잡이가 켜진 동안 403(잡 단위)
NO_DATA = "no_data"                        # 후보 데이터가 없다(로그인 틀·본인 신원)

ROUTE_TABLE: dict[tuple[str, str], str] = {
    ("POST", "/golden/build"): ADMIN_ONLY,
    ("GET", "/golden/builds"): ADMIN_ONLY,
    ("POST", "/golden/jobs/register"): ADMIN_ONLY,
    ("POST", "/golden/candidates/upload"): ADMIN_ONLY,
    ("POST", "/golden/candidates/{doc_id}/provenance"): ADMIN_ONLY,
    ("POST", "/golden/candidates/promote"): ADMIN_ONLY,
    ("POST", "/golden/assignments"): ADMIN_ONLY,
    ("POST", "/golden/assignments/revoke"): ADMIN_ONLY,
    ("GET", "/golden/assignments"): ADMIN_ONLY,
    ("GET", "/golden/candidates"): REVIEWER_SCOPED,
    ("GET", "/golden/candidates/summary"): REVIEWER_SCOPED,
    ("GET", "/golden/candidates/decisions"): REVIEWER_SCOPED,
    ("GET", "/golden/candidates/{doc_id}"): REVIEWER_SCOPED,
    ("POST", "/golden/candidates/{doc_id}/decision"): REVIEWER_SCOPED,
    ("GET", "/golden/candidates/manage.html"): REVIEWER_SCOPED,
    ("GET", "/golden/candidates/session"): NO_DATA,
    ("GET", "/golden/candidates/login.html"): NO_DATA,
    ("GET", "/golden/jobs"): REVIEWER_CLOSED,
    ("GET", "/golden/jobs/{job_id}"): REVIEWER_CLOSED,
    ("GET", "/golden/summary"): REVIEWER_CLOSED,
    ("GET", "/golden/jobs/{job_id}/review.html"): REVIEWER_CLOSED,
    ("GET", "/golden/jobs/{job_id}/signoff.html"): REVIEWER_CLOSED,
    ("GET", "/golden/jobs/{job_id}/signoff/preflight"): REVIEWER_CLOSED,
    ("POST", "/golden/jobs/{job_id}/signoff"): REVIEWER_CLOSED,
}


def _golden_routes() -> set[tuple[str, str]]:
    import koipa.api.golden as g

    out = set()
    for router in (g.router, g.html_router):
        for route in router.routes:
            for method in getattr(route, "methods", None) or ():
                out.add((method, route.path))
    return out


def test_every_golden_route_is_classified():
    """새 라우트가 생기면 '검수자가 닿나·배정/숨김이 적용되나'를 **정하고 나서야** 통과한다."""
    live = _golden_routes()
    assert live - set(ROUTE_TABLE) == set(), f"분류 안 된 라우트: {sorted(live - set(ROUTE_TABLE))}"
    assert set(ROUTE_TABLE) - live == set(), f"표에만 있는 라우트: {sorted(set(ROUTE_TABLE) - live)}"


@pytest.mark.parametrize("flags", [
    dict(), dict(assignment=True), dict(blind=True), dict(assignment=True, blind=True)])
def test_admin_only_routes_are_403_for_reviewers_under_every_flag_combination(h, monkeypatch, flags):
    set_flags(monkeypatch, **flags)
    upload = {"files": {"file": ("a.txt", b"x", "text/plain")}}
    calls = {
        ("POST", "/golden/build"): {"json": {}},
        ("GET", "/golden/builds"): {},
        ("POST", "/golden/jobs/register"): {"json": {}},
        ("POST", "/golden/candidates/upload"): upload,
        ("POST", "/golden/candidates/{doc_id}/provenance"): {"json": {}},
        ("POST", "/golden/candidates/promote"): {"json": {}},
        ("POST", "/golden/assignments"): {"json": {}},
        ("POST", "/golden/assignments/revoke"): {"json": {}},
        ("GET", "/golden/assignments"): {},
    }
    assert {k for k, v in ROUTE_TABLE.items() if v == ADMIN_ONLY} == set(calls), "표와 호출 목록이 어긋났다"
    for (method, path), kw in calls.items():
        real = path.replace("{doc_id}", A1)
        r = getattr(h, method.lower())(EXPERT_A, real, **kw)
        assert r.status_code == 403, (method, path, r.status_code)


def test_manage_html_carries_no_candidate_data_inline(h, monkeypatch):
    """화면은 정적 틀이고 데이터는 API 로 받는다 — 후보 정보가 HTML 본문에 박혀 나가면 API 격리가 무의미하다."""
    h.standard_assignments()
    set_flags(monkeypatch, assignment=True, blind=True)
    for who in (EXPERT_A, EXPERT_C, ADMIN):
        r = h.get(who, "/golden/candidates/manage.html")
        assert r.status_code == 200
        for doc_id in POOL:
            assert doc_id not in r.text, (who, doc_id)
            assert f"BODY-{doc_id}" not in r.text and f"제목 {doc_id}" not in r.text
    # 화면 틀은 배정 강제를 알지 못한다 — 같은 틀이 검수자마다 같고, 걸러내는 일은 API 가 한다.
    set_flags(monkeypatch, assignment=True)
    assert h.get(EXPERT_A, "/golden/candidates/manage.html").text == \
        h.get(EXPERT_B, "/golden/candidates/manage.html").text


# ══ 5. 이 시험이 정말 격리를 잠그는가 — 무력화하면 잡아내야 한다 ═══════════════════════════════


def isolation_leaks(h: Harness, monkeypatch) -> list[str]:
    """두 손잡이를 켜고 expert-a 로 배정 밖·숨김 대상을 **찔러 본다.** 새는 것마다 한 줄을 돌려준다."""
    set_flags(monkeypatch, assignment=True, blind=True)
    leaks: list[str] = []

    def note(cond: bool, what: str) -> None:
        if cond:
            leaks.append(what)

    listed = h.get(EXPERT_A, "/golden/candidates")
    ids = {h.real(c["doc_id"]) for c in listed.json().get("candidates", [])} if listed.status_code == 200 else set()
    note(bool(ids - A_VISIBLE), "목록에 배정 밖 문서가 보인다")
    note(h.get(EXPERT_A, f"/golden/candidates/{B1}").status_code != 404, "배정 밖 문서 상세가 열린다")
    n = len(h.decision_lines())
    r = h.post(EXPERT_A, f"/golden/candidates/{B1}/decision", json={"action": "change", "grade": "S1", "reason": "찔러봄"})
    note(r.status_code != 404 or len(h.decision_lines()) != n, "배정 밖 문서에 결정이 기록된다")
    note(h.get(EXPERT_A, "/golden/candidates/summary").json().get("total") != 3, "집계에 배정 밖 문서가 섞인다")
    note("batch-B" in json.dumps(listed.json().get("available_batches", [])), "배정 밖 배치 이름이 보인다")
    note(any(h.real(e["doc_id"]) not in A_VISIBLE
             for e in h.get(EXPERT_A, "/golden/candidates/decisions").json().get("events", [])),
         "결정 원장에 배정 밖 문서가 보인다")
    row = h.get(EXPERT_A, f"/golden/candidates/{A1}").json()
    note("proposed_grade" in row, "숨김인데 제안 등급이 응답에 있다")
    note(h.get(EXPERT_A, "/golden/candidates", params={"grade": "TS"}).status_code != 422,
         "grade 필터로 제안 등급을 캘 수 있다")
    r = h.post(EXPERT_A, f"/golden/candidates/{A2}/decision", json={"action": "approve"})
    note(r.status_code != 403, "숨김인데 제안 등급 그대로 확정(approve)이 받아들여진다")
    note(h.get(EXPERT_A, "/golden/jobs").status_code != 403, "잡 목록이 검수자에게 열린다")
    note(h.get(EXPERT_A, f"/golden/jobs/{JOB}").status_code != 403, "잡 상태(서명 URL 발급)가 검수자에게 열린다")
    return leaks


def test_isolation_probe_finds_nothing_when_isolation_is_on(h, monkeypatch):
    h.standard_assignments()
    assert isolation_leaks(h, monkeypatch) == []


def test_isolation_probe_detects_disabled_isolation(h, monkeypatch):
    """격리 코드를 monkeypatch 로 무력화(파일 수정 없이)하면 같은 탐침이 **새는 것을 잡는다.**

    잡지 못하면 이 파일의 다른 시험은 격리를 잠그지 못한 것이다 — 무력화해도 초록불이기 때문이다.
    """
    h.standard_assignments()
    monkeypatch.setattr(access, "resolve_scope", lambda auth: access.UNRESTRICTED)
    monkeypatch.setattr(access, "enforcement_active", lambda: False)
    leaks = isolation_leaks(h, monkeypatch)
    for expected in (
        "목록에 배정 밖 문서가 보인다", "배정 밖 문서 상세가 열린다", "배정 밖 문서에 결정이 기록된다",
        "집계에 배정 밖 문서가 섞인다", "배정 밖 배치 이름이 보인다", "결정 원장에 배정 밖 문서가 보인다",
        "숨김인데 제안 등급이 응답에 있다", "grade 필터로 제안 등급을 캘 수 있다",
        "숨김인데 제안 등급 그대로 확정(approve)이 받아들여진다", "잡 목록이 검수자에게 열린다",
        "잡 상태(서명 URL 발급)가 검수자에게 열린다",
    ):
        assert expected in leaks, f"무력화했는데 탐침이 못 잡았다: {expected} (잡은 것: {leaks})"
