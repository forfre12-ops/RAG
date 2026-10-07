"""골든셋 콘솔의 관리자 전용 「검수 배정」 패널 (2026-09-22).

왜. 검수자별 배정 API(POST/GET /golden/assignments · POST /golden/assignments/revoke)는 있는데 관리자가 배정하려면 API 를 직접
불러야 했다. manage.html 에 관리자·kl_backend 에게만 보이는 패널을 더했다 — 격리 상태(읽기 전용) · 현재 배정 표 · 배정 폼.
새 라우트는 없다. 이 파일이 잠그는 것:

  (가) 서버 렌더링 — 관리자·kl_backend 화면에는 패널이 있고, reviewer 화면(숨김 끔·켬)에는 **아무것도 없다**(마크업·스크립트·
       관리자 API 주소 전부). CSS 로 숨기는 것이 아니라 검수자용 변환이 패널 조각을 통째로 걷어낸다. 걷은 뒤에도 흔적이 남으면
       화면을 내주지 않는다(fail-closed).
  (나) 화면 동작(jsdom) — 표 그리기 · 배치 배정 · 문서 목록 배정 · 해제 · 422 오류 표시 · 빈 상태 · 403 · 네트워크 오류 · 401 ·
       요청 주소·방법·본문 · 배정한 관리자 ID 를 싣지 않음 · XSS·유니코드 정규화(NFC/NFD). 서버 응답은 **이 서버가 실제로 낸
       응답**을 그대로 심는다(손으로 만든 값이 아니다).
  (다) GET /golden/assignments 의 enforcement 키가 손잡이를 따라 바뀌고 기존 키는 그대로다 · 배정 표(assignments)가 원장과 같다.

이 시험이 정말 잠그는지는 test_probe_* 가 확인한다 — 패널 제거·검수자 변환 무력화·XSS 방어 제거를 monkeypatch 로 흉내 내면 같은
탐침이 그것을 **잡아야** 한다(무력화해도 초록불인 시험은 잠그지 못한 것이다).

node 나 tests/e2e_console/node_modules/jsdom 이 없으면 화면(jsdom) 시험만 건너뛴다.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from koipa.api import golden as golden_api
from koipa.api._jwt_auth import require_auth
from koipa.api.app import app
from koipa.services import proxy_gold_candidate_service as pgs
from tests.test_golden_reviewer_assignment import (
    A1, A3, ADMIN, B1, B2, EXPERT_A, EXPERT_C, KL_BACKEND, N1, Harness, make_pool, set_flags,
)

API = "/api/v1"
ROOT = Path(__file__).resolve().parents[1]
FLAG_COMBOS = [dict(), dict(assignment=True), dict(blind=True), dict(assignment=True, blind=True)]

# 검수자 화면에 하나라도 있으면 새는 것 — 패널의 표식·id·문구·관리자 API 주소.
PANEL_TOKENS = (
    "assign-panel", "golden/assignments", 'id="assignments"', 'id="asn', "asnRevoke",
    "검수 배정", "배정 강제", "제안 등급 숨김", "배정한 관리자", "배정일시",
)


@pytest.fixture
def h(tmp_path, monkeypatch):
    """기본은 두 손잡이 모두 꺼진 상태 — 켜는 것은 각 시험의 몫이다."""
    make_pool(tmp_path)
    monkeypatch.setattr(pgs, "_DEFAULT_ROOT", tmp_path)
    pgs._CANDIDATE_CACHE.clear()
    set_flags(monkeypatch)
    yield Harness(TestClient(app), tmp_path)
    app.dependency_overrides.pop(require_auth, None)
    pgs._CANDIDATE_CACHE.clear()


def manage_html(h: Harness, who) -> str:
    r = h.get(who, "/golden/candidates/manage.html")
    assert r.status_code == 200, (who, r.status_code)
    return r.text


# ══ (가) 서버 렌더링 — 누구에게 패널이 있고 누구에게 없나 ═══════════════════════════════════════════


def admin_panel_problems(text: str) -> list[str]:
    """관리자 화면에 패널이 제대로 실렸나 — 빠진 것마다 한 줄. 비어 있으면 온전하다."""
    out = []
    for what, needle in (
        ("본문 구간", 'id="assignments"'), ("배정 표", 'id="asnRows"'), ("배정 폼", 'id="asnSubmit"'),
        ("메뉴 링크", 'href="#assignments"'), ("스크립트(관리자 API 주소)", "golden/assignments"),
        ("스타일", "#assignments .asnGrid"), ("격리 상태 표시", 'id="asnEnfAssign"'),
    ):
        if needle not in text:
            out.append(f"{what} 이 없다: {needle}")
    begin, end = golden_api._ASSIGN_PANEL_BEGIN, golden_api._ASSIGN_PANEL_END
    if text.count(begin) != 4 or text.count(end) != 4:
        out.append(f"패널 조각(메뉴 링크·스타일·본문·스크립트)이 4개가 아니다: 시작 {text.count(begin)} · 끝 {text.count(end)}")
    return out


def reviewer_panel_leaks(h: Harness, monkeypatch) -> list[str]:
    """검수자(배정 있음·없음) 화면을 손잡이 4조합으로 열어 패널 흔적을 찾는다. 새는 것마다 한 줄."""
    leaks: list[str] = []
    for flags in FLAG_COMBOS:
        set_flags(monkeypatch, **flags)
        for who in (EXPERT_A, EXPERT_C):
            r = h.get(who, "/golden/candidates/manage.html")
            if r.status_code != 200:
                leaks.append(f"{who[1]} {flags}: HTTP {r.status_code}")
                continue
            leaks += [f"{who[1]} {flags}: {t}" for t in PANEL_TOKENS if t in r.text]
    return leaks


@pytest.mark.parametrize("flags", FLAG_COMBOS)
@pytest.mark.parametrize("who", [ADMIN, KL_BACKEND])
def test_admin_and_kl_backend_screens_carry_the_panel(h, monkeypatch, who, flags):
    set_flags(monkeypatch, **flags)
    text = manage_html(h, who)
    assert admin_panel_problems(text) == []
    assert text.count('id="assignments"') == 1


def test_reviewer_screens_carry_no_trace_of_the_panel_under_any_flag_combination(h, monkeypatch):
    """숨김을 끈 검수자 화면과 켠 검수자 화면 모두 — 패널 id·문구·관리자 API 주소가 0건이다."""
    h.standard_assignments()
    assert reviewer_panel_leaks(h, monkeypatch) == []


@pytest.mark.parametrize("flags", FLAG_COMBOS)
def test_reviewer_screen_is_still_a_reviewer_screen_without_the_panel(h, monkeypatch, flags):
    """패널만 빠졌을 뿐 검수자 화면의 나머지(안내띠·관리자 조작 감춤·독립 검수 안내)는 그대로다."""
    set_flags(monkeypatch, **flags)
    text = manage_html(h, EXPERT_A)
    assert "검수자 권한으로 열었습니다" in text and "#openUpload,#promote,#provBox{display:none!important}" in text
    assert ("독립 검수 모드입니다" in text) == bool(flags.get("blind"))
    for anchor in ('id="rows"', 'id="detail"', 'id="ledgerAll"', "load();"):
        assert anchor in text, anchor


def test_stripping_the_panel_gives_back_the_screen_that_existed_before_the_panel():
    """패널 구간만 더해졌다 — 걷어 내면 남는 것에 기존 골격이 그대로 있고, 조각은 정확히 네 개다."""
    admin = golden_api._render_specledger_gold_console_html()
    stripped = golden_api._strip_assignment_panel(admin)
    assert len(stripped) < len(admin) and stripped.endswith("</body></html>")
    for anchor in ('id="overview"', 'id="candidates"', 'id="detail"', 'id="ledgerAll"', 'id="quality"',
                   'href="#quality"', "async function load(showProgress)", "\nload(true);\n</script>"):
        assert anchor in stripped, anchor
    assert stripped == golden_api._strip_assignment_panel(stripped), "걷어 낸 화면을 다시 걷어도 그대로여야 한다(멱등)"
    # 조각이 밖으로 나가는 자리가 없다: 마크업이 표식 밖에 있으면 검수자 화면에 남는다
    rx = re.compile(re.escape(golden_api._ASSIGN_PANEL_BEGIN) + ".*?" + re.escape(golden_api._ASSIGN_PANEL_END), re.S)
    assert len(rx.findall(admin)) == 4


def test_strip_refuses_to_serve_a_page_that_still_carries_a_trace_of_the_panel():
    """fail-closed — 표식이 어긋나거나 패널 마크업이 표식 밖에 붙으면 화면을 내주지 않고 예외를 낸다."""
    admin = golden_api._render_specledger_gold_console_html()
    head, _sep, tail = admin.rpartition(golden_api._ASSIGN_PANEL_END)          # 마지막 조각(스크립트)의 끝 표식이 사라진 화면
    with pytest.raises(RuntimeError):
        golden_api._strip_assignment_panel(head + tail)
    stray = admin.replace("</body>", '<div id="asnStray">패널 조각이 표식 밖에 있다</div></body>')
    with pytest.raises(RuntimeError):
        golden_api._strip_assignment_panel(stray)
    with pytest.raises(RuntimeError):
        golden_api._as_reviewer_view(stray)
    with pytest.raises(RuntimeError):
        golden_api._as_blind_reviewer_view(stray)


def test_both_reviewer_transforms_strip_the_panel_on_their_own():
    admin = golden_api._render_specledger_gold_console_html()
    assert admin_panel_problems(admin) == []
    for fn in (golden_api._as_reviewer_view, golden_api._as_blind_reviewer_view):
        out = fn(admin)
        assert [t for t in PANEL_TOKENS if t in out] == [], fn.__name__


# ── 이 시험이 정말 잠그는가 — 흉내로 끄면 탐침이 잡아야 한다 ────────────────────────────────────────


@pytest.mark.parametrize(("target", "screens"), [
    ("_strip_assignment_panel", 8),      # 걷는 함수 자체가 무력화 — 손잡이 4조합 × 검수자 2명 모두 샌다
    ("_as_reviewer_view", 4),            # 검수자 변환이 무력화 — 숨김을 켠 두 조합은 숨김 변환이 따로 걷어 새지 않는다(이중 방어)
])
def test_probe_detects_a_disabled_reviewer_strip(h, monkeypatch, target, screens):
    """검수자용 변환을 무력화(패널을 걷지 않음)하면 검수자 화면 탐침이 패널 흔적을 잡아낸다."""
    h.standard_assignments()
    monkeypatch.setattr(golden_api, target, lambda html: html)
    leaks = reviewer_panel_leaks(h, monkeypatch)
    for token in ("golden/assignments", 'id="assignments"', "배정 강제", "assign-panel"):
        assert any(token in x for x in leaks), (token, leaks[:4])
    assert len(leaks) == screens * len(PANEL_TOKENS), (len(leaks), screens)
    assert any(x.startswith("expert-a {}:") for x in leaks) and any(x.startswith("expert-c {}:") for x in leaks)


@pytest.mark.parametrize("piece", ["_ASSIGN_PANEL_SECTION", "_ASSIGN_PANEL_SCRIPT", "_ASSIGN_PANEL_CSS", "_ASSIGN_PANEL_NAV"])
def test_probe_detects_a_removed_panel_piece(h, monkeypatch, piece):
    """패널을 제거(조각 하나라도 빠짐)하면 관리자 화면 탐침이 빠진 것을 알려 준다."""
    assert admin_panel_problems(manage_html(h, ADMIN)) == []
    monkeypatch.setattr(golden_api, piece, "")
    assert admin_panel_problems(manage_html(h, ADMIN)) != []


# ── 화면 코드 규칙 (소스 검사) ──────────────────────────────────────────────────────────────────────


def _panel_source() -> tuple[str, str]:
    section = golden_api._ASSIGN_PANEL_SECTION
    script = golden_api._ASSIGN_PANEL_SCRIPT
    # 조각이 비어 있으면 아래 소스 검사가 빈 문자열을 그대로 통과시킨다 — 패널이 없는 상태에서 초록불이 나지 않게 먼저 막는다.
    assert 'id="assignments"' in section and "golden/assignments" in script, "패널 본문·스크립트 조각이 비어 있다"
    return section, script


def test_panel_script_never_builds_markup_from_strings():
    """서버가 준 값·관리자가 입력한 값(검수자 ID 포함)은 textContent·DOM 생성으로만 넣는다 — 문자열을 마크업으로 파싱하는 API 가 없다."""
    _section, script = _panel_source()
    for banned in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute('on"):
        assert banned not in script, banned


def test_panel_has_no_key_or_token_field_and_no_confidence_figure():
    """키·토큰을 받는 칸·요청 머리·주소 파라미터가 없고, 신뢰도 수치를 쓰지 않는다. 신원은 same-origin 쿠키로만 간다."""
    section, script = _panel_source()
    ids = re.findall(r'<(input|select|textarea)\b[^>]*\bid="([^"]+)"', section)
    assert sorted(i for _t, i in ids) == sorted(["asnReviewer", "asnKind", "asnBatch", "asnDocs", "asnReason"])
    assert not re.search(r'type="(password|hidden|file)"', section)
    for banned in ("X-API-Key", "Authorization", "localStorage", "prompt(", "apikey", "api_key", "?key=", "token=", "actor"):
        assert banned.lower() not in script.lower(), banned
    for banned in ("신뢰도", "confidence"):
        assert banned not in section and banned.lower() not in script.lower(), banned
    assert "credentials:'same-origin'" in script


def test_every_input_of_the_panel_is_read_by_the_script():
    """쓰이지 않는 입력칸이 없다 — 모든 칸의 id 를 스크립트가 읽고, 그 값이 요청에 실린다(실제 요청은 jsdom 시험이 본다)."""
    section, script = _panel_source()
    for _tag, ident in re.findall(r'<(input|select|textarea)\b[^>]*\bid="([^"]+)"', section):
        assert f"$('{ident}')" in script, f"{ident} 을 스크립트가 읽지 않는다"


# ══ (다) GET /golden/assignments — enforcement · assignments ═══════════════════════════════════


OLD_KEYS = {"total_candidates", "assigned_candidate_count", "unassigned_candidate_count", "reviewers",
            "ledger_events_total", "by_event", "enforced"}


@pytest.mark.parametrize("flags", FLAG_COMBOS)
@pytest.mark.parametrize("who", [ADMIN, KL_BACKEND])
def test_status_enforcement_follows_the_two_flags(h, monkeypatch, who, flags):
    set_flags(monkeypatch, **flags)
    body = h.get(who, "/golden/assignments").json()
    a, b = bool(flags.get("assignment")), bool(flags.get("blind"))
    assert body["enforcement"] == {"assignment_enforced": a, "blind_enforced": b}
    assert body["enforced"] == {"assignment": a, "blind": b}, "옛 표기는 그대로이고 같은 값이어야 한다"


def test_status_keeps_every_existing_key_and_adds_only_enforcement_and_assignments(h):
    h.standard_assignments()
    body = h.get(ADMIN, "/golden/assignments").json()
    assert set(body) == OLD_KEYS | {"enforcement", "assignments"}
    assert set(h.get(ADMIN, "/golden/assignments", params={"include_events": "true"}).json()) == \
        OLD_KEYS | {"enforcement", "assignments", "events"}
    # 기존 키의 값은 종전 그대로 — 아래는 test_golden_reviewer_assignment 가 잠근 값과 같다
    assert body["total_candidates"] == 7 and body["assigned_candidate_count"] == 5 and body["unassigned_candidate_count"] == 2
    by = {r["reviewer_id"]: r for r in body["reviewers"]}
    assert set(by["expert-a"]) == {"reviewer_id", "doc_ids", "review_batches", "visible_doc_count"}
    assert by["expert-a"]["review_batches"] == ["batch-A"] and by["expert-a"]["visible_doc_count"] == 3
    assert body["ledger_events_total"] == 4 and body["by_event"] == {"assign": 4}


def test_status_assignments_table_matches_the_ledger_line_by_line(h):
    """배정 표의 시각·관리자·사유는 그 배정을 적은 원장 이벤트의 값이다. 검수자별로 묶이고 배치가 문서보다 앞에 온다."""
    assert h.assign("expert-b", doc_ids=[B2, B1], reason="2차 검수").status_code == 200
    assert h.assign("expert-a", review_batch="batch-A", who=KL_BACKEND, reason="1차").status_code == 200
    assert h.assign("expert-b", review_batch="batch-B").status_code == 200
    ledger = [json.loads(x) for x in h.ledger_lines()]
    at_of = {(e["reviewer_id"], e.get("doc_id") or e.get("review_batch")): e for e in ledger}
    rows = h.get(ADMIN, "/golden/assignments").json()["assignments"]
    assert [(r["reviewer_id"], r["kind"], r["target"]) for r in rows] == [
        ("expert-a", "review_batch", "batch-A"),
        ("expert-b", "review_batch", "batch-B"), ("expert-b", "doc_id", B1), ("expert-b", "doc_id", B2)]
    for r in rows:
        e = at_of[(r["reviewer_id"], r["target"])]
        assert (r["assigned_at"], r["assigned_by"], r["reason"]) == (e["at"], e["actor_id"], e["reason"]), r
    assert {r["target"]: r["assigned_by"] for r in rows}["batch-A"] == "kl-ops"
    assert {r["target"]: r["reason"] for r in rows}[B1] == "2차 검수"
    for r in rows:
        assert set(r) == {"reviewer_id", "kind", "target", "assigned_at", "assigned_by", "reason"}


def test_status_assignments_drop_revoked_rows_and_show_the_latest_assignment_time(h):
    h.standard_assignments()
    first = {r["target"]: r for r in h.get(ADMIN, "/golden/assignments").json()["assignments"]}
    assert h.revoke("expert-b", doc_ids=[B1], who=KL_BACKEND).status_code == 200
    after = {(r["reviewer_id"], r["target"]) for r in h.get(ADMIN, "/golden/assignments").json()["assignments"]}
    assert ("expert-b", B1) not in after and ("expert-b", B2) in after and ("expert-a", "batch-A") in after
    assert len(h.ledger_lines()) == 5, "해제도 원장에는 덧붙는다"
    assert h.assign("expert-b", doc_ids=[B1], who=KL_BACKEND, reason="다시").status_code == 200
    again = {r["target"]: r for r in h.get(ADMIN, "/golden/assignments").json()["assignments"] if r["reviewer_id"] == "expert-b"}
    assert again[B1]["assigned_by"] == "kl-ops" and again[B1]["reason"] == "다시"
    assert again[B1]["assigned_at"] >= first[B1]["assigned_at"]
    assert again[B2]["assigned_by"] == "admin-kim", "다시 배정하지 않은 줄은 처음 배정 그대로"


def test_status_assignments_follow_the_same_filters_as_reviewers(h):
    h.standard_assignments()
    only_b = h.get(ADMIN, "/golden/assignments", params={"reviewer_id": "expert-b"}).json()
    assert {r["reviewer_id"] for r in only_b["assignments"]} == {"expert-b"} == {r["reviewer_id"] for r in only_b["reviewers"]}
    shared = h.get(ADMIN, "/golden/assignments", params={"doc_id": A3}).json()
    assert {r["reviewer_id"] for r in shared["assignments"]} == {"expert-a", "expert-b"}
    assert h.get(ADMIN, "/golden/assignments", params={"reviewer_id": "nobody"}).json()["assignments"] == []


LINE_BREAKERS = ["\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"]


@pytest.mark.parametrize("ch", LINE_BREAKERS, ids=[f"U+{ord(c):04X}" for c in LINE_BREAKERS])
def test_ids_and_reasons_holding_unicode_line_separators_are_not_lost_from_the_ledger(h, ch):
    """원장은 한글을 그대로 적으므로(ensure_ascii=False) 검수자 ID·사유에 U+2028 같은 줄 경계 문자가 있으면 한 이벤트가 두 줄로 읽혀
    둘 다 버려졌다(str.splitlines) — 배정은 적혔다고 답했는데 표·검수자 시야 어디에도 없었다. 줄은 개행 문자(LF)로만 가른다."""
    reviewer = f"검수{ch}자"
    reason = f"사유{ch}첫줄{ch}둘째줄"
    r = h.assign(reviewer, review_batch="batch-A", reason=reason)
    assert r.status_code == 200 and r.json()["events_written"] == 1
    rows = h.get(ADMIN, "/golden/assignments").json()["assignments"]
    assert [(x["reviewer_id"], x["target"], x["reason"]) for x in rows] == [(reviewer, "batch-A", reason)]
    assert h.get(ADMIN, "/golden/assignments").json()["ledger_events_total"] == 1
    # 같은 배정을 또 넣으면 이미 있는 것으로 건너뛴다(원장을 제대로 읽어야 알 수 있다) · 해제하면 사라진다
    again = h.assign(reviewer, review_batch="batch-A")
    assert again.json()["events_written"] == 0 and len(again.json()["skipped"]) == 1
    assert h.revoke(reviewer, review_batch="batch-A").json()["events_written"] == 1
    assert h.get(ADMIN, "/golden/assignments").json()["assignments"] == []


def test_status_still_needs_admin_or_kl_backend(h):
    assert h.get(EXPERT_A, "/golden/assignments").status_code == 403
    assert h.get(ADMIN, "/golden/assignments").status_code == 200


def test_openapi_documents_the_new_keys_in_code_and_in_the_yaml():
    schema = app.openapi()["components"]["schemas"]["GoldenAssignmentStatusResponse"]
    assert {"enforcement", "assignments", "enforced", "reviewers"} <= set(schema["properties"])
    yaml_text = (ROOT.parent / "doc" / "03_openapi_koipa_kl.yaml").read_text(encoding="utf-8")
    block = yaml_text.split("  /golden/assignments:\n", 1)[1].split("\n  /golden/assignments/revoke:", 1)[0]
    get_block = block.split("    post:\n", 1)[0]
    for word in ("enforcement:", "assignments:", "assignment_enforced", "blind_enforced", "assigned_at", "assigned_by"):
        assert word in get_block, word


# ══ (나) 화면 동작 — jsdom ═══════════════════════════════════════════════════════════════════════

NODE = shutil.which("node")
_PKG = Path(__file__).resolve().parent / "e2e_console" / "package.json"
needs_dom = pytest.mark.skipif(
    not NODE or not (_PKG.parent / "node_modules" / "jsdom").is_dir(),
    reason="node 또는 tests/e2e_console/node_modules/jsdom 이 없다 — 화면(jsdom) 시험만 건너뜀")

MANAGE_URL = "http://localhost:8000/api/v1/golden/candidates/manage.html"
ASN = "GET /api/v1/golden/assignments"

# 한 시나리오를 jsdom 에서 돌린다. 서버 응답은 시나리오가 준 것(이 서버가 실제로 낸 응답)을 순서대로 돌려주고, 마지막 것은 반복한다.
# 요청은 전부 기록한다(주소·방법·머리·본문). 각 단계 뒤에 요청이 다 끝날 때까지 기다리고, snap 단계에서 패널의 상태를 찍는다.
_PROBE = r'''
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(process.argv[3]);
const { JSDOM, VirtualConsole } = require('jsdom');
const sc = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const tick = (ms) => new Promise((r) => setTimeout(r, ms));

const requests = [], unknown = [], errors = [], navAttempts = [], snaps = {};
const seqs = {};
let inflight = 0;
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => {
  if (/navigation/i.test(String(e.message))) navAttempts.push(String(e.message));
  else errors.push('jsdomError: ' + e.message);
});
const mk = (status, body, text) => ({
  ok: status < 400, status,
  text: async () => (text !== undefined && text !== null ? text : JSON.stringify(body)),
  json: async () => body,
});
const fetchStub = async (url, opt = {}) => {
  const u = new URL(String(url), 'http://localhost:8000/');
  const method = String(opt.method || 'GET').toUpperCase();
  const key = method + ' ' + u.pathname;
  let body = null;
  if (opt.body !== undefined && opt.body !== null) { try { body = JSON.parse(opt.body); } catch (e) { body = '<non-json>'; } }
  requests.push({ method, path: u.pathname, search: u.search, headers: opt.headers || {}, credentials: opt.credentials || null, body });
  const list = sc.routes[key];
  if (!list) { unknown.push(key); return mk(404, { detail: 'no stub for ' + key }); }
  const i = seqs[key] || 0; seqs[key] = i + 1;
  const r = list[Math.min(i, list.length - 1)];
  inflight += 1;
  try {
    if (r.delayMs) await tick(r.delayMs);
    if (r.net) throw new TypeError('Failed to fetch');
    return mk(r.status === undefined ? 200 : r.status, r.body, r.text);
  } finally { inflight -= 1; }
};

const dom = new JSDOM(sc.html, {
  runScripts: 'dangerously', pretendToBeVisual: true, url: sc.url, virtualConsole: vc,
  beforeParse(w) {
    w.fetch = fetchStub;
    w.HTMLElement.prototype.scrollIntoView = () => {};
    w.addEventListener('error', (e) => errors.push('onerror: ' + (e.message || e.error)));
    w.addEventListener('unhandledrejection', (e) => errors.push('rejection: ' + (e.reason && e.reason.message || e.reason)));
    if (sc.bounced) w.sessionStorage.setItem('koipa_login_bounced', '1');
  },
});
const w = dom.window, d = w.document;
const by = (id) => d.getElementById(id);
const text = (id) => { const e = by(id); return e ? e.textContent : null; };

async function settle() {
  let calm = 0;
  for (let i = 0; i < 240 && calm < 4; i++) { await tick(25); calm = inflight === 0 ? calm + 1 : 0; }
}
function snap() {
  return {
    rows: [...d.querySelectorAll('#asnRows .asnRow')].map((r) => [...r.children].map((c) => c.textContent)),
    revokeButtons: [...d.querySelectorAll('#asnRows .asnRevoke')].map((b) => ({ disabled: b.disabled, label: b.getAttribute('aria-label') })),
    emptyText: (d.querySelector('#asnRows .empty') || {}).textContent || null,
    count: text('asnCount'),
    enfAssign: text('asnEnfAssign'), enfBlind: text('asnEnfBlind'), enfNote: text('asnEnfNote'),
    datalist: [...d.querySelectorAll('#asnReviewers option')].map((o) => o.value),
    batchOptions: [...d.querySelectorAll('#asnBatch option')].map((o) => [o.value, o.textContent]),
    batchDisabled: by('asnBatch').disabled, batchValue: by('asnBatch').value,
    kind: by('asnKind').value,
    batchWrapHidden: by('asnBatchWrap').style.display === 'none', docsWrapHidden: by('asnDocsWrap').style.display === 'none',
    docsDisabled: by('asnDocs').disabled,
    msg: text('asnMsg'), msgIsError: by('asnMsg').className.includes('error'),
    tableMsg: text('asnTableMsg'), tableMsgIsError: by('asnTableMsg').className.includes('error'),
    submitDisabled: by('asnSubmit').disabled,
    inputs: { reviewer: by('asnReviewer').value, docs: by('asnDocs').value, reason: by('asnReason').value },
    danger: {
      elements: d.querySelectorAll('#assignments img, #assignments svg, #assignments script, #assignments iframe, #assignments style, #assignments a').length,
      handlers: d.querySelectorAll('#assignments [onerror], #assignments [onload], #assignments [onclick], #assignments [onfocus]').length,
      xss: w.__xss === undefined ? null : w.__xss,
    },
    bounceFlag: w.sessionStorage.getItem('koipa_login_bounced'),
    navAttempts: navAttempts.length,
  };
}
await settle();
snaps.loaded = snap();
for (const st of sc.steps) {
  if (st.op === 'snap') { snaps[st.name] = snap(); continue; }
  if (st.op === 'wait') { await tick(st.ms); continue; }
  if (st.op === 'settle') { await settle(); continue; }
  if (st.op === 'setroute') { sc.routes[st.key] = st.list; seqs[st.key] = 0; continue; }
  if (st.op === 'type') {
    const e = by(st.id); e.value = st.value; e.dispatchEvent(new w.Event('input', { bubbles: true }));
  } else if (st.op === 'select') {
    const e = by(st.id); e.value = st.value;
    if (e.value !== st.value) throw new Error('선택지가 없다: ' + st.id + ' = ' + st.value);
    e.dispatchEvent(new w.Event('change', { bubbles: true }));
  } else if (st.op === 'click') {
    const el = d.querySelectorAll(st.sel)[st.nth || 0];
    if (!el) throw new Error('누를 요소가 없다: ' + st.sel + ' #' + (st.nth || 0));
    for (let i = 0; i < (st.times || 1); i++) el.click();
  } else throw new Error('알 수 없는 단계: ' + st.op);
  if (st.settle !== false) await settle();
}
snaps.final = snap();
console.log(JSON.stringify({ snaps, requests, unknown, errors, navAttempts: navAttempts.length }));
w.close();
'''


def run_probe(tmp_path: Path, *, html: str, routes: dict, steps: list | None = None, bounced: bool = False) -> dict:
    scenario = {"html": html, "routes": routes, "steps": steps or [], "url": MANAGE_URL, "bounced": bounced}
    spec, js = tmp_path / "scenario.json", tmp_path / "probe.mjs"
    spec.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
    js.write_text(_PROBE, encoding="utf-8")
    run = subprocess.run([NODE, str(js), str(spec), str(_PKG)], capture_output=True, text=True,
                         encoding="utf-8", timeout=180)
    assert run.returncode == 0, run.stderr[-1500:]
    return json.loads(run.stdout)


def ok(resp) -> dict:
    """TestClient 응답 → 스텁 응답. 상태 코드와 본문을 그대로 옮긴다."""
    try:
        return {"status": resp.status_code, "body": resp.json()}
    except ValueError:
        return {"status": resp.status_code, "text": resp.text}


def base_routes(h: Harness) -> dict:
    """콘솔 본체(목록·세션·결정 이력)가 부르는 주소 — 이 서버의 실제 응답. 패널의 배치 목록도 이 목록 응답의 available_batches 다."""
    return {
        "GET /api/v1/golden/candidates/session": [ok(h.get(ADMIN, "/golden/candidates/session"))],
        "GET /api/v1/golden/candidates": [ok(h.get(ADMIN, "/golden/candidates"))],
        "GET /api/v1/golden/candidates/decisions": [ok(h.get(ADMIN, "/golden/candidates/decisions"))],
    }


def status_route(h: Harness, who=ADMIN) -> dict:
    return ok(h.get(who, "/golden/assignments"))


def panel_requests(out: dict, *, method: str | None = None) -> list[dict]:
    return [r for r in out["requests"] if "/golden/assignments" in r["path"] and (method is None or r["method"] == method)]


def assert_clean(out: dict) -> None:
    assert out["errors"] == [], out["errors"]
    assert out["unknown"] == [], out["unknown"]


def assert_request_hygiene(out: dict) -> None:
    """패널이 보낸 요청 전부 — 배정한 관리자·키·토큰을 싣지 않고, 쿠키(same-origin)로만 인증하고, 주소에 질의가 없다."""
    allowed = {"reviewer_id", "doc_ids", "review_batch", "reason"}
    for r in panel_requests(out):
        assert r["credentials"] == "same-origin", r
        assert set(r["headers"]) <= {"Content-Type"}, r["headers"]
        assert r["search"] == "", r["search"]
        if r["method"] == "POST":
            assert r["headers"] == {"Content-Type": "application/json"}
            assert set(r["body"]) <= allowed, r["body"]
            assert ("doc_ids" in r["body"]) != ("review_batch" in r["body"]), "대상은 정확히 하나"
        else:
            assert r["method"] == "GET" and r["body"] is None


@needs_dom
def test_panel_shows_flags_empty_table_and_batch_choices_from_the_candidate_list(h, tmp_path):
    """꺼짐·빈 목록: 두 손잡이가 꺼짐으로 보이고 「적용되지 않음」 한 줄이 뜨며, 표는 빈 상태 문구, 배치 목록은 후보 목록 응답의 available_batches 다."""
    routes = {**base_routes(h), ASN: [status_route(h)]}
    assert routes[ASN][0]["body"]["assignments"] == []
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes)
    assert_clean(out)
    s = out["snaps"]["loaded"]
    assert (s["enfAssign"], s["enfBlind"]) == ("꺼짐", "꺼짐")
    assert "배정해도 검수자 화면에 적용되지 않습니다" in s["enfNote"]
    assert s["rows"] == [] and s["count"] == "0건" and "현재 배정이 없습니다" in s["emptyText"]
    assert s["datalist"] == []
    listed = routes["GET /api/v1/golden/candidates"][0]["body"]["available_batches"]
    assert listed == [{"review_batch": "batch-A", "total": 3}, {"review_batch": "batch-B", "total": 3}]
    assert s["batchOptions"] == [["", "배치를 선택하십시오"], ["batch-A", "batch-A · 3건"], ["batch-B", "batch-B · 3건"]]
    assert s["kind"] == "review_batch" and not s["batchDisabled"] and s["docsWrapHidden"] and s["docsDisabled"]
    assert not s["batchWrapHidden"] and s["submitDisabled"] is False
    assert [(r["method"], r["path"]) for r in panel_requests(out)] == [("GET", "/api/v1/golden/assignments")]
    assert_request_hygiene(out)


@needs_dom
def test_panel_draws_the_table_from_server_rows_and_suggests_assigned_reviewers(h, monkeypatch, tmp_path):
    """켜짐·배정 있음: 표의 여섯 칸이 서버가 준 값(검수자·종류·대상+사유·배정일시·관리자)이고 datalist 는 이미 배정된 검수자 ID 다."""
    assert h.assign("expert-a", review_batch="batch-A", reason="1차 검수").status_code == 200
    assert h.assign("expert-b", doc_ids=[B1, B2], who=KL_BACKEND).status_code == 200
    set_flags(monkeypatch, assignment=True, blind=True)
    st = status_route(h)
    routes = {**base_routes(h), ASN: [st]}
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes)
    assert_clean(out)
    s = out["snaps"]["loaded"]
    assert (s["enfAssign"], s["enfBlind"]) == ("켜짐", "켜짐") and s["enfNote"] == ""
    server_rows = st["body"]["assignments"]
    assert len(server_rows) == 3 and s["count"] == "3건"
    assert s["emptyText"] is None
    for cells, row in zip(s["rows"], server_rows):
        target = row["target"] + (("사유: " + row["reason"]) if row["reason"] else "")
        assert cells == [row["reviewer_id"], "배치" if row["kind"] == "review_batch" else "문서", target,
                         row["assigned_at"][:19].replace("T", " "), row["assigned_by"], "해제"], (cells, row)
    assert s["rows"][0][4] == "admin-kim" and s["rows"][1][4] == "kl-ops"
    assert s["datalist"] == ["expert-a", "expert-b"]
    assert [b["disabled"] for b in s["revokeButtons"]] == [False] * 3
    assert [b["label"] for b in s["revokeButtons"]][0] == "검수자 expert-a 의 배정 해제 — batch-A"


@needs_dom
def test_panel_assigns_a_batch_and_reloads_the_table(h, tmp_path):
    """배치 배정: 요청은 POST /golden/assignments · 본문은 {reviewer_id, review_batch, reason} 뿐(배정한 관리자 없음) · 결과 문구 · 표 다시 불러옴."""
    before = status_route(h)
    posted = ok(h.assign("expert-c", review_batch="batch-B", reason="교수 검수"))
    after = status_route(h)
    assert posted["body"]["events_written"] == 1 and after["body"]["assignments"] != before["body"]["assignments"]
    routes = {**base_routes(h), ASN: [before, after], "POST /api/v1/golden/assignments": [posted]}
    steps = [
        {"op": "type", "id": "asnReviewer", "value": "  expert-c "},
        {"op": "select", "id": "asnBatch", "value": "batch-B"},
        {"op": "type", "id": "asnReason", "value": " 교수 검수 "},
        {"op": "click", "sel": "#asnSubmit"},
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    posts = panel_requests(out, method="POST")
    assert [(p["method"], p["path"], p["search"]) for p in posts] == [("POST", "/api/v1/golden/assignments", "")]
    assert posts[0]["body"] == {"reviewer_id": "expert-c", "review_batch": "batch-B", "reason": "교수 검수"}
    assert len(panel_requests(out, method="GET")) == 2, "성공 뒤 표를 다시 불러온다"
    s = out["snaps"]["final"]
    assert s["msg"] == "새로 배정 1건 · 이미 배정돼 건너뜀 0건" and not s["msgIsError"]
    assert s["rows"][-1][:2] == ["expert-c", "배치"] and s["rows"][-1][2].startswith("batch-B")
    assert s["datalist"] == ["expert-c"] and s["inputs"]["reviewer"] == "  expert-c ", "검수자 ID 칸은 남겨 이어서 배정할 수 있다(입력한 그대로)"
    assert s["inputs"]["reason"] == "" and s["submitDisabled"] is False
    assert_request_hygiene(out)


@needs_dom
def test_panel_assigns_a_pasted_document_list_and_reports_skipped_ones(h, tmp_path):
    """문서 목록 배정: 한 줄에 하나 · 빈 줄·앞뒤 공백·중복은 정리하고 본문은 {reviewer_id, doc_ids, reason}. 이미 배정된 문서는 건너뜀으로 센다."""
    assert h.assign("expert-b", doc_ids=[B1]).status_code == 200
    before = status_route(h)
    posted = ok(h.assign("expert-b", doc_ids=[B1, N1], reason=""))
    after = status_route(h)
    assert posted["body"]["events_written"] == 1 and len(posted["body"]["skipped"]) == 1
    routes = {**base_routes(h), ASN: [before, after], "POST /api/v1/golden/assignments": [posted]}
    steps = [
        {"op": "type", "id": "asnReviewer", "value": "expert-b"},
        {"op": "select", "id": "asnKind", "value": "doc_ids"},
        {"op": "snap", "name": "docs_mode"},
        {"op": "type", "id": "asnDocs", "value": f"{B1}\r\n\n   {N1}  \n{B1}\n"},
        {"op": "click", "sel": "#asnSubmit"},
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    m = out["snaps"]["docs_mode"]
    assert m["batchWrapHidden"] and m["batchDisabled"] and not m["docsWrapHidden"] and not m["docsDisabled"], \
        "쓰이지 않는 칸(배치 선택)은 감추고 잠근다"
    posts = panel_requests(out, method="POST")
    assert len(posts) == 1 and posts[0]["body"] == {"reviewer_id": "expert-b", "doc_ids": [B1, N1], "reason": ""}
    s = out["snaps"]["final"]
    assert s["msg"] == "새로 배정 1건 · 이미 배정돼 건너뜀 1건"
    assert s["inputs"]["docs"] == "" and {r[2] for r in s["rows"]} >= {B1, N1}
    assert_request_hygiene(out)


@needs_dom
def test_panel_revokes_a_batch_row_and_a_document_row_with_the_same_unit(h, tmp_path):
    """해제: 해제 버튼이 POST /golden/assignments/revoke 를 부르고 대상 단위(배치 ↔ 문서)가 배정과 같다. 성공 뒤 표를 다시 불러온다."""
    h.standard_assignments()
    s0 = status_route(h)
    rows = s0["body"]["assignments"]
    batch_idx = next(i for i, r in enumerate(rows) if r["kind"] == "review_batch")
    doc_idx = next(i for i, r in enumerate(rows) if r["kind"] == "doc_id" and r["target"] == B1)
    r1 = ok(h.revoke("expert-a", review_batch="batch-A"))
    s1 = status_route(h)
    r2 = ok(h.revoke("expert-b", doc_ids=[B1]))
    s2 = status_route(h)
    routes = {**base_routes(h), ASN: [s0, s1, s2],
              "POST /api/v1/golden/assignments/revoke": [r1, r2]}
    steps = [
        {"op": "click", "sel": "#asnRows .asnRevoke", "nth": batch_idx},
        {"op": "snap", "name": "after_batch"},
        {"op": "click", "sel": "#asnRows .asnRevoke", "nth": doc_idx - 1},        # 첫 줄(배치)이 사라져 한 칸 당겨졌다
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    posts = panel_requests(out, method="POST")
    assert [(p["path"], p["body"]) for p in posts] == [
        ("/api/v1/golden/assignments/revoke", {"reviewer_id": "expert-a", "review_batch": "batch-A"}),
        ("/api/v1/golden/assignments/revoke", {"reviewer_id": "expert-b", "doc_ids": [B1]}),
    ]
    a = out["snaps"]["after_batch"]
    assert a["tableMsg"] == "해제했습니다 — expert-a · batch-A" and not a["tableMsgIsError"]
    assert all(r[0] != "expert-a" for r in a["rows"]), "해제한 줄은 다시 불러온 표에 없다"
    f = out["snaps"]["final"]
    assert f["tableMsg"] == f"해제했습니다 — expert-b · {B1}"
    assert f["count"] == f"{len(s2['body']['assignments'])}건"
    assert len(panel_requests(out, method="GET")) == 3
    assert_request_hygiene(out)


@needs_dom
def test_panel_shows_the_server_message_verbatim_on_422_and_keeps_what_was_typed(h, tmp_path):
    """422(존재하지 않는 문서·배치 · 형식 오류): 서버가 준 메시지를 그대로 보이고, 입력은 지우지 않으며, 표는 다시 부르지 않는다."""
    unknown_doc = ok(h.assign("expert-b", doc_ids=["NOPE-1"]))
    unknown_batch = ok(h.assign("expert-b", review_batch="batch-Z"))
    too_many = ok(h.assign("expert-b", doc_ids=[f"D-{i}" for i in range(501)]))
    assert [x["status"] for x in (unknown_doc, unknown_batch, too_many)] == [422, 422, 422]
    assert isinstance(too_many["body"]["detail"], list), "형식 오류는 검증 오류 목록(배열)으로 온다"
    routes = {**base_routes(h), ASN: [status_route(h)],
              "POST /api/v1/golden/assignments": [unknown_doc, unknown_batch, too_many]}
    steps = [
        {"op": "type", "id": "asnReviewer", "value": "expert-b"},
        {"op": "select", "id": "asnKind", "value": "doc_ids"},
        {"op": "type", "id": "asnDocs", "value": "NOPE-1"},
        {"op": "type", "id": "asnReason", "value": "메모"},
        {"op": "click", "sel": "#asnSubmit"},
        {"op": "snap", "name": "unknown_doc"},
        {"op": "select", "id": "asnKind", "value": "review_batch"},
        {"op": "select", "id": "asnBatch", "value": "batch-A"},
        {"op": "click", "sel": "#asnSubmit"},
        {"op": "snap", "name": "unknown_batch"},
        {"op": "select", "id": "asnKind", "value": "doc_ids"},
        {"op": "type", "id": "asnDocs", "value": "\n".join(f"D-{i}" for i in range(501))},
        {"op": "click", "sel": "#asnSubmit"},
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    a, b, c = out["snaps"]["unknown_doc"], out["snaps"]["unknown_batch"], out["snaps"]["final"]
    assert unknown_doc["body"]["detail"] in a["msg"] and a["msgIsError"]
    assert unknown_batch["body"]["detail"] in b["msg"] and b["msgIsError"]
    for item in too_many["body"]["detail"]:
        assert item["msg"] in c["msg"], (item["msg"], c["msg"])
    assert c["msgIsError"]
    assert a["inputs"] == {"reviewer": "expert-b", "docs": "NOPE-1", "reason": "메모"}, "실패하면 입력을 지우지 않는다"
    assert len(panel_requests(out, method="GET")) == 1, "실패한 배정은 표를 다시 부르지 않는다"
    assert len(panel_requests(out, method="POST")) == 3
    assert_request_hygiene(out)


@needs_dom
def test_panel_validates_locally_without_sending_anything(h, tmp_path):
    """검수자 ID 가 비었거나 고른 대상이 없으면 요청을 보내지 않고 이유를 말한다."""
    routes = {**base_routes(h), ASN: [status_route(h)]}
    steps = [
        {"op": "click", "sel": "#asnSubmit"},
        {"op": "snap", "name": "no_reviewer"},
        {"op": "type", "id": "asnReviewer", "value": "   "},
        {"op": "click", "sel": "#asnSubmit"},
        {"op": "snap", "name": "blank_reviewer"},
        {"op": "type", "id": "asnReviewer", "value": "expert-a"},
        {"op": "click", "sel": "#asnSubmit"},
        {"op": "snap", "name": "no_batch"},
        {"op": "select", "id": "asnKind", "value": "doc_ids"},
        {"op": "type", "id": "asnDocs", "value": " \n\r\n  "},
        {"op": "click", "sel": "#asnSubmit"},
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    assert "검수자 ID를 입력하십시오" in out["snaps"]["no_reviewer"]["msg"]
    assert "검수자 ID를 입력하십시오" in out["snaps"]["blank_reviewer"]["msg"]
    assert "검수 배치를 선택하십시오" in out["snaps"]["no_batch"]["msg"]
    assert "문서 ID를 한 줄에 하나씩" in out["snaps"]["final"]["msg"] and out["snaps"]["final"]["msgIsError"]
    assert panel_requests(out, method="POST") == []


@needs_dom
def test_panel_without_any_batch_marker_says_so_and_still_assigns_documents(h, tmp_path):
    """배치 표식이 하나도 없는 서버: 배치 선택은 잠기고 이유를 말하며, 문서 목록 배정은 그대로 된다."""
    for meta in h.root.glob("*.metadata.json"):
        data = json.loads(meta.read_text(encoding="utf-8"))
        data.pop("review_batch", None)
        meta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pgs._CANDIDATE_CACHE.clear()
    assert h.get(ADMIN, "/golden/candidates").json()["available_batches"] == []
    posted = ok(h.assign("expert-a", doc_ids=[A1]))
    routes = {**base_routes(h), ASN: [status_route(h)], "POST /api/v1/golden/assignments": [posted]}
    steps = [
        {"op": "snap", "name": "start"},
        {"op": "type", "id": "asnReviewer", "value": "expert-a"},
        {"op": "click", "sel": "#asnSubmit"},
        {"op": "snap", "name": "batch_mode_submit"},
        {"op": "select", "id": "asnKind", "value": "doc_ids"},
        {"op": "type", "id": "asnDocs", "value": A1},
        {"op": "click", "sel": "#asnSubmit"},
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    s = out["snaps"]["start"]
    assert s["batchOptions"] == [["", "배치 표식 없음 — 문서 목록으로 배정하십시오"]] and s["batchDisabled"]
    b = out["snaps"]["batch_mode_submit"]
    assert b["msgIsError"] and "검수 배치를 선택하십시오" in b["msg"]
    posts = panel_requests(out, method="POST")
    assert len(posts) == 1 and posts[0]["body"]["doc_ids"] == [A1], "배치 모드의 제출은 보내지 않았고 문서 배정만 나갔다"


@needs_dom
def test_panel_403_network_error_and_recovery(h, tmp_path):
    """403 · 네트워크 오류는 각각 자기 문구로 알리고(표는 「불러오지 못했습니다」, 격리 상태는 「확인 못 함」), 「새로고침」으로 회복된다."""
    forbidden = ok(h.get(EXPERT_A, "/golden/assignments"))
    assert forbidden["status"] == 403
    good = status_route(h)
    routes = {**base_routes(h), ASN: [forbidden]}
    steps = [
        {"op": "snap", "name": "forbidden"},
        {"op": "setroute", "key": ASN, "list": [{"net": True}]},
        {"op": "click", "sel": "#asnReload"},
        {"op": "snap", "name": "network"},
        {"op": "setroute", "key": ASN, "list": [good]},
        {"op": "click", "sel": "#asnReload"},
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    f, n, r = out["snaps"]["forbidden"], out["snaps"]["network"], out["snaps"]["final"]
    assert "관리자 권한이 필요합니다" in f["tableMsg"] and f["tableMsgIsError"]
    assert "불러오지 못했습니다" in f["emptyText"] and (f["enfAssign"], f["enfBlind"]) == ("확인 못 함", "확인 못 함")
    assert f["enfNote"] == "", "읽지 못한 값으로 「적용되지 않음」을 말하지 않는다"
    assert "서버에 연결하지 못했습니다" in n["tableMsg"] and n["tableMsgIsError"]
    assert r["tableMsg"] == "" and (r["enfAssign"], r["enfBlind"]) == ("꺼짐", "꺼짐") and "현재 배정이 없습니다" in r["emptyText"]


@needs_dom
def test_panel_action_failures_403_network_and_server_error_are_reported_per_kind(h, tmp_path):
    forbidden = ok(h.post(EXPERT_A, "/golden/assignments", json={"reviewer_id": "x", "review_batch": "batch-A"}))
    assert forbidden["status"] == 403
    routes = {**base_routes(h), ASN: [status_route(h)],
              "POST /api/v1/golden/assignments": [forbidden, {"net": True}, {"status": 500, "text": "internal error"}]}
    steps = [
        {"op": "type", "id": "asnReviewer", "value": "expert-c"},
        {"op": "select", "id": "asnBatch", "value": "batch-A"},
        {"op": "click", "sel": "#asnSubmit"}, {"op": "snap", "name": "forbidden"},
        {"op": "click", "sel": "#asnSubmit"}, {"op": "snap", "name": "network"},
        {"op": "click", "sel": "#asnSubmit"},
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    assert "관리자 권한이 필요합니다" in out["snaps"]["forbidden"]["msg"]
    assert "서버에 연결하지 못했습니다" in out["snaps"]["network"]["msg"]
    last = out["snaps"]["final"]
    assert "서버 오류(500)" in last["msg"] and "internal error" in last["msg"] and last["msgIsError"]
    assert all(x["msgIsError"] for x in out["snaps"].values() if x["msg"])
    assert last["submitDisabled"] is False, "실패해도 다시 누를 수 있다"
    assert last["inputs"]["reviewer"] == "expert-c"


@needs_dom
def test_panel_sends_a_401_to_the_login_screen_once_only(h, tmp_path):
    """401 은 다른 콘솔과 같은 방식 — 로그인 화면으로 한 번 보낸다(표식 koipa_login_bounced). 두 번째부터는 보내지 않고 문구로 알린다."""
    unauthorized = {"status": 401, "body": {"detail": "missing authorization"}}
    routes = {**base_routes(h), ASN: [unauthorized]}
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes,
                    steps=[{"op": "snap", "name": "first"}, {"op": "click", "sel": "#asnReload"}])
    assert out["errors"] == [], out["errors"]
    first, last = out["snaps"]["first"], out["snaps"]["final"]
    assert first["bounceFlag"] == "1" and first["navAttempts"] == 1, "처음 401 → 로그인 화면으로 한 번 이동을 시도한다"
    assert first["tableMsg"] == "로그인 화면으로 이동합니다…"
    assert last["navAttempts"] == 1, "두 번째 401 은 다시 보내지 않는다"
    assert "로그인이 만료되었습니다" in last["tableMsg"] and last["tableMsgIsError"]
    # 표식이 이미 있는 탭(다른 화면이 먼저 보냈던 탭)에서는 처음부터 보내지 않는다
    again = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, bounced=True)
    assert again["snaps"]["loaded"]["navAttempts"] == 0 and "로그인이 만료되었습니다" in again["snaps"]["loaded"]["tableMsg"]


@needs_dom
def test_panel_blocks_double_submits_while_a_request_is_in_flight(h, tmp_path):
    """POST 가 진행 중일 때 버튼은 잠기고 두 번 눌러도 요청은 한 번이다. 끝나면 다시 풀린다."""
    posted = {**ok(h.assign("expert-c", review_batch="batch-A")), "delayMs": 400}
    routes = {**base_routes(h), ASN: [status_route(h)], "POST /api/v1/golden/assignments": [posted]}
    steps = [
        {"op": "type", "id": "asnReviewer", "value": "expert-c"},
        {"op": "select", "id": "asnBatch", "value": "batch-A"},
        {"op": "click", "sel": "#asnSubmit", "times": 2, "settle": False},
        {"op": "wait", "ms": 100},
        {"op": "snap", "name": "in_flight"},
        {"op": "settle"},
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    assert out["snaps"]["in_flight"]["submitDisabled"] is True
    assert len(panel_requests(out, method="POST")) == 1
    assert out["snaps"]["final"]["submitDisabled"] is False and out["snaps"]["final"]["msg"].startswith("새로 배정 1건")


# ── XSS · 유니코드 정규화 ──────────────────────────────────────────────────────────────────────

XSS_REVIEWERS = [
    '<img src=x onerror="window.__xss=1">',
    '"><script>window.__xss=2</script>',
    "O'Brien \"따옴표\" & <b>굵게</b> 김검수",
    "중간\u2028줄바꿈\u200b폭없는공백끝",
]
HOSTILE_BATCH = '<img src=x onerror="window.__xss=3"> 배치'
HOSTILE_REASON = '<svg onload="window.__xss=4"> 사유 "\'`'
HOSTILE_ADMIN = '<img src=x onerror="window.__xss=5">admin'
NFD_REVIEWER = unicodedata.normalize("NFD", "한글검수자")
NFC_REVIEWER = unicodedata.normalize("NFC", "한글검수자")


def add_hostile_doc(root: Path) -> None:
    meta = {"doc_id": "GOLD-X-S1-900", "document_origin": "synthetic", "document_type": "제목 GOLD-X-S1-900",
            "candidate_status": "proposed", "claim_scope": "fictional test candidate", "requires_manual_audit": True,
            "intended_label": "S1", "review_batch": HOSTILE_BATCH}
    (root / "GOLD-X-S1-900.metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (root / "GOLD-X-S1-900_문서.md").write_text("본문 표식 BODY-X " + "내용 " * 60, encoding="utf-8")
    pgs._CANDIDATE_CACHE.clear()


def hostile_world(h: Harness) -> dict:
    """적대적 문자열이 서버를 거쳐 표에 오는 세계 — 검수자 ID · 배치 이름 · 사유 · 배정한 관리자(JWT sub) 모두."""
    add_hostile_doc(h.root)
    hostile_admin = ("admin", HOSTILE_ADMIN)
    for rid in XSS_REVIEWERS:
        assert h.assign(rid, review_batch=HOSTILE_BATCH, who=hostile_admin, reason=HOSTILE_REASON).status_code == 200
    assert h.assign(NFD_REVIEWER, doc_ids=[A1], reason="정규화 시험").status_code == 200
    st = status_route(h)
    ids = {r["reviewer_id"] for r in st["body"]["assignments"]}
    assert NFC_REVIEWER in ids and NFD_REVIEWER not in ids, "서버는 검수자 ID 를 NFC 로 적는다(NFD 로 넣어도)"
    return st


@needs_dom
def test_hostile_strings_from_the_server_are_drawn_as_plain_text_and_never_run(h, tmp_path):
    """검수자 ID·배치 이름·사유·배정한 관리자에 태그·스크립트·따옴표·한글·NFD 가 들어 있어도 표·datalist·배치 목록에 **글자 그대로** 보이고,
    실행되는 것도 새로 생기는 요소도 없다. 해제 버튼은 그 검수자 ID 를 글자 그대로 되돌려 보낸다."""
    st = hostile_world(h)
    routes = {**base_routes(h), ASN: [st]}
    rows = st["body"]["assignments"]
    steps = [{"op": "click", "sel": "#asnRows .asnRevoke", "nth": 0, "settle": False}, {"op": "wait", "ms": 200}]
    routes["POST /api/v1/golden/assignments/revoke"] = [{"status": 200, "body": ok(h.revoke(rows[0]["reviewer_id"], review_batch=HOSTILE_BATCH))["body"]}]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert out["errors"] == [], out["errors"]
    s = out["snaps"]["loaded"]
    assert s["danger"] == {"elements": 0, "handlers": 0, "xss": None}, s["danger"]
    assert len(s["rows"]) == len(rows) == len(XSS_REVIEWERS) + 1
    for cells, row in zip(s["rows"], rows):
        reason = ("사유: " + row["reason"]) if row["reason"] else ""
        assert cells[0] == row["reviewer_id"] and cells[2] == row["target"] + reason and cells[4] == row["assigned_by"], cells
    shown = {c[0] for c in s["rows"]}
    assert set(XSS_REVIEWERS) <= shown and NFC_REVIEWER in shown
    assert next(c for c in s["rows"] if c[0] == NFC_REVIEWER)[0] == NFC_REVIEWER != NFD_REVIEWER
    assert {c[4] for c in s["rows"]} >= {HOSTILE_ADMIN}
    assert HOSTILE_REASON in next(c for c in s["rows"] if c[0] == XSS_REVIEWERS[0])[2]
    assert sorted(s["datalist"]) == sorted({r["reviewer_id"] for r in rows}), "datalist 의 값도 글자 그대로"
    assert [HOSTILE_BATCH, f"{HOSTILE_BATCH} · 1건"] in s["batchOptions"]
    assert s["revokeButtons"][0]["label"] == f"검수자 {rows[0]['reviewer_id']} 의 배정 해제 — {rows[0]['target']}"
    assert out["snaps"]["final"]["danger"] == {"elements": 0, "handlers": 0, "xss": None}
    posts = panel_requests(out, method="POST")
    assert posts[0]["body"] == {"reviewer_id": rows[0]["reviewer_id"], "review_batch": HOSTILE_BATCH}, \
        "표의 글자를 다시 가공하지 않고 그대로 되돌려 보낸다"


@needs_dom
def test_hostile_and_nfd_strings_typed_into_the_form_are_sent_exactly_as_typed(h, tmp_path):
    """폼에 넣은 값은 화면이 가공하지 않는다(앞뒤 공백 제외) — NFC 정리는 서버가 한다. 응답 뒤 표에는 서버가 적은 NFC 형이 보인다."""
    add_hostile_doc(h.root)
    typed = f"  {NFD_REVIEWER}  "
    before = status_route(h)
    posted = ok(h.assign(NFD_REVIEWER, review_batch=HOSTILE_BATCH, reason=HOSTILE_REASON))
    assert posted["body"]["reviewer_id"] == NFC_REVIEWER
    routes = {**base_routes(h), ASN: [before, status_route(h)], "POST /api/v1/golden/assignments": [posted]}
    steps = [
        {"op": "type", "id": "asnReviewer", "value": typed},
        {"op": "select", "id": "asnBatch", "value": HOSTILE_BATCH},
        {"op": "type", "id": "asnReason", "value": HOSTILE_REASON},
        {"op": "click", "sel": "#asnSubmit"},
    ]
    out = run_probe(tmp_path, html=manage_html(h, ADMIN), routes=routes, steps=steps)
    assert_clean(out)
    posts = panel_requests(out, method="POST")
    assert posts[0]["body"] == {"reviewer_id": NFD_REVIEWER, "review_batch": HOSTILE_BATCH, "reason": HOSTILE_REASON}
    s = out["snaps"]["final"]
    assert [r[0] for r in s["rows"]] == [NFC_REVIEWER] and s["danger"] == {"elements": 0, "handlers": 0, "xss": None}
    assert s["datalist"] == [NFC_REVIEWER] and NFC_REVIEWER != NFD_REVIEWER


@needs_dom
def test_probe_detects_a_panel_that_would_build_markup_from_server_strings(h, tmp_path):
    """XSS 방어를 제거(textContent → innerHTML)한 판을 같은 탐침에 넣으면 새로 생긴 요소·이벤트 속성을 잡아낸다 — 잡지 못하면 위 시험은 방어를 잠그지 못한 것이다."""
    st = hostile_world(h)
    routes = {**base_routes(h), ASN: [st]}
    html = manage_html(h, ADMIN)
    vulnerable = html.replace("if(text!==undefined)e.textContent=text", "if(text!==undefined)e.innerHTML=text", 1)
    assert vulnerable != html
    out = run_probe(tmp_path, html=vulnerable, routes=routes)
    d = out["snaps"]["loaded"]["danger"]
    assert d["elements"] > 0 and d["handlers"] > 0, d


@needs_dom
def test_probe_detects_a_removed_panel_script(h, tmp_path):
    """스크립트 조각을 뺀 화면(패널 제거의 화면 쪽 흉내)에서는 표가 그려지지 않고 요청도 나가지 않는다 — 위 시험이 그것을 실패로 잡는다."""
    h.standard_assignments()
    routes = {**base_routes(h), ASN: [status_route(h)]}
    html = manage_html(h, ADMIN)
    inert = re.sub(re.escape(golden_api._ASSIGN_PANEL_BEGIN) + r"<script>.*?</script>" + re.escape(golden_api._ASSIGN_PANEL_END),
                   "", html, flags=re.S)
    assert inert != html
    out = run_probe(tmp_path, html=inert, routes=routes)
    s = out["snaps"]["loaded"]
    assert s["rows"] == [] and s["count"] == "" and panel_requests(out) == []
