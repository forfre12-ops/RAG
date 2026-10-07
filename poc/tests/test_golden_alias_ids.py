"""검수자용 별칭 ID(불투명 번호) 계층 — doc_id 에 박힌 등급 코드가 숨김 검수를 뚫지 못하게 한다 (2026-09-22).

왜. 숨김 검수(test_golden_blind_review)는 제안 등급을 응답에서 뺐지만, 후보 풀 1,067건 중 988건의 doc_id 가
GOLD-B1-S1-036 처럼 등급 코드를 품고 있다(그 코드는 988/988건이 제안 등급과 같다 — 2026-09-22 측정). doc_id 는 화면·원장·결정
API 의 열쇠라 서버가 지울 수 없었고, 목록이 doc_id 순으로 정렬되어 **줄 위치만으로도 등급이 읽혔다**(이웃 문서가 같은 제안
등급인 비율 0.9606, 라벨을 섞으면 0.2608). 그래서 숨김이 켜진 동안 검수자에게 나가는 모든 doc_id 를 서버가 별칭
(`RV-` + HMAC 의 앞 16자)으로 바꾼다. 이 파일이 그 계층을 잠근다:

  · 검수자의 어떤 응답에도 실 doc_id 와 등급 코드 토큰이 (본문 밖에) 없다 — 재귀로 전부 훑는다.
  · 별칭은 재시작·솔트 재읽기 후에도 같고, 솔트는 동시 생성 경합에서 하나만 살아남는다.
  · 별칭으로 상세·결정이 되고, 결정은 원장에 **실 doc_id** 로 남는다(원장·후보 파일 형식은 그대로).
  · 실 doc_id 를 경로에 넣으면 없는 문서와 같은 404 다. 검색(query)은 별칭 앞부분에만 닿는다(doc_id·title 은 등급 코드를 담을
    수 있어 뺐다). 정렬은 별칭 순이다.
  · 별칭 계층이 망가지면(솔트 읽기·쓰기 실패, 풀 안 충돌) 요청을 거절한다(fail-closed).
  · 손잡이를 끄면·관리자 시야는 종전과 같다(관리자에게는 숨김이 켜진 동안 reviewer_alias 키만 더해진다).

이 시험이 정말 계층을 잠그는지는 test_probe_* 가 확인한다 — 계층을 monkeypatch 로 끄면 같은 탐침이 새는 것을 **잡아야** 한다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import shutil
import subprocess
import threading
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from koipa.api._jwt_auth import require_auth
from koipa.api.app import app
from koipa.services import golden_reviewer_access as access
from koipa.services import proxy_gold_candidate_service as pgs
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService
from tests.test_golden_reviewer_assignment import (
    ADMIN, KL_BACKEND, SYSTEM, make_auth, set_flags,
)

API = "/api/v1"
RV_A = ("reviewer", "rv-a")
RV_B = ("reviewer", "rv-b")
NOISE = "본문 표식 없음 " * 40

# 등급 코드가 독립 낱말로 나오는가 — 한글·밑줄이 붙은 'TS급'·'S3로' 도 잡도록 영문·숫자만 경계로 본다.
CODE = re.compile(r"(?<![A-Za-z0-9])(?:TS|S1|S2|S3)(?![A-Za-z0-9])", re.IGNORECASE)

BODY_PAIR1 = "공정 조건을 검토한 문서입니다. 재검토가 필요하면 S3로 재검토할 수 있고, 근거를 남깁니다. " + NOISE
BODY_PAIR2 = "설비 점검 결과를 정리한 문서입니다. 점검 주기와 조치 결과를 적었습니다. " + NOISE
BODIES = {
    "B1": BODY_PAIR1, "B2": BODY_PAIR2,
    "B3": "TS 등급 후보를 논의하는 회의록이 아니라 일반 회의 문서입니다. " + NOISE,
    "B4": "취급 지침을 설명하는 문서입니다. " + NOISE,
    "B5": "공개된 기업 공시를 요약한 문서입니다. " + NOISE,
    "B6": "관리 규정 본문입니다. " + NOISE,
}

# (doc_id, 제안 등급, 출처, 배치, 제목, 본문 키). 같은 본문 쌍 두 개 — doc_id 의 등급 코드만 다르거나(036) 코드가 아예 없다(037).
ALIAS_POOL = [
    ("GOLD-B1-S1-036", "S1", "synthetic", "kl-x", "공정 조건표 검토", "B1"),
    ("GOLD-B1-S3-036", "S3", "synthetic", "kl-x", "공정 조건표 검토", "B1"),
    ("GOLD-B1-S1-037", "S1", "synthetic", "kl-x", "설비 점검 보고", "B2"),
    ("GOLD-NC-0037", "S1", "synthetic", "kl-x", "설비 점검 보고", "B2"),
    ("GOLD-P-TS-001", "TS", "synthetic", "kl-y", "제목 GOLD-P-TS-001 검토", "B3"),      # 제목에 자기 doc_id
    ("GOLD-B1-S2-007", "S2", "synthetic", "kl-y", "특급기밀 취급지침", "B4"),            # 합성 후보의 제목에 등급명 어휘
    ("GOLD-UPL-94FFC63156F6", None, "public_real", "kl-y", "real-S3-ipo-ksensor", "B5"),  # 실제 풀에서 잰 1건과 같은 꼴
    ("RPUB-0002", None, "public_real", None, "대외비 관리규정", "B6"),                    # 실문서의 등급명 어휘는 제목 그대로
]
P1S1, P1S3, P2S1, P2NC, PTS, PS2, PUPL, PRPUB = (row[0] for row in ALIAS_POOL)
ALL_IDS = [row[0] for row in ALIAS_POOL]
X_BATCH = {P1S1, P1S3, P2S1, P2NC}

SALT_HEX = hashlib.sha256(b"fixed salt for the alias tests").hexdigest()        # 64자 16진수 — 값이 고정돼야 결과가 고정된다


def make_alias_pool(root: Path, rows=ALIAS_POOL) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for doc_id, label, origin, batch, title, body_key in rows:
        meta = {
            "doc_id": doc_id, "document_origin": origin, "document_type": title,
            "candidate_status": "proposed", "claim_scope": "fictional test candidate",
            "requires_manual_audit": True,
        }
        if label:
            meta["intended_label"] = label
        if batch:
            meta["review_batch"] = batch
        if origin == "public_real":
            meta["provenance"] = {"source_reference": "공개 기관 게시물", "authorization_basis": "공개",
                                  "status": "recorded"}
        (root / f"{doc_id}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        (root / f"{doc_id}_문서.md").write_text(BODIES[body_key], encoding="utf-8")


class Api:
    """역할별 호출 — 시험 도구(Harness)와 달리 경로를 **있는 그대로** 보낸다(별칭으로 바꿔 주지 않는다)."""

    def __init__(self, client: TestClient, root: Path) -> None:
        self.client, self.root = client, root

    def _as(self, who) -> None:
        auth = make_auth(*who)
        app.dependency_overrides[require_auth] = lambda: auth

    def get(self, who, path: str, **kw):
        self._as(who)
        return self.client.get(API + path, **kw)

    def post(self, who, path: str, **kw):
        self._as(who)
        return self.client.post(API + path, **kw)

    def assign(self, reviewer: str, *, doc_ids=None, review_batch=None):
        body = {"reviewer_id": reviewer, **({"doc_ids": doc_ids} if doc_ids else {"review_batch": review_batch})}
        r = self.post(ADMIN, "/golden/assignments", json=body)
        assert r.status_code == 200, r.text
        return r

    def decision_lines(self) -> list[str]:
        p = self.root / pgs._LEDGER_NAME
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    @property
    def salt_path(self) -> Path:
        return self.root / access.ALIAS_SALT_NAME

    # 검수자가 보는 번호 표 — 관리자 시야의 reviewer_alias(= 숨김이 켜진 동안 매핑용으로 더해지는 키)에서 읽는다.
    def alias_table(self) -> dict[str, str]:
        rows = self.get(ADMIN, "/golden/candidates").json()["candidates"]
        return {c["doc_id"]: c["reviewer_alias"] for c in rows}


@pytest.fixture
def api(tmp_path, monkeypatch):
    make_alias_pool(tmp_path)
    monkeypatch.setattr(pgs, "_DEFAULT_ROOT", tmp_path)
    pgs._CANDIDATE_CACHE.clear()
    set_flags(monkeypatch)
    yield Api(TestClient(app), tmp_path)
    app.dependency_overrides.pop(require_auth, None)
    pgs._CANDIDATE_CACHE.clear()


def blind_on(monkeypatch, *, assignment: bool = True) -> None:
    set_flags(monkeypatch, assignment=assignment, blind=True)


def standard_assignments(api: Api) -> None:
    """rv-a: kl-x 배치 + GOLD-P-TS-001 · rv-b: kl-y 배치(TS-001·B1-S2-007·UPL) + 공유 문서 GOLD-B1-S1-036."""
    api.assign("rv-a", review_batch="kl-x")
    api.assign("rv-a", doc_ids=[PTS])
    api.assign("rv-b", review_batch="kl-y")
    api.assign("rv-b", doc_ids=[P1S1])


# ── 도구 ─────────────────────────────────────────────────────────────────────


def independent_alias(salt_hex: str, doc_id: str) -> str:
    """별칭 식을 **구현과 다른 방법**으로 다시 계산한다 — RFC4648 base32 를 Crockford 글자로 옮긴다(앞 16자 = 앞 80비트)."""
    digest = hmac.new(bytes.fromhex(salt_hex), unicodedata.normalize("NFC", doc_id).encode("utf-8"),
                      hashlib.sha256).digest()
    std = base64.b32encode(digest).decode("ascii")
    table = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567", "0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    return "RV-" + std.translate(table)[:16]


def strings_outside_body(obj, skip_keys=("text", "final_grade")) -> list[str]:
    """응답 안의 모든 문자열(중첩 dict·list, 키 이름 포함). 본문(text)과 검수자 **자신의 결정 등급**(final_grade)은 뺀다 —
    앞의 것은 문서의 사실이고 뒤의 것은 검수자가 방금 정한 값이다."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            if k not in skip_keys:
                out += strings_outside_body(v, skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            out += strings_outside_body(v, skip_keys)
    elif isinstance(obj, str):
        out.append(obj)
    return out


def leaks_in(payload, pool_ids=ALL_IDS) -> list[str]:
    """본문 밖에서 실 doc_id(대소문자 무시)·등급 코드 토큰이 나오는 곳."""
    found: list[str] = []
    for s in strings_outside_body(payload):
        for real in pool_ids:
            if real.lower() in s.lower():
                found.append(f"실 doc_id {real!r} in {s[:60]!r}")
        m = CODE.search(s)
        if m:
            found.append(f"등급 코드 {m.group(0)!r} in {s[:60]!r}")
    return found


def collect_reviewer_responses(api: Api, who, docs_visible: list[str], *, decide: bool) -> dict[str, object]:
    """검수자가 닿는 후보 응답 전부 — 목록·필터 변형·상세·이력·decisions·summary·결정 응답. 경로에는 **별칭만** 쓴다."""
    out: dict[str, object] = {}

    def get(path: str, **kw):
        r = api.get(who, path, **kw)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        out[f"{len(out):02d} GET {path} {json.dumps(kw, ensure_ascii=False) if kw else ''}"] = r.json()
        return r.json()

    listed = get("/golden/candidates")
    aliases = {c["doc_id"] for c in listed["candidates"]}
    for status in ("proposed", "approved_proxy", "deferred", "discarded", "out_of_scope"):
        get("/golden/candidates", params={"status": status})
    get("/golden/candidates", params={"origin": "synthetic"})
    get("/golden/candidates", params={"origin": "public_real"})
    get("/golden/candidates", params={"review_batch": "kl-x"})
    for q in ("TS", "S1", "S2", "S3", "ts", "s3", "GOLD", "B1", "036", "0037", "UPL", "RPUB", "특급", "대외비", "real"):
        get("/golden/candidates", params={"query": q})
    get("/golden/candidates/summary")
    get("/golden/candidates/decisions")
    for alias in sorted(aliases):
        get(f"/golden/candidates/{alias}")
    if decide:
        for n, alias in enumerate(sorted(aliases)[:3]):
            body = ({"action": "change", "grade": ("S2", "S3", "TS")[n], "reason": "본문을 읽고 판단했습니다"}
                    if n < 2 else {"action": "defer", "reason": "출처 확인이 필요합니다"})
            r = api.post(who, f"/golden/candidates/{alias}/decision", json=body)
            assert r.status_code == 200, r.text
            out[f"{len(out):02d} POST {alias}"] = r.json()
        for alias in sorted(aliases):
            get(f"/golden/candidates/{alias}")           # 이력(decision_history)이 채워진 상세
        get("/golden/candidates/decisions")
        get("/golden/candidates")
        get("/golden/candidates/summary")
    assert len(aliases) == len(docs_visible), (sorted(aliases), docs_visible)
    return out


# ══ 1. 검수자의 어떤 응답에도 실 doc_id 와 등급 코드가 없다 ═════════════════════════════════════════════


@pytest.mark.parametrize("assignment", [True, False], ids=["assign-on", "assign-off"])
def test_no_real_doc_id_or_grade_code_reaches_a_blind_reviewer_outside_the_body(api, monkeypatch, assignment):
    """목록·필터 변형·query 변형·상세·이력·decisions·summary·결정 응답 전부를 재귀로 훑는다. 배정 강제가 꺼진 경우(전 문서가 보임)도."""
    standard_assignments(api)
    blind_on(monkeypatch, assignment=assignment)
    docs = ALL_IDS if not assignment else sorted(X_BATCH | {PTS})
    responses = collect_reviewer_responses(api, RV_A, docs, decide=True)
    checked = 0
    for name, payload in responses.items():
        assert leaks_in(payload) == [], f"{name}: {leaks_in(payload)[:3]}"
        checked += len(strings_outside_body(payload))
    assert len(responses) >= 40 and checked > 1500, f"훑은 양이 너무 적다 — 시험이 공허하다({len(responses)}건·{checked}개)"


def test_the_same_scan_finds_leaks_in_the_admin_view(api, monkeypatch):
    """이 훑기가 공허하지 않다는 증거 — 같은 응답을 관리자가 받으면 실 doc_id 와 등급 코드가 실제로 나온다."""
    blind_on(monkeypatch)
    listed = api.get(ADMIN, "/golden/candidates").json()
    found = leaks_in(listed)
    assert any("실 doc_id" in f for f in found) and any("등급 코드" in f for f in found), found[:5]


def test_manage_html_is_a_static_shell_without_any_doc_id(api, monkeypatch):
    """화면 틀에는 후보 데이터가 없다 — 실 doc_id 도 제목도 없고, 검수자마다·풀마다 같은 틀이다."""
    standard_assignments(api)
    blind_on(monkeypatch)
    html = api.get(RV_A, "/golden/candidates/manage.html").text
    assert "독립 검수 모드" in html and "별칭" in html
    for doc_id in ALL_IDS:
        assert doc_id not in html
    for _d, _l, _o, _b, title, _k in ALIAS_POOL:
        assert title not in html
    assert html == api.get(RV_B, "/golden/candidates/manage.html").text


def test_body_text_is_a_document_fact_and_is_never_touched(api, monkeypatch):
    """본문(text)에 적힌 등급 표기는 문서 자체의 사실이라 그대로 나간다(보고서에는 수치로 적는다)."""
    standard_assignments(api)
    blind_on(monkeypatch)
    alias = api.alias_table()[P1S1]
    d = api.get(RV_A, f"/golden/candidates/{alias}").json()
    assert d["text"] == BODY_PAIR1 and "S3로" in d["text"]
    assert d["document_sha256"] == hashlib.sha256(BODY_PAIR1.encode("utf-8")).hexdigest()


# ══ 2. 같은 본문 쌍 — doc_id 의 등급 코드가 응답에서 티가 나지 않는다 ═══════════════════════════════════════════


def _detail_modulo_alias(api: Api, who, doc_id: str, table: dict[str, str]) -> dict:
    alias = table[doc_id]
    text = api.get(who, f"/golden/candidates/{alias}").text
    assert alias in text
    return json.loads(text.replace(alias, "<ALIAS>"))


@pytest.mark.parametrize("a, b", [(P1S1, P1S3), (P2S1, P2NC)], ids=["S1-vs-S3-code", "S1-code-vs-no-code"])
def test_a_pair_of_documents_with_the_same_body_looks_the_same_whatever_their_doc_id_says(api, monkeypatch, a, b):
    """doc_id 에 S1 이 든 문서와 S3 가 든(또는 코드가 아예 없는) 문서가 본문·제목이 같으면, 별칭만 빼고 응답이 **같다.**"""
    blind_on(monkeypatch, assignment=False)
    table = api.alias_table()
    assert table[a] != table[b]
    assert _detail_modulo_alias(api, RV_A, a, table) == _detail_modulo_alias(api, RV_A, b, table)
    for who in (RV_A, RV_B):
        assert _detail_modulo_alias(api, who, a, table) == _detail_modulo_alias(api, who, b, table)


def test_titles_are_kept_unless_they_carry_a_grade_code_or_the_real_doc_id(api, monkeypatch):
    """깨끗한 제목은 검수자가 문서를 알아보는 이름이라 그대로 둔다. 자기 doc_id 가 박힌 자리는 별칭으로, 등급 코드(합성 후보는
    등급명 어휘까지)가 든 제목은 통째로 별칭으로 바꾼다. 실문서의 등급명('대외비')은 문서의 사실이라 그대로다."""
    blind_on(monkeypatch, assignment=False)
    table = api.alias_table()
    rows = {c["doc_id"]: c for c in api.get(RV_A, "/golden/candidates").json()["candidates"]}
    assert rows[table[P1S1]]["title"] == "공정 조건표 검토"
    assert rows[table[P2NC]]["title"] == "설비 점검 보고"
    assert rows[table[PTS]]["title"] == f"제목 {table[PTS]} 검토", "자기 doc_id 가 박힌 자리만 별칭으로"
    assert rows[table[PS2]]["title"] == table[PS2], "합성 후보의 등급명 어휘('특급기밀')는 제목 전체를 별칭으로"
    assert rows[table[PUPL]]["title"] == table[PUPL], "제목의 등급 코드(real-S3-…)도 제목 전체를 별칭으로"
    assert rows[table[PRPUB]]["title"] == "대외비 관리규정", "실문서에 찍힌 등급명은 문서의 사실"


# ══ 3. 별칭의 식·지속성·솔트 ═══════════════════════════════════════════════════════════════════════════


def test_alias_formula_is_hmac_sha256_of_the_nfc_doc_id_in_crockford_base32(api, monkeypatch):
    """식을 구현과 다른 방법으로 다시 계산해 대조한다. 솔트를 미리 심어 값이 고정된다."""
    api.salt_path.write_text(SALT_HEX + "\n", encoding="ascii")
    blind_on(monkeypatch)
    standard_assignments(api)
    table = api.alias_table()
    for doc_id in ALL_IDS:
        assert table[doc_id] == independent_alias(SALT_HEX, doc_id), doc_id
        assert re.fullmatch(r"RV-[0-9ABCDEFGHJKMNPQRSTVWXYZ]{16}", table[doc_id])
    seen = {c["doc_id"] for c in api.get(RV_A, "/golden/candidates").json()["candidates"]}
    assert seen == {table[d] for d in X_BATCH | {PTS}}, "검수자가 보는 번호 = 관리자가 대응시키는 번호"
    # 한글 doc_id 는 NFC/NFD 어느 쪽으로 들어와도 같은 별칭이다
    salt = bytes.fromhex(SALT_HEX)
    nfc, nfd = unicodedata.normalize("NFC", "GOLD-검토-1"), unicodedata.normalize("NFD", "GOLD-검토-1")
    assert nfc != nfd and access.alias_for(salt, nfc) == access.alias_for(salt, nfd)
    # 다른 솔트면 다른 별칭 — 솔트 없이는 별칭에서 doc_id 를 되짚을 수 없다
    assert access.alias_for(bytes(32), P1S1) != table[P1S1]


def test_aliases_survive_a_restart_and_the_salt_is_created_once(api, monkeypatch):
    """솔트 파일이 유일한 상태다 — 프로세스 안의 캐시를 다 비우고 새 클라이언트로 다시 물어도 같은 별칭이다."""
    blind_on(monkeypatch)
    assert not api.salt_path.exists(), "손잡이가 켜지기 전에는 솔트가 없다"
    before = api.alias_table()
    salt_bytes, salt_mtime = api.salt_path.read_bytes(), api.salt_path.stat().st_mtime_ns
    assert re.fullmatch(rb"[0-9a-f]{64}\n", salt_bytes), "무작위 32바이트 = 16진수 64자"
    pgs._CANDIDATE_CACHE.clear()                                    # '재시작'
    restarted = Api(TestClient(app), api.root)
    assert restarted.alias_table() == before
    assert api.salt_path.read_bytes() == salt_bytes and api.salt_path.stat().st_mtime_ns == salt_mtime, "솔트가 다시 쓰였다"
    assert access.load_or_create_salt(api.root) == bytes.fromhex(salt_bytes.decode().strip())
    stray = [p.name for p in api.root.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")]
    assert stray == [], f"임시 파일이 남았다: {stray}"


def test_salt_is_created_atomically_and_concurrent_creators_all_read_the_winner(tmp_path):
    """스레드 16개가 동시에 솔트를 만든다 — 파일은 하나뿐이고 모두 같은 값을 돌려받는다. 25번 반복한다."""
    for round_no in range(25):
        root = tmp_path / f"race{round_no}"
        root.mkdir()
        barrier = threading.Barrier(16)
        results: list[bytes | BaseException] = []

        def worker():
            try:
                barrier.wait()
                results.append(access.load_or_create_salt(root))
            except BaseException as exc:  # noqa: BLE001 — 실패도 수집해서 한꺼번에 본다
                results.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        errors = [r for r in results if isinstance(r, BaseException)]
        assert not errors, f"round {round_no}: {errors[:2]}"
        assert len(results) == 16 and len(set(results)) == 1, f"round {round_no}: 솔트가 갈렸다"
        assert sorted(p.name for p in root.iterdir()) == [access.ALIAS_SALT_NAME], "솔트 파일 하나만 남아야 한다"
        assert (root / access.ALIAS_SALT_NAME).read_bytes() == results[0].hex().encode() + b"\n"


def test_a_creator_that_loses_the_race_reads_the_winners_salt(tmp_path, monkeypatch):
    """경합에서 지는 순간을 결정적으로 만든다 — 붙이려는 순간 다른 쪽이 이미 다른 솔트를 붙여 둔 상태."""
    winner = "ab" * 32
    real_link = access.os.link

    def racing_link(src, dst, *a, **kw):
        Path(dst).write_text(winner + "\n", encoding="ascii")        # 경합 상대가 방금 붙임
        return real_link(src, dst, *a, **kw)                         # 이제 FileExistsError

    monkeypatch.setattr(access.os, "link", racing_link)
    assert access.load_or_create_salt(tmp_path) == bytes.fromhex(winner)
    assert [p.name for p in tmp_path.iterdir()] == [access.ALIAS_SALT_NAME], "진 쪽의 임시 파일이 남았다"


def test_salt_creation_falls_back_when_hard_links_are_unavailable(tmp_path, monkeypatch):
    def no_link(*a, **kw):
        raise OSError(95, "hard links are not supported here")

    monkeypatch.setattr(access.os, "link", no_link)
    salt = access.load_or_create_salt(tmp_path)
    assert len(salt) == 32 and access.load_or_create_salt(tmp_path) == salt
    assert [p.name for p in tmp_path.iterdir()] == [access.ALIAS_SALT_NAME]


# ══ 4. 별칭으로 상세·결정이 되고 원장에는 실 doc_id 가 남는다 ═══════════════════════════════════════════════


def test_detail_and_decision_work_by_alias_and_the_ledger_keeps_the_real_doc_id(api, monkeypatch):
    standard_assignments(api)
    blind_on(monkeypatch)
    table = api.alias_table()
    files_before = {p.name: p.read_bytes() for p in api.root.iterdir() if p.is_file()}
    alias = table[P1S1]

    detail = api.get(RV_A, f"/golden/candidates/{alias}")
    assert detail.status_code == 200 and detail.json()["doc_id"] == alias

    resp = api.post(RV_A, f"/golden/candidates/{alias}/decision",
                    json={"action": "change", "grade": "S2", "reason": "본문을 읽고 판단했습니다",
                          "security_marking": "confidential"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["doc_id"] == alias and body["final_grade"] == "S2"
    assert body["latest_decision"]["doc_id"] == alias

    # 원장 — 실 doc_id, 종전과 같은 키(형식을 바꾸지 않았다)
    (line,) = api.decision_lines()
    event = json.loads(line)
    assert event["doc_id"] == P1S1 and event["actor_id"] == "rv-a" and event["final_grade"] == "S2"
    assert set(event) == {
        "schema_version", "event_id", "doc_id", "action", "status", "proposed_grade", "final_grade", "reason",
        "actor_id", "decided_at", "document_sha256", "document_origin", "claim_scope", "provenance_at_decision",
        "management_before", "management_after"}
    assert alias not in line

    # 검수자의 이력·결정 원장 화면·목록 행 어디서도 같은 별칭
    d2 = api.get(RV_A, f"/golden/candidates/{alias}").json()
    assert [e["doc_id"] for e in d2["decision_history"]] == [alias] and d2["latest_decision"]["doc_id"] == alias
    ev = api.get(RV_A, "/golden/candidates/decisions").json()
    assert [e["doc_id"] for e in ev["events"]] == [alias]
    row = next(c for c in api.get(RV_A, "/golden/candidates").json()["candidates"] if c["doc_id"] == alias)
    assert row["final_grade"] == "S2" and row["latest_decision"]["doc_id"] == alias

    # 관리자는 실 doc_id 로 같은 결정을 본다(별칭을 경로에 넣지는 못한다 — 관리자 시야는 종전 그대로)
    adm = api.get(ADMIN, f"/golden/candidates/{P1S1}").json()
    assert adm["doc_id"] == P1S1 and adm["latest_decision"]["doc_id"] == P1S1 and adm["reviewer_alias"] == alias
    assert api.get(ADMIN, f"/golden/candidates/{alias}").status_code == 404

    # 후보 파일은 M 을 준 그 문서의 메타데이터 하나만 바뀌었다 + 새 파일은 솔트·원장·잠금뿐
    files_after = {p.name: p.read_bytes() for p in api.root.iterdir() if p.is_file()}
    changed = {n for n in files_before if files_after.get(n) != files_before[n]}
    assert changed == {f"{P1S1}.metadata.json"}, changed
    assert set(files_after) - set(files_before) == {pgs._LEDGER_NAME, pgs._LEDGER_NAME + ".lock"}


# ══ 5. 실 doc_id 는 경로에서 없는 문서와 같은 404 다 ═══════════════════════════════════════════════════════


def test_a_real_doc_id_in_the_path_is_the_same_404_as_a_missing_document(api, monkeypatch):
    standard_assignments(api)
    blind_on(monkeypatch)
    table = api.alias_table()
    missing = api.get(RV_A, "/golden/candidates/RV-0000000000000000")
    assert missing.status_code == 404
    body = {"action": "change", "grade": "S2", "reason": "본문을 읽고 판단했습니다"}
    missing_post = api.post(RV_A, "/golden/candidates/RV-0000000000000000/decision", json=body)
    assert missing_post.status_code == 404

    visible_real = [P1S1, P2NC, PTS]                               # rv-a 에게 배정된 문서
    foreign = [PS2, PUPL, PRPUB]                                   # 배정 밖 문서
    unassigned_alias = [table[d] for d in foreign]                 # 배정 밖 문서의 별칭
    lower = [table[P1S1].lower()]                                  # 철자가 다른 별칭도 없는 문서
    before = api.decision_lines()
    for spelling in visible_real + foreign + unassigned_alias + lower + [P1S1.lower(), P1S1 + "_문서"]:
        r = api.get(RV_A, f"/golden/candidates/{spelling}")
        assert (r.status_code, r.json()) == (missing.status_code, missing.json()), spelling
        p = api.post(RV_A, f"/golden/candidates/{spelling}/decision", json=body)
        assert (p.status_code, p.json()) == (missing_post.status_code, missing_post.json()), spelling
    assert api.decision_lines() == before, "거절된 결정이 원장에 남았다"
    # 배정된 문서의 별칭은 열린다
    assert api.get(RV_A, f"/golden/candidates/{table[P1S1]}").status_code == 200
    # 배정 강제가 꺼진 경우에도 실 doc_id 는 닫혀 있고 별칭은 전 문서에 통한다
    blind_on(monkeypatch, assignment=False)
    assert api.get(RV_A, f"/golden/candidates/{PS2}").status_code == 404
    assert api.get(RV_A, f"/golden/candidates/{table[PS2]}").status_code == 200


def test_two_reviewers_share_the_same_alias_for_a_document_but_never_see_each_others_decisions(api, monkeypatch):
    """별칭은 문서마다 하나(솔트는 풀 전체의 것)다. 같은 문서를 배정받은 두 검수자의 독립성은 별칭이 같아도 그대로다."""
    standard_assignments(api)
    blind_on(monkeypatch)
    alias = api.alias_table()[P1S1]                                # rv-a·rv-b 가 함께 배정받은 문서
    ra = api.get(RV_A, f"/golden/candidates/{alias}").json()
    rb = api.get(RV_B, f"/golden/candidates/{alias}").json()
    assert ra["doc_id"] == rb["doc_id"] == alias
    r = api.post(RV_A, f"/golden/candidates/{alias}/decision",
                 json={"action": "change", "grade": "S2", "reason": "SENTINEL-A 판단"})
    assert r.status_code == 200
    b_after = api.get(RV_B, f"/golden/candidates/{alias}").json()
    assert (b_after["status"], b_after["final_grade"], b_after["latest_decision"]) == ("proposed", None, None)
    assert "SENTINEL-A" not in json.dumps(api.get(RV_B, "/golden/candidates").json(), ensure_ascii=False)
    assert api.get(RV_B, "/golden/candidates/decisions").json() == {"total": 0, "by_action": {}, "events": []}
    a_now = api.get(RV_A, f"/golden/candidates/{alias}").json()
    assert a_now["final_grade"] == "S2" and a_now["latest_decision"]["doc_id"] == alias
    r2 = api.post(RV_B, f"/golden/candidates/{alias}/decision",
                  json={"action": "change", "grade": "TS", "reason": "SENTINEL-B 판단"})
    assert r2.status_code == 200 and r2.json()["doc_id"] == alias
    assert [json.loads(x)["doc_id"] for x in api.decision_lines()] == [P1S1, P1S1]


# ══ 6. 검색(query)은 화면에 나가는 값에만 닿는다 · 목록 순서는 별칭 순이다 ═══════════════════════════════════


def test_query_matches_only_the_alias_prefix_and_can_not_dig_grade_codes(api, monkeypatch):
    """종전 query 는 doc_id(등급 코드가 든다)·title 에 부분 일치해서 query=TS 가 GOLD-P-TS-001 을 돌려줬다. 후보 풀 측정에서 등급 코드가
    나오는 필드가 doc_id(988건)·title(1건)이라 검수자의 query 는 **둘 다 뺐다** — 검색어는 화면에 나가는 별칭의 앞부분('RV-…')
    에만 닿는다. 등급 코드·낱말·doc_id 조각·제목 낱말로는 아무것도 못 찾으니, 결과 집합에서 등급을 읽어 낼 수 없다.
    (솔트가 무엇이든 결과가 같다 — 별칭 안의 글자가 우연히 'TS' 를 이루어도 'RV-' 로 시작하지 않는 검색어는 별칭에 닿지 않는다.)"""
    api.salt_path.write_text(SALT_HEX + "\n", encoding="ascii")
    blind_on(monkeypatch, assignment=False)
    table = api.alias_table()

    def got(q: str) -> set[str]:
        r = api.get(RV_A, "/golden/candidates", params={"query": q})
        assert r.status_code == 200
        return {c["doc_id"] for c in r.json()["candidates"]}

    probes = ["TS", "S1", "S2", "S3", "ts", "s3", "-TS-", "GOLD", "B1-S1", "GOLD-P", "036", "0037", "kl", "NC", "UPL",
              "특급", "대외비", "real", "ksensor", "공정", "설비", "검토", "관리규정", "RPUB"]
    for q in probes:
        assert got(q) == set(), f"query={q}: 등급 코드·제목 낱말로 무언가 찾아졌다"
    for real in ALL_IDS:                                                # 실 doc_id 전체·조각
        assert got(real) == set(), real
        assert got(real[:10]) == set(), real[:10]
    alias = table[P1S1]
    assert got(alias[3:]) == set(), "'RV-' 없이 별칭 안의 글자로 찾을 수는 없다"
    # 별칭으로 찾는 것은 된다 — 전체·앞부분·대소문자 무시
    assert got(alias) == {alias} and got(alias.lower()) == {alias} and alias in got(alias[:8])
    assert got("RV-") == {table[d] for d in ALL_IDS} and got("rv-") == got("RV-")
    # 종전(꺼진 상태)에는 같은 질의가 등급 코드를 그대로 캔다 — 이 시험이 공허하지 않다는 증거
    set_flags(monkeypatch)
    plain = {c["doc_id"] for c in api.get(RV_A, "/golden/candidates", params={"query": "TS"}).json()["candidates"]}
    assert PTS in plain and P1S1 not in plain
    # 관리자는 종전 규칙 그대로(doc_id·제목 부분 일치)
    blind_on(monkeypatch, assignment=False)
    adm = {c["doc_id"] for c in api.get(ADMIN, "/golden/candidates", params={"query": "TS"}).json()["candidates"]}
    assert adm == {PTS}


def _order_pool_rows():
    grades = ("S1", "S2", "S3", "TS")
    return [(f"GOLD-B1-{g}-{n:03d}", g, "synthetic", "kl-x", f"문서 {g}{n}", "B2")
            for g in grades for n in range(1, 13)]


def test_list_order_is_by_alias_so_the_position_no_longer_tells_the_grade(tmp_path, monkeypatch):
    """종전 목록은 실 doc_id 순이었다 — 등급 코드가 든 id 는 같은 등급끼리 뭉쳐 줄 위치만으로 등급이 갈렸다(실제 풀에서 이웃이
    같은 제안 등급인 비율 0.9606, 라벨을 섞으면 0.2608). 별칭 순이면 그 상관이 없어진다(솔트를 심어 결과가 고정된다)."""
    rows = _order_pool_rows()
    make_alias_pool(tmp_path, rows)
    (tmp_path / access.ALIAS_SALT_NAME).write_text(SALT_HEX + "\n", encoding="ascii")
    monkeypatch.setattr(pgs, "_DEFAULT_ROOT", tmp_path)
    pgs._CANDIDATE_CACHE.clear()
    set_flags(monkeypatch, assignment=False, blind=True)
    api = Api(TestClient(app), tmp_path)
    try:
        label = {r[0]: r[1] for r in rows}
        admin_order = [c["doc_id"] for c in api.get(ADMIN, "/golden/candidates").json()["candidates"]]
        assert admin_order == sorted(label), "관리자 목록은 종전대로 실 doc_id 순"
        table = {c["doc_id"]: c["reviewer_alias"] for c in api.get(ADMIN, "/golden/candidates").json()["candidates"]}
        real_of = {a: r for r, a in table.items()}
        blind_order = [c["doc_id"] for c in api.get(RV_A, "/golden/candidates").json()["candidates"]]
        assert blind_order == sorted(table.values()), "검수자 목록은 별칭 순"

        def adjacent_same(seq):
            g = [label[d] for d in seq]
            return sum(1 for x, y in zip(g, g[1:]) if x == y) / (len(g) - 1)

        assert adjacent_same(admin_order) > 0.9, "이 시험이 공허하지 않다는 증거: 실 doc_id 순에서는 등급이 뭉친다"
        assert adjacent_same([real_of[a] for a in blind_order]) < 0.55, "별칭 순인데도 등급이 뭉쳐 있다"
    finally:
        app.dependency_overrides.pop(require_auth, None)
        pgs._CANDIDATE_CACHE.clear()


# ══ 7. fail-closed — 별칭 계층을 쓸 수 없으면 숨김 검수자의 요청을 거절한다 ══════════════════════════════════════


REVIEWER_CALLS = [
    ("get", "/golden/candidates", None),
    ("get", "/golden/candidates/summary", None),
    ("get", "/golden/candidates/decisions", None),
    ("get", "/golden/candidates/RV-ABCDEFGHJKMNPQRS", None),
    ("post", "/golden/candidates/RV-ABCDEFGHJKMNPQRS/decision",
     {"action": "change", "grade": "S2", "reason": "본문을 읽고 판단했습니다"}),
]


def _all_refused(api: Api, who=RV_A) -> None:
    before = api.decision_lines()
    for method, path, body in REVIEWER_CALLS:
        r = getattr(api, method)(who, path, **({"json": body} if body else {}))
        assert r.status_code == 503, (path, r.status_code, r.text[:200])
        assert set(r.json()) == {"detail"} and "alias" in r.json()["detail"], r.text
        assert not leaks_in(r.json()) and "GOLD" not in r.text
    assert api.decision_lines() == before


def test_alias_collision_in_the_pool_closes_blind_review_and_is_logged(api, monkeypatch, caplog):
    """풀 안에서 두 문서의 별칭이 같으면 어느 쪽인지 가를 수 없다 — 조용히 넘기지 않고 숨김 검수자의 요청을 거절한다.
    (검수자에게 안 보이는 문서와 겹쳐도 마찬가지다: 충돌은 풀 전체를 기준으로 본다.)"""
    standard_assignments(api)
    blind_on(monkeypatch)
    real_alias = access.alias_for

    def clashing(salt, doc_id):
        return real_alias(salt, P1S1 if doc_id == PRPUB else doc_id)      # PRPUB 는 rv-a 에게 안 보이는 문서

    monkeypatch.setattr(access, "alias_for", clashing)
    with caplog.at_level("ERROR"):
        _all_refused(api)
        _all_refused(api, RV_B)
    assert any("별칭 계층" in r.message and "충돌" in r.message for r in caplog.records), "충돌을 로그에 남기지 않았다"
    # 관리자는 화면을 잃지 않는다 — 목록·결정은 종전대로 되고 매핑 키만 비어 있다
    rows = api.get(ADMIN, "/golden/candidates").json()["candidates"]
    assert len(rows) == 8 and all(c["reviewer_alias"] is None for c in rows)
    assert api.post(ADMIN, f"/golden/candidates/{P1S1}/decision", json={"action": "approve"}).status_code == 200
    # 손잡이를 끄면 검수자는 종전대로 쓴다(별칭 계층은 숨김 켜진 동안의 것)
    set_flags(monkeypatch)
    assert api.get(RV_A, "/golden/candidates").status_code == 200


@pytest.mark.parametrize("damage", ["garbage", "directory", "cannot_create", "half_written"])
def test_unreadable_or_uncreatable_salt_closes_blind_review(api, monkeypatch, caplog, damage):
    standard_assignments(api)
    blind_on(monkeypatch)
    monkeypatch.setattr(access, "_SALT_WAIT_SECONDS", 0.1)
    if damage == "garbage":
        api.salt_path.write_text("이건 솔트가 아니다\n", encoding="utf-8")
    elif damage == "directory":
        api.salt_path.mkdir()
    elif damage == "half_written":
        api.salt_path.write_text("0f1e2d\n", encoding="ascii")            # 쓰다 멈춘 파일
    else:
        real_open, real_link = access.os.open, access.os.link

        def refuse_open(path, *a, **kw):        # 솔트 파일만 못 만든다(읽기 전용 마운트) — 다른 파일 열기는 그대로
            if access.ALIAS_SALT_NAME in str(path):
                raise PermissionError(13, "read-only file system")
            return real_open(path, *a, **kw)

        def refuse_link(src, dst, *a, **kw):
            if access.ALIAS_SALT_NAME in str(dst):
                raise PermissionError(13, "read-only file system")
            return real_link(src, dst, *a, **kw)

        monkeypatch.setattr(access.os, "open", refuse_open)
        monkeypatch.setattr(access.os, "link", refuse_link)
    with caplog.at_level("ERROR"):
        _all_refused(api)
    assert any("별칭 계층" in r.message for r in caplog.records)
    if damage == "garbage":
        assert api.salt_path.read_text(encoding="utf-8") == "이건 솔트가 아니다\n", (
            "형식이 틀린 솔트를 조용히 덮어썼다 — 별칭이 통째로 바뀐다")


def test_a_missing_candidate_folder_needs_no_salt_and_is_not_created(tmp_path, monkeypatch):
    """풀이 아직 없으면 별칭을 붙일 문서가 없다 — 읽기 요청이 폴더를 만들지 않는다."""
    gone = tmp_path / "not-yet"
    monkeypatch.setattr(pgs, "_DEFAULT_ROOT", gone)
    set_flags(monkeypatch, assignment=False, blind=True)
    api = Api(TestClient(app), gone)
    try:
        assert api.get(RV_A, "/golden/candidates").json()["candidates"] == []
        assert api.get(RV_A, "/golden/candidates/RV-ABCDEFGHJKMNPQRS").status_code == 404
        assert not gone.exists()
    finally:
        app.dependency_overrides.pop(require_auth, None)


# ══ 8. 손잡이를 끄면·관리자 시야는 종전과 같다 ═══════════════════════════════════════════════════════════════


def _json(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))


def _service_views(root: Path) -> dict[str, object]:
    """종전 코드 경로가 돌려주던 값 — 서비스를 직접 불러 만든다(라우트·별칭 계층을 거치지 않는다)."""
    svc = ProxyGoldCandidateService(root)
    out = {
        "/golden/candidates": svc.list_candidates(),
        "/golden/candidates?status=proposed": svc.list_candidates(status="proposed"),
        "/golden/candidates?query=TS": svc.list_candidates(query="TS"),
        "/golden/candidates/summary": svc.summary(),
        "/golden/candidates/decisions": svc.recent_decisions(limit=100),
    }
    for doc_id in ALL_IDS:
        out[f"/golden/candidates/{doc_id}"] = svc.get_candidate(doc_id)
    return _json(out)


def test_with_the_blind_flag_off_every_role_gets_the_pre_change_response_and_no_salt_appears(api, monkeypatch):
    """손잡이가 꺼진(또는 배정만 켜진) 응답은 종전과 같다 — 실 doc_id·reviewer_alias 없음·정렬 그대로·솔트 파일도 안 생긴다.
    기준은 서비스를 직접 불러 만든 값(종전 코드 경로가 돌려주던 것)이라 라우트·별칭 계층과 독립이다."""
    standard_assignments(api)
    api.post(ADMIN, f"/golden/candidates/{P1S1}/decision", json={"action": "approve"})
    api.post(ADMIN, f"/golden/candidates/{PTS}/decision",
             json={"action": "change", "grade": "S2", "reason": "관리자 판단", "security_marking": "secret"})
    expected = _service_views(api.root)
    for flags in (dict(), dict(assignment=True)):
        set_flags(monkeypatch, **flags)
        # 배정이 걸린 검수자는 일부만 본다 — 별칭과 무관한 종전 동작이라 배정이 꺼진 경우에만 검수자도 비교한다
        for who in (ADMIN, KL_BACKEND, SYSTEM) + ((RV_A,) if not flags else ()):
            for path, want in expected.items():
                r = api.get(who, path)
                assert r.status_code == 200, (who, flags, path)
                assert r.json() == want, (who, flags, path)
                assert access.ADMIN_ALIAS_KEY not in r.text
    assert not api.salt_path.exists(), "손잡이가 꺼진 응답이 솔트를 만들었다"
    # 종전 모양 그대로 — 검수자도 실 doc_id 와 제안 등급을 본다
    set_flags(monkeypatch)
    d = api.get(RV_A, f"/golden/candidates/{P1S1}").json()
    assert d["doc_id"] == P1S1 and d["proposed_grade"] == "S1" and access.ADMIN_ALIAS_KEY not in d
    assert api.post(RV_A, f"/golden/candidates/{P2S1}/decision", json={"action": "approve"}).json()["doc_id"] == P2S1
    assert not api.salt_path.exists()


@pytest.mark.parametrize("who", [ADMIN, KL_BACKEND, SYSTEM])
def test_privileged_roles_get_the_pre_change_response_plus_only_the_mapping_key_while_blind_is_on(api, monkeypatch, who):
    """관리자·kl_backend·system 의 응답은 종전과 같고, 숨김이 켜진 동안 후보 행·상세에 reviewer_alias 키 하나만 더해진다.
    그 값은 검수자가 화면에서 보는 번호다. 요약·결정 원장 화면은 그대로다."""
    standard_assignments(api)
    api.post(ADMIN, f"/golden/candidates/{P1S1}/decision", json={"action": "approve"})
    expected = _service_views(api.root)
    blind_on(monkeypatch)
    table = api.alias_table()
    assert len(set(table.values())) == len(ALL_IDS), "별칭이 문서마다 하나씩이어야 한다"
    seen_by_reviewer = {c["doc_id"] for c in api.get(RV_A, "/golden/candidates").json()["candidates"]}
    assert seen_by_reviewer == {table[d] for d in X_BATCH | {PTS}}
    for path, want in expected.items():
        r = api.get(who, path)
        assert r.status_code == 200, path
        got = r.json()
        if path in ("/golden/candidates/summary", "/golden/candidates/decisions"):
            assert access.ADMIN_ALIAS_KEY not in r.text, path
        elif path.startswith("/golden/candidates?") or path == "/golden/candidates":       # 목록 — 행마다 키가 더해진다
            for row in got["candidates"]:
                assert row.pop(access.ADMIN_ALIAS_KEY) == table[row["doc_id"]], path
        else:                                                                             # 상세
            assert got.pop(access.ADMIN_ALIAS_KEY) == table[want["doc_id"]], path
        assert got == want, f"{who} {path}: 매핑 키를 빼면 종전 응답과 같아야 한다"
    # 결정 응답(관리자)은 종전 모양 그대로다 — 키를 더하지 않는다
    if who != SYSTEM:                                                # system 은 결정 API 에 닿지 않는다(종전과 같다)
        r = api.post(who, f"/golden/candidates/{P2S1}/decision", json={"action": "approve"})
        assert r.status_code == 200 and set(r.json()) == {"doc_id", "status", "final_grade", "latest_decision"}
        assert r.json()["doc_id"] == P2S1 and r.json()["latest_decision"]["doc_id"] == P2S1


# ══ 9. 화면(manage.html) — 서버가 내려준 별칭을 그대로 표시하고 그것으로 요청한다 ═══════════════════════════════


_DOM_PROBE = r'''
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(process.argv[3]);
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(process.argv[2], 'utf8');
const fx = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const errors = [], urls = [], posts = [], badUrls = [];
const json = (body, status = 200) => Promise.resolve({ ok: status < 400, status,
  json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
const fetchStub = (url, opt = {}) => {
  const u = String(url);
  urls.push((opt.method || 'GET') + ' ' + u);
  if (fx.realIds.some(r => u.includes(r))) badUrls.push(u);            // 실 doc_id 로 요청하면 잡는다
  if (opt.method === 'POST') { posts.push({ url: u, body: JSON.parse(opt.body) }); return json(fx.decisionResponse); }
  if (u.endsWith('/session')) return json(fx.session);
  if (u.includes('/decisions')) return json(fx.decisions);
  if (u.endsWith('/candidates/' + fx.alias)) return json(fx.detail);
  if (u.includes('/candidates/')) return json({ detail: 'proxy-gold candidate not found' }, 404);
  return json(fx.list);
};
const dom = new JSDOM(html, {
  runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost:8000/api/v1/golden/candidates/manage.html',
  beforeParse(w) {
    w.fetch = fetchStub; w.HTMLElement.prototype.scrollIntoView = () => {};
    w.addEventListener('error', e => errors.push('onerror: ' + e.message));
    w.addEventListener('unhandledrejection', e => errors.push('rejection: ' + e.reason));
  },
});
const w = dom.window, d = w.document;
const tick = (ms = 60) => new Promise(r => setTimeout(r, ms));
await tick(300);
const out = {};
out.rowIds = [...d.querySelectorAll('#rows .docid')].map(e => e.textContent);
out.rowTitles = [...d.querySelectorAll('#rows .doctitle')].map(e => e.textContent);
[...d.querySelectorAll('#rows .candidate')].find(b => b.querySelector('.docid').textContent === fx.alias).click();
await tick(300);
out.detailTitle = d.getElementById('detailTitle').textContent;
out.selectedRow = [...d.querySelectorAll('#rows .candidate.selected .docid')].map(e => e.textContent);
d.getElementById('action').value = 'change';
d.getElementById('action').dispatchEvent(new w.Event('change'));
d.getElementById('finalGrade').value = 'S2';
d.getElementById('reason').value = '본문을 읽고 판단했습니다';
d.getElementById('save').click();
await tick(400);
out.saveMsg = d.getElementById('saveMsg').textContent;
out.ledgerIds = [...d.querySelectorAll('#ledgerRows .docid')].map(e => e.textContent);
if (d.querySelector('#ledgerRows .candidate')) { d.querySelector('#ledgerRows .candidate').click(); await tick(300); }
out.urls = urls; out.posts = posts; out.badUrls = badUrls; out.errors = errors;
console.log(JSON.stringify(out));
w.close();
'''


def _jsdom_package_json():
    p = Path(__file__).resolve().parent / "e2e_console" / "package.json"
    return p if (p.parent / "node_modules" / "jsdom").is_dir() else None


def test_manage_html_lists_opens_and_saves_by_alias_in_a_real_dom(api, monkeypatch, tmp_path):
    """화면 JS 가 doc_id 를 다루는 자리(목록 행·선택 표시·상세 조회·결정 저장·결정 이력 이동)가 전부 응답에서 받은 값만 쓴다는 것을
    jsdom 으로 확인한다. 서버 응답은 **이 서버가 실제로 낸 별칭 응답**을 그대로 심는다(손으로 만든 값이 아니다).
    node 나 jsdom 이 없으면 이 시험만 건너뛴다."""
    node, pkg = shutil.which("node"), _jsdom_package_json()
    if not node or pkg is None:
        pytest.skip("node 또는 tests/e2e_console/node_modules/jsdom 이 없다 — 이 시험만 건너뜀")
    standard_assignments(api)
    blind_on(monkeypatch)
    table = api.alias_table()
    alias = table[P1S1]
    listed = api.get(RV_A, "/golden/candidates").json()
    detail = api.get(RV_A, f"/golden/candidates/{alias}").json()
    real_post = api.post(RV_A, f"/golden/candidates/{alias}/decision",
                         json={"action": "change", "grade": "S2", "reason": "본문을 읽고 판단했습니다"})
    assert real_post.status_code == 200
    fixture = {
        "alias": alias, "realIds": ALL_IDS, "list": listed, "detail": detail,
        "decisions": api.get(RV_A, "/golden/candidates/decisions").json(),
        "decisionResponse": real_post.json(),
        "session": {"actor_id": "rv-a", "auth_mode": "jwt", "actor_role": "reviewer"},
    }
    html_path, js_path, fx_path = tmp_path / "manage.html", tmp_path / "probe.mjs", tmp_path / "fx.json"
    html_path.write_text(api.get(RV_A, "/golden/candidates/manage.html").text, encoding="utf-8")
    js_path.write_text(_DOM_PROBE, encoding="utf-8")
    fx_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    run = subprocess.run([node, str(js_path), str(html_path), str(pkg), str(fx_path)],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert run.returncode == 0, run.stderr[-800:]
    out = json.loads(run.stdout)
    assert out["errors"] == [], out["errors"]
    assert out["badUrls"] == [], "화면이 실 doc_id 로 요청을 보냈다"
    assert set(out["rowIds"]) == {c["doc_id"] for c in listed["candidates"]} and all(i.startswith("RV-") for i in out["rowIds"])
    assert out["detailTitle"].startswith(alias + " · ") and out["selectedRow"] == [alias]
    assert out["posts"] == [{"url": f"/api/v1/golden/candidates/{alias}/decision",
                             "body": {"action": "change", "reason": "본문을 읽고 판단했습니다", "grade": "S2"}}]
    assert "저장했습니다" in out["saveMsg"] or "저장 완료" in out["saveMsg"], out["saveMsg"]
    assert out["ledgerIds"] and all(i.startswith("RV-") for i in out["ledgerIds"])
    doc_urls = [u for u in out["urls"] if "/candidates/" in u and not u.endswith(("/session", "/manage.html"))
                and "/decisions" not in u]
    assert doc_urls and all(alias in u for u in doc_urls), doc_urls


# ══ 10. 이 시험이 정말 계층을 잠그는가 — 계층을 끄면 탐침이 새는 것을 잡아야 한다 ═══════════════════════════════════


def alias_leaks(api: Api, monkeypatch) -> list[str]:
    """숨김을 켜고 rv-a 로 **찔러 본다.** 새는 것마다 한 줄을 돌려준다. 어느 철자(별칭·실 doc_id)로든 열리면 새는 것이다."""
    standard_assignments(api)
    blind_on(monkeypatch)
    table = api.alias_table()
    leaks: list[str] = []
    listed = api.get(RV_A, "/golden/candidates")
    if listed.status_code != 200:
        return [f"목록이 {listed.status_code} 로 거절됐다(누출이 아니라 고장): {listed.text[:80]}"]
    payloads = {"list": listed.json()}
    for spelling in (P1S1, table[P1S1]):
        r = api.get(RV_A, f"/golden/candidates/{spelling}")
        if spelling == P1S1 and r.status_code != 404:
            leaks.append("실 doc_id 를 경로에 넣으면 상세가 열린다")
        if r.status_code == 200:
            payloads[f"detail:{spelling}"] = r.json()
    w = api.post(RV_A, f"/golden/candidates/{P2NC}/decision", json={"action": "change", "grade": "S2", "reason": "찔러봄"})
    if w.status_code != 404:
        leaks.append("실 doc_id 를 경로에 넣으면 결정이 기록된다")
    ok = api.post(RV_A, f"/golden/candidates/{table[P2NC]}/decision", json={"action": "change", "grade": "S2", "reason": "판단"})
    if ok.status_code == 200:
        payloads["decision"] = ok.json()
    payloads["decisions"] = api.get(RV_A, "/golden/candidates/decisions").json()
    for q in ("TS", "S1", "GOLD"):
        payloads[f"query={q}"] = api.get(RV_A, "/golden/candidates", params={"query": q}).json()
    for name, payload in payloads.items():
        for f in leaks_in(payload)[:2]:
            leaks.append(f"{name}: {f}")
    order = [c["doc_id"] for c in payloads["list"]["candidates"]]
    if order != sorted(order):
        leaks.append("목록이 별칭 순이 아니다")
    return list(dict.fromkeys(leaks))


def test_probe_finds_nothing_when_the_alias_layer_is_on(api, monkeypatch):
    assert alias_leaks(api, monkeypatch) == []


def test_probe_detects_a_disabled_alias_layer(api, monkeypatch):
    """계층을 monkeypatch 로 끄면(별칭 = 실 doc_id, 응답의 별칭 모양 관문도 열림, 검색 규칙도 종전으로) 같은 탐침이 새는 것을 잡는다 — 잡지 못하면
    이 파일의 다른 시험은 계층을 잠그지 못한 것이다(무력화해도 초록불)."""
    monkeypatch.setattr(access, "alias_for", lambda salt, doc_id: doc_id)
    monkeypatch.setattr(access, "is_alias", lambda value: True)
    # 검색 규칙도 종전(doc_id·title 부분 일치)으로 되돌린다 — 검색어가 등급 코드를 캐던 시절
    monkeypatch.setattr(access, "blind_query_match",
                        lambda row, needle: needle in row["doc_id"].lower() or needle in row["title"].lower())
    leaks = alias_leaks(api, monkeypatch)
    for expected in ("실 doc_id 를 경로에 넣으면 상세가 열린다", "실 doc_id 를 경로에 넣으면 결정이 기록된다"):
        assert expected in leaks, (expected, leaks)
    assert any(x.startswith("list: 실 doc_id") for x in leaks) and any("등급 코드" in x for x in leaks), leaks
    assert any(x.startswith("decisions: 실 doc_id") for x in leaks) and any(x.startswith("query=TS") for x in leaks), leaks


def test_missing_salt_is_recreated_but_never_silently(tmp_path, caplog):
    """솔트가 없어 새로 만들 때 경고가 남는다 — 이미 별칭이 발급된 뒤 솔트가 사라졌다면 별칭이 전부 바뀌는 사건이다.

    (독립 검증 2026-09-22: 결정 원장에 검수자 이벤트가 있는 상태에서 솔트를 지우면 로그 경고 없이 별칭이 바뀌었다.)
    첫 생성과 사고를 파일만으로는 가를 수 없어 둘 다 경고한다. 두 번째 호출(솔트가 이미 있음)은 경고하지 않는다.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger=access.logger.name):
        first = access.load_or_create_salt(tmp_path)
    assert (tmp_path / access.ALIAS_SALT_NAME).exists()
    assert any("솔트가 없어 새로 만들었다" in r.getMessage() for r in caplog.records), caplog.records

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=access.logger.name):
        second = access.load_or_create_salt(tmp_path)
    assert second == first, "이미 있는 솔트는 그대로 읽어야 한다"
    assert not caplog.records, "솔트가 이미 있으면 경고하지 않는다"
