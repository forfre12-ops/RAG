"""제안 등급 숨김 모드 — 검수자가 제안에 끌려가지 않게 서버가 응답에서 뺀다 (2026-09-21).

왜. 골든셋 검수는 **독립 판정**이어야 한다. 화면이 제안 등급과 '제안 등급 그대로 확정' 버튼을 보여
주면 검수자가 먼저 본 값에 쏠린다(앵커링). 두 전문가가 같은 등급을 냈어도 그것이 독립 판정인지
알 수 없게 된다.

⚠ 화면에서 CSS 로 가리기만 하면 API 로 그대로 읽힌다. 그래서 본체는 **서버가 응답에서 필드를 빼는
것**이고(golden_reviewer_access.shape_*), 화면 조정은 그 위의 정리일 뿐이다.

원칙(독립 검증 뒤 고침, 2026-09-21): **숨김 켠 검수자에게는 자기 자신의 결정에서 온 정보만 보인다.** 다른
검수자의 결정·M 입력·신원, 그리고 "다른 검수자가 결정했다"는 사실(status·grade_fixed·집계·이벤트의
management_before)이 응답 어디에도 없어야 한다. 후보·이벤트·집계의 모든 필드는 (자기 것 / 숨김 / 무관 공개)
셋 중 정확히 하나에 속하고, 그 분류를 test_blind_field_lists_* · test_every_*_is_classified_* 가 실제 응답의
키와 대조해 잠근다 — 새 키가 생기면 분류를 정하기 전에는 시험이 실패한다.

그리고 그 원칙을 키 하나씩 막는 것이 아니라 **불변성**으로 시험한다(섹션 5b): 검수자 R 의 모든 응답은
다른 사람이 어떤 결정을 내려도 **바이트 단위로 그대로**여야 한다. 응답 어디에 무엇이 새든(아직 이름 없는 키
포함) 이 시험이 잡는다. 두 검수자가 서로 다른 M 입력·등급으로 결정하는 시나리오는 양방향(a→b, b→a)이다.

제거할 수 없어 **남는 것**(한계)은 test_known_limits_* 가 사실로 고정한다.

별칭 ID(2026-09-22): 숨김이 켜진 동안 검수자에게 나가는 doc_id 는 전부 별칭이다. 이 파일의 시험은 문서를 POOL 의 실 doc_id
로 적어 두었으므로 Harness 가 검수자 요청의 경로를 별칭으로 바꿔 보내고 응답의 별칭을 실 doc_id 로 되돌린다
(test_golden_reviewer_assignment.Harness). 별칭 계층 자체는 tests/test_golden_alias_ids.py 가 시험한다.
"""
from __future__ import annotations

import json
import unicodedata

import pytest
from fastapi.testclient import TestClient

from koipa.api import golden as golden_api
from koipa.api._jwt_auth import require_auth
from koipa.api.app import app
from koipa.services import golden_reviewer_access as access
from koipa.services import proxy_gold_candidate_service as pgs
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService
# 풀·역할·호출 도구는 배정 시험과 같은 것을 쓴다(같은 시나리오 위에서 숨김을 본다).
from tests.test_golden_reviewer_assignment import (
    A1, A2, A3, ADMIN, B1, B2, B3, EXPERT_A, EXPERT_B, KL_BACKEND, POOL, SYSTEM, Harness, make_pool, set_flags,
)


@pytest.fixture
def h(tmp_path, monkeypatch):
    """배정 시험의 h 와 같은 것 — fixture 를 import 하면 린터(F811)가 막아 여기서 다시 정의한다."""
    make_pool(tmp_path)
    monkeypatch.setattr(pgs, "_DEFAULT_ROOT", tmp_path)
    set_flags(monkeypatch)
    yield Harness(TestClient(app), tmp_path)
    app.dependency_overrides.pop(require_auth, None)


# 응답 어디에도 있으면 안 되는 키 — 재귀로 찾는다(중첩 dict·list 안까지).
ANSWER_KEYS = {
    "proposed_grade", "proposed_grade_basis", "document_path",     # 후보·이벤트
    "by_proposed_grade", "by_final_grade", "quality",              # 집계
    "management_before",         # 이벤트 — 결정 직전의 저장된 M 이라 다른 검수자가 적은 값·신원이 그대로 실려 있다
}


def walk_keys(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= walk_keys(v)
    return out


def blind(monkeypatch, *, assignment: bool = True) -> None:
    set_flags(monkeypatch, assignment=assignment, blind=True)


# ══ 1. 무엇을 뺐나 — 후보 스키마를 필드마다 분류해 잠근다 ═══════════════════════════════════════


def _full_candidate(h) -> dict:
    """결정 이력·출처까지 붙은 후보 한 건(관리자 시점 = 아무것도 안 뺀 원본)."""
    svc = ProxyGoldCandidateService()
    svc.decide(doc_id=B3, action="change", grade="S3", actor_id="admin-kim", reason="공개 판결문")
    return svc.get_candidate(B3)


def test_blind_field_lists_are_locked_literally():
    """이 목록이 바뀌면 검수자에게 보이는 것이 바뀐다 — 의도한 변경이면 여기도 함께 고친다."""
    # 후보
    assert access.BLIND_HIDDEN_CANDIDATE_FIELDS == {"proposed_grade", "proposed_grade_basis", "document_path"}
    assert access.BLIND_OWN_ONLY_CANDIDATE_FIELDS == {
        "final_grade", "latest_decision", "decision_history", "status", "grade_fixed", "management"}
    assert access.BLIND_ALIASED_CANDIDATE_FIELDS == {"doc_id", "title"}
    assert access.BLIND_KEPT_CANDIDATE_FIELDS == {
        "document_origin", "requires_manual_audit", "review_batch", "claim_scope", "content_revision", "characters",
        "document_sha256", "extraction", "provenance", "source_file_sha256", "is_actual_document", "text",
        "department", "info_type"}
    # 이벤트
    assert access.BLIND_HIDDEN_EVENT_FIELDS == {"proposed_grade", "management_before"}
    assert access.BLIND_REBUILT_EVENT_FIELDS == {"management_after"}
    assert access.BLIND_OWN_EVENT_FIELDS == {
        "event_id", "action", "status", "final_grade", "reason", "actor_id", "decided_at"}
    assert access.BLIND_ALIASED_EVENT_FIELDS == {"doc_id"}
    assert access.BLIND_PUBLIC_EVENT_FIELDS == {
        "schema_version", "event_kind", "document_sha256", "document_origin", "claim_scope",
        "provenance_at_decision"}
    # 집계
    assert access.BLIND_HIDDEN_SUMMARY_FIELDS == {"quality", "by_proposed_grade", "by_final_grade"}
    assert access.BLIND_OWN_SUMMARY_FIELDS == {
        "fixed", "unfixed", "deferred", "discarded", "out_of_scope", "by_status", "actual_grade_fixed_unlocked"}
    assert access.BLIND_HIDDEN_BATCH_SUMMARY_FIELDS == {"by_final_grade"}
    assert access.BLIND_OWN_BATCH_SUMMARY_FIELDS == {"terminal", "pending", "deferred", "by_status"}


def _exactly_one(*sets) -> frozenset:
    """분류 집합들이 서로 겹치지 않는지 확인하고 합집합을 돌려준다(한 키가 두 분류에 있으면 실패)."""
    union: set = set()
    for group in sets:
        assert not (union & group), f"한 키가 두 분류에 있다: {sorted(union & group)}"
        union |= group
    return frozenset(union)


def test_every_candidate_key_is_classified_as_hidden_own_or_kept(h):
    """후보 dict 의 모든 키는 (숨김 / 자기 것 / 무관 공개) 셋 중 하나에 **정확히** 속해야 한다. 새 키가 생기면 여기서
    실패한다 — 그 키가 정답·다른 검수자를 암시하는지 정하지 않은 채 검수자에게 나가는 것을 막는다."""
    row = _full_candidate(h)
    classified = _exactly_one(
        access.BLIND_HIDDEN_CANDIDATE_FIELDS, access.BLIND_OWN_ONLY_CANDIDATE_FIELDS,
        access.BLIND_ALIASED_CANDIDATE_FIELDS, access.BLIND_KEPT_CANDIDATE_FIELDS)
    assert set(row) == classified, (
        f"분류 안 된 키: {sorted(set(row) - classified)} · 후보에 없는 분류: {sorted(classified - set(row))}")
    # 이름에 grade 가 든 키는 전부 의식적으로 정해야 한다 — grade_fixed 는 '확정 여부'라도 다른 검수자의 결정을 말하므로
    # 자기 것으로 다시 계산한다(결함 3)
    assert {k for k in row if "grade" in k} == {
        "proposed_grade", "proposed_grade_basis", "final_grade", "grade_fixed"}
    assert "grade_fixed" in access.BLIND_OWN_ONLY_CANDIDATE_FIELDS and isinstance(row["grade_fixed"], bool)
    # 결정에서 온 값(status·management 포함)은 남겨 두는 분류에 하나도 없다
    assert not ({"status", "grade_fixed", "management", "final_grade"} & access.BLIND_KEPT_CANDIDATE_FIELDS)
    # 문서를 가리키는 값은 남겨 두는 분류에 없다 — 별칭으로 바꿔 나간다(2026-09-22 측정: doc_id 988/1,067건·title 1건이 등급 코드를 품는다)
    assert not ({"doc_id", "title"} & access.BLIND_KEPT_CANDIDATE_FIELDS)


def test_every_decision_event_grade_key_is_known(h):
    """이벤트는 후보 dict 와 별개로 proposed_grade 를 **복사해** 들고 있다(decide). 자기 결정에서도 빼야 한다."""
    _full_candidate(h)
    events = [json.loads(x) for x in h.decision_lines()]
    assert events
    grade_keys = {k for e in events for k in e if "grade" in k}
    assert grade_keys == {"proposed_grade", "final_grade"}, (
        f"이벤트에 등급 키가 새로 생겼다: {sorted(grade_keys)} — 숨김에서 다룰지 정하라")
    assert "proposed_grade" in access.BLIND_HIDDEN_EVENT_FIELDS


def test_every_decision_event_key_is_classified_exactly_once(h):
    """decide() 가 원장에 적는 이벤트의 모든 키는 (숨김 / 다시 만듦 / 자기 것 / 문서 사실) 넷 중 정확히 하나다.

    이벤트는 후보 dict 와 따로 자란다 — 후보 분류 시험은 이벤트 안의 새 키(예: management_before)를 못 본다.
    M 을 준 결정과 안 준 결정을 다 적어 본 뒤 키의 합집합을 분류표와 대조한다."""
    svc = ProxyGoldCandidateService()
    svc.decide(doc_id=A1, action="approve", actor_id="admin-kim")
    svc.decide(doc_id=A2, action="change", grade="S1", reason="r", actor_id="x", security_marking="secret")
    svc.decide(doc_id=A3, action="defer", reason="r", actor_id="y", access_scope="designated")
    svc.decide(doc_id=B1, action="reopen", actor_id="y")
    svc.decide(doc_id=B3, action="change", grade="S3", reason="r", actor_id="y")      # 실문서: provenance_at_decision 값
    seen = {k for x in h.decision_lines() for k in json.loads(x)}
    classified = _exactly_one(
        access.BLIND_HIDDEN_EVENT_FIELDS, access.BLIND_REBUILT_EVENT_FIELDS, access.BLIND_OWN_EVENT_FIELDS,
        access.BLIND_ALIASED_EVENT_FIELDS, access.BLIND_PUBLIC_EVENT_FIELDS)
    # event_kind 는 결정 이벤트에는 없고(출처 기록 같은 다른 종류에만 있다) 문서 사실로 분류만 해 둔 것이다
    assert seen == classified - {"event_kind"}, (
        f"분류 안 된 이벤트 키: {sorted(seen - classified)} · 이벤트에 없는 분류: {sorted(classified - seen)}")


def test_every_summary_key_is_classified_exactly_once(h):
    """집계(summary · 목록 안 summary · batch_summary)의 모든 키도 셋 중 정확히 하나다 — 결정을 세는 키는 자기 것으로
    다시 세고(결함 3), 등급·길이 분포는 뺀다."""
    svc = ProxyGoldCandidateService()
    svc.decide(doc_id=A1, action="approve", actor_id="admin-kim")
    summary = svc.summary()
    listed = svc.list_candidates()
    classified = _exactly_one(
        access.BLIND_HIDDEN_SUMMARY_FIELDS, access.BLIND_OWN_SUMMARY_FIELDS, access.BLIND_KEPT_SUMMARY_FIELDS)
    # /summary 와 목록 안의 summary 는 같은 집계 + (목록만) scope. quality 는 둘 다 있다
    assert set(summary) | set(listed["summary"]) == classified, (
        sorted((set(summary) | set(listed["summary"])) ^ classified))
    grade_like = {k for k in summary if "grade" in k or k == "quality"}
    # actual_grade_fixed_unlocked 는 '등급을 확정한 실문서 **건수**'(상태 집계)라 등급 값을 담지 않는다 → 결정 집계(자기 것)
    assert grade_like == {"by_final_grade", "by_proposed_grade", "quality", "actual_grade_fixed_unlocked"}
    assert grade_like - {"actual_grade_fixed_unlocked"} == access.BLIND_HIDDEN_SUMMARY_FIELDS
    assert isinstance(summary["actual_grade_fixed_unlocked"], int)
    batch_classified = _exactly_one(
        access.BLIND_HIDDEN_BATCH_SUMMARY_FIELDS, access.BLIND_OWN_BATCH_SUMMARY_FIELDS,
        access.BLIND_KEPT_BATCH_SUMMARY_FIELDS)
    assert set(listed["batch_summary"]) == batch_classified
    assert {k for k in listed["batch_summary"] if "grade" in k} == access.BLIND_HIDDEN_BATCH_SUMMARY_FIELDS
    # quality 를 왜 통째로 빼나: 등급별 길이 최소·중앙·최대를 주므로 길이로 등급을 추정할 수 있다
    assert "length_by_grade" in summary["quality"]


def test_unclassified_keys_do_not_leave_the_server_fail_closed():
    """응답은 분류표를 **허용 목록**으로 만든다 — 표에 없는 키는(시험이 실패하기 전에도) 나가지 않는다."""
    scope = access.ReviewerScope(restricted=True, actor_id="expert-a", blind=True)
    an_alias = "RV-0123456789ABCDEF"
    assert scope.shape_summary({"total": 1, "fixed": 0, "brand_new_count": 9}) == {"total": 1, "fixed": 0}
    listed = scope.shape_list({
        "total": 1, "candidates": [{"doc_id": an_alias, "brand_new_key": "누설", "proposed_grade": "TS"}],
        "summary": {"total": 1, "new_dist": {"TS": 1}}, "batch_summary": {"total": 1, "new_dist": {"TS": 1}}})
    assert listed["candidates"] == [{"doc_id": an_alias}]
    assert listed["summary"] == {"total": 1} and listed["batch_summary"] == {"total": 1}
    event = {"event_id": "e", "actor_id": "expert-a", "reason": "r", "brand_new_key": "누설", "management_before": {}}
    assert access.BlindView("expert-a")._shape_event(event, {}) == {
        "event_id": "e", "actor_id": "expert-a", "reason": "r"}
    # 별칭 모양이 아닌 doc_id 는 허용 목록을 통과해도 **나가지 않는다** — 시야(BlindView)를 안 거친 행이라는 뜻이다
    with pytest.raises(access.AliasLayerError):
        scope.shape_list({"candidates": [{"doc_id": "GOLD-T-TS-001"}]})


# ══ 2. 서버 응답에서 실제로 빠졌나 — 역할 3종 × 라우트 ═══════════════════════════════════════════


def _reviewer_responses(h, who) -> dict[str, dict]:
    """검수자가 닿는 후보 응답 전부(목록·집계·이력·상세·결정 응답). 배정 안 된 문서는 제외."""
    out = {
        "list": h.get(who, "/golden/candidates").json(),
        "summary": h.get(who, "/golden/candidates/summary").json(),
        "decisions": h.get(who, "/golden/candidates/decisions").json(),
    }
    for doc in (A1, A3):
        out[f"detail:{doc}"] = h.get(who, f"/golden/candidates/{doc}").json()
    return out


@pytest.mark.parametrize("assignment", [False, True])
def test_no_answer_key_reaches_a_blind_reviewer_on_any_route(h, monkeypatch, assignment):
    """숨김만 켠 경우(배정 꺼짐 — 전 문서가 보임)와 둘 다 켠 경우 모두."""
    h.standard_assignments()
    blind(monkeypatch, assignment=assignment)
    # 관리자가 결정을 남긴 뒤에도 — 이벤트에 proposed_grade 가 복사돼 있다
    h.post(ADMIN, f"/golden/candidates/{A1}/decision", json={"action": "approve"})
    for who in (EXPERT_A, EXPERT_B):
        for name, payload in _reviewer_responses(h, who).items():
            leaked = walk_keys(payload) & ANSWER_KEYS
            assert not leaked, f"{who[1]} · {name}: 정답을 암시하는 키가 응답에 있다 {sorted(leaked)}"
    # 자기 결정 응답에도 없다(latest_decision 은 이벤트 그대로라 proposed_grade 가 실려 있다)
    r = h.post(EXPERT_A, f"/golden/candidates/{A2}/decision",
               json={"action": "change", "grade": "S1", "reason": "본문 근거"})
    assert r.status_code == 200
    assert not (walk_keys(r.json()) & ANSWER_KEYS), r.json()
    assert r.json()["final_grade"] == "S1", "자기 결정의 등급은 보여야 한다"
    assert not (walk_keys(h.get(EXPERT_A, f"/golden/candidates/{A2}").json()) & ANSWER_KEYS)


def test_default_flags_off_reviewer_still_sees_the_proposals(h):
    """이 시험이 공허하지 않다는 증거 — 손잡이가 꺼져 있으면 같은 응답에 그 키들이 실제로 있다."""
    payloads = _reviewer_responses(h, EXPERT_A)
    assert walk_keys(payloads["list"]) >= {"proposed_grade", "by_proposed_grade", "quality", "document_path"}
    assert "proposed_grade" in payloads[f"detail:{A1}"] and payloads[f"detail:{A1}"]["proposed_grade"] == "TS"
    assert payloads["summary"]["by_proposed_grade"]
    b3 = h.get(EXPERT_A, f"/golden/candidates/{B3}").json()
    assert b3["proposed_grade"] == "S3" and b3["proposed_grade_basis"], "공개 실문서는 S3 제안 + 근거 문구가 붙는다"


@pytest.mark.parametrize("who", [ADMIN, KL_BACKEND, SYSTEM])
def test_admin_like_roles_always_get_the_full_response_when_blind_is_on(h, monkeypatch, who):
    blind(monkeypatch)
    payloads = _reviewer_responses(h, who)
    assert walk_keys(payloads["list"]) >= {"proposed_grade", "by_proposed_grade", "quality", "document_path"}
    assert payloads[f"detail:{A1}"]["proposed_grade"] == "TS"
    assert {"by_final_grade", "by_proposed_grade", "quality"} <= set(payloads["summary"])
    assert h.listed_ids(who) == set(POOL)


def test_blind_reviewer_keeps_everything_needed_to_review(h, monkeypatch):
    """뺄 것만 빼고 검수에 필요한 것은 그대로다 — 본문·출처·상태·M 입력·배치."""
    blind(monkeypatch, assignment=False)
    d = h.get(EXPERT_A, f"/golden/candidates/{A1}").json()
    for key in ("doc_id", "title", "text", "status", "document_origin", "review_batch", "characters",
                "document_sha256", "provenance", "management", "claim_scope", "is_actual_document"):
        assert key in d, f"검수에 필요한 {key} 가 사라졌다"
    assert "BODY-" + A1 in d["text"] and d["review_batch"] == "batch-A"
    listed = h.get(EXPERT_A, "/golden/candidates").json()
    assert listed["total"] == 7 and listed["available_batches"], "목록·배치 선택지는 그대로"
    assert {"total", "fixed", "unfixed", "deferred", "discarded", "by_status", "by_origin"} <= set(listed["summary"])


# ══ 3. grade 필터는 정답을 캐는 통로다 ═════════════════════════════════════════════════════════


def test_grade_filter_is_an_oracle_without_blind_and_refused_with_it(h, monkeypatch):
    # 꺼진 상태: grade=TS 로 좁히면 TS 로 제안된 문서만 남는다 — 응답 필드를 빼도 이 필터가 남으면 새는 통로
    assert h.listed_ids(EXPERT_A, "?grade=TS") == {A1, "GOLD-T-TS-005"}
    blind(monkeypatch, assignment=False)
    for g in ("TS", "S1", "S2", "S3"):
        r = h.get(EXPERT_A, f"/golden/candidates?grade={g}")
        assert r.status_code == 422 and "blind" in r.text, g
    # 잘못된 값은 종전대로 422, 관리자는 그대로 쓴다
    assert h.get(EXPERT_A, "/golden/candidates?grade=XX").status_code == 422
    assert h.listed_ids(ADMIN, "?grade=TS") == {A1, "GOLD-T-TS-005"}
    # 다른 필터는 정답을 캐지 못하므로 그대로 쓴다
    assert h.listed_ids(EXPERT_A, "?status=proposed") == set(POOL)


# ══ 4. 결정 — '제안 등급 그대로 확정'은 거절, 등급을 직접 지정 ═══════════════════════════════════════


def test_blind_reviewer_can_not_confirm_the_proposal_and_must_pick_a_grade(h, monkeypatch):
    h.standard_assignments()
    blind(monkeypatch)
    before = h.decision_lines()
    r = h.post(EXPERT_A, f"/golden/candidates/{A1}/decision", json={"action": "approve"})
    assert r.status_code == 403 and "blind" in r.text
    assert h.decision_lines() == before, "거절된 approve 가 원장에 남았다"

    ok = h.post(EXPERT_A, f"/golden/candidates/{A1}/decision",
                json={"action": "change", "grade": "TS", "reason": "본문의 영업비밀 근거"})
    assert ok.status_code == 200 and ok.json()["final_grade"] == "TS"
    ev = json.loads(h.decision_lines()[-1])
    assert ev["actor_id"] == EXPERT_A[1] and ev["action"] == "change" and ev["final_grade"] == "TS"

    # 등급을 정하지 않는 결정은 제안과 무관하므로 그대로 쓴다
    for doc, action in ((A2, "defer"), (A3, "discard"), (A1, "exclude")):
        r = h.post(EXPERT_A, f"/golden/candidates/{doc}/decision", json={"action": action, "reason": "사유"})
        assert r.status_code == 200, (action, r.text)


def test_approve_is_still_allowed_for_admins_and_when_blind_is_off(h, monkeypatch):
    r = h.post(EXPERT_A, f"/golden/candidates/{A1}/decision", json={"action": "approve"})
    assert r.status_code == 200 and r.json()["final_grade"] == "TS", "꺼진 상태 = 종전 동작"
    blind(monkeypatch)
    for who, doc in ((ADMIN, A2), (KL_BACKEND, A3)):
        r = h.post(who, f"/golden/candidates/{doc}/decision", json={"action": "approve"})
        assert r.status_code == 200, (who, r.text)


# ══ 5. 다른 검수자의 판단은 앵커다 ══════════════════════════════════════════════════════════════


def test_other_experts_decisions_are_hidden_but_own_decisions_stay_visible(h, monkeypatch):
    """A3 는 expert-a·expert-b 가 함께 배정받은 문서다. a 가 정한 등급을 b 가 보면 독립 판정이 아니다."""
    h.standard_assignments()
    blind(monkeypatch)
    h.post(EXPERT_A, f"/golden/candidates/{A3}/decision",
           json={"action": "change", "grade": "S1", "reason": "a 의 판단"})

    # 본인은 자기 결정이 보인다(proposed_grade 만 빠짐)
    mine = h.get(EXPERT_A, f"/golden/candidates/{A3}").json()
    assert mine["final_grade"] == "S1" and mine["latest_decision"]["actor_id"] == EXPERT_A[1]
    assert [e["reason"] for e in mine["decision_history"]] == ["a 의 판단"]
    assert "proposed_grade" not in mine["latest_decision"]

    # 다른 검수자에게는 a 의 등급·사유·이력·행위자가 없다
    theirs = h.get(EXPERT_B, f"/golden/candidates/{A3}").json()
    assert theirs["final_grade"] is None and theirs["latest_decision"] is None
    assert theirs["decision_history"] == []
    assert "a 의 판단" not in json.dumps(theirs, ensure_ascii=False) and EXPERT_A[1] not in json.dumps(theirs)
    row = next(c for c in h.get(EXPERT_B, "/golden/candidates").json()["candidates"] if c["doc_id"] == h.alias(A3))
    assert row["final_grade"] is None and row["latest_decision"] is None
    assert (theirs["status"], theirs["grade_fixed"]) == ("proposed", False), "남의 결정이 status 로 샌다"
    assert (row["status"], row["grade_fixed"]) == ("proposed", False)
    assert h.get(EXPERT_B, "/golden/candidates/decisions").json() == {"total": 0, "by_action": {}, "events": []}
    assert "by_final_grade" not in h.get(EXPERT_B, "/golden/candidates/summary").json()
    # 관리자는 둘 다 본다
    adm = h.get(ADMIN, f"/golden/candidates/{A3}").json()
    assert adm["final_grade"] == "S1" and adm["latest_decision"]["actor_id"] == EXPERT_A[1]

    # 숨김을 끄면(배정만 강제) 종전 그대로 다른 사람의 결정이 보인다
    set_flags(monkeypatch, assignment=True, blind=False)
    assert h.get(EXPERT_B, f"/golden/candidates/{A3}").json()["final_grade"] == "S1"


def test_other_experts_management_input_is_hidden_but_loaded_and_own_values_stay(h, monkeypatch):
    """M(보안표시·접근범위)은 등급을 거의 단독으로 가른다. 다른 검수자가 결정하며 적은 M 은 그 사람의 판단이라
    가리고, 적재 때 들어온 값(문서의 사실)과 자기가 적은 값은 그대로 보인다.

    가린 자리에 '가려졌다'는 표식을 내지도 않는다 — 표식이 있으면 남이 적었다는 사실이 샌다. 남이 결정하기
    **전과 후의 화면이 똑같아야** 한다(불변성)."""
    meta = h.root / f"{A3}.metadata.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["management"] = {"security_marking": "confidential"}          # 적재 값 — recorded_by 없음
    meta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    h.standard_assignments()
    blind(monkeypatch)

    def m(who):
        return h.get(who, f"/golden/candidates/{A3}").json()["management"]

    assert m(EXPERT_A)["security_marking"] == "confidential" == m(EXPERT_B)["security_marking"], "적재 값은 둘 다 본다"
    b_before = m(EXPERT_B)
    assert b_before["recorded_by"] is None and b_before["state"] == "present" and b_before["level"] == 1

    r = h.post(EXPERT_A, f"/golden/candidates/{A3}/decision", json={
        "action": "change", "grade": "S1", "reason": "판단", "security_marking": "secret", "access_scope": "designated"})
    assert r.status_code == 200, r.text
    mine = m(EXPERT_A)
    assert (mine["security_marking"], mine["access_scope"], mine["recorded_by"]) == ("secret", "designated", EXPERT_A[1])
    theirs = m(EXPERT_B)
    assert theirs == b_before, "다른 검수자가 결정한 뒤 화면이 달라졌다 — M 입력 또는 결정 사실이 샌다"
    assert "secret" not in json.dumps(h.get(EXPERT_B, f"/golden/candidates/{A3}").json())
    row = next(c for c in h.get(EXPERT_B, "/golden/candidates").json()["candidates"] if c["doc_id"] == h.alias(A3))
    assert row["management"] == b_before
    assert h.get(ADMIN, f"/golden/candidates/{A3}").json()["management"]["security_marking"] == "secret"
    # 숨김을 끄면 종전 그대로 보인다
    set_flags(monkeypatch, assignment=True, blind=False)
    assert m(EXPERT_B)["security_marking"] == "secret"


def test_own_decisions_are_recognised_across_unicode_normalization_forms(h, monkeypatch):
    """한글 sub 가 NFD 로 로그인해도(원장에는 그 형태로 적힌다) 배정(NFC)·자기 결정 판별이 같은 사람으로 본다."""
    nfc = unicodedata.normalize("NFC", "김전문")
    nfd = unicodedata.normalize("NFD", "김전문")
    assert nfc != nfd
    assert h.assign(nfc, review_batch="batch-A").status_code == 200
    blind(monkeypatch)
    who = ("reviewer", nfd)
    r = h.post(who, f"/golden/candidates/{A1}/decision", json={"action": "change", "grade": "TS", "reason": "판단"})
    assert r.status_code == 200 and r.json()["final_grade"] == "TS"
    assert json.loads(h.decision_lines()[-1])["actor_id"] == nfd, "원장에는 로그인 때의 형태 그대로 적힌다"
    d = h.get(who, f"/golden/candidates/{A1}").json()
    assert d["final_grade"] == "TS" and len(d["decision_history"]) == 1
    ev = h.get(who, "/golden/candidates/decisions").json()
    assert ev["total"] == 1 and [h.real(e["doc_id"]) for e in ev["events"]] == [A1], "결정 원장 화면이 자기 결정을 못 찾는다"


def test_blind_decision_ledger_view_is_own_events_only_and_limit_is_applied_after(h, monkeypatch):
    h.standard_assignments()
    h.post(ADMIN, f"/golden/candidates/{A1}/decision", json={"action": "approve"})            # 관리자 결정(제안 포함)
    blind(monkeypatch)
    h.post(EXPERT_A, f"/golden/candidates/{A2}/decision", json={"action": "change", "grade": "S2", "reason": "판단"})
    h.post(ADMIN, f"/golden/candidates/{A3}/decision", json={"action": "defer", "reason": "관리자 메모"})
    out = h.get(EXPERT_A, "/golden/candidates/decisions").json()
    assert [h.real(e["doc_id"]) for e in out["events"]] == [A2]
    assert out["total"] == 1 and out["by_action"] == {"change": 1}
    assert "proposed_grade" not in out["events"][0] and out["events"][0]["final_grade"] == "S2"
    # 관리자가 나중에 이벤트를 더 남겨도 limit=1 이 내 이벤트를 밀어내지 않는다
    for i in range(3):
        h.post(ADMIN, f"/golden/candidates/{A3}/decision", json={"action": "defer", "reason": f"메모 {i}"})
    assert [h.real(e["doc_id"]) for e in h.get(EXPERT_A, "/golden/candidates/decisions", params={"limit": 1}).json()["events"]] == [A2]


# ══ 5b. 두 검수자가 서로 다른 입력으로 결정한다 — 남의 결정은 내 화면에 아무 흔적도 남기지 않는다 ══════════
#
# 독립 검증(2026-09-21)이 재현한 결함 셋을 시나리오로 옮겼다. 셋은 뿌리가 하나다 — 응답이 **저장된 값**(남의
# 결정이 섞인 병합 결과)을 그대로 실었다. 고친 뒤에는 검수자 R 의 응답이 R 자신의 결정만으로 정해진다.
#
#   결함 1  이벤트의 management_before 에 남이 적은 M 값과 신원(recorded_by)이 그대로 실렸다
#   결함 2  결정 시 M 은 '이번에 준 칸만 덮고 나머지는 이전 값을 잇는' 병합이라, 내가 한 칸만 보냈는데 남이 적은
#           다른 칸이 내가 적은 것처럼 보였다
#   결함 3  status·grade_fixed 와 집계(fixed·by_status·batch_summary)가 남의 결정을 따라갔다
#
# 두 사람이 **서로 겹치지 않는 값**을 쓰므로 어느 응답에 누구의 값이 보이는지 문자열로 가려낼 수 있다.
# 방향은 둘 다다: (a 먼저 → b 나중) 과 (b 먼저 → a 나중). 한쪽 방향만 막은 코드도 있을 수 있기 때문이다.

P1 = dict(grade="TS", reason="SENTINEL-P1-사유", security_marking="top_secret", access_scope="approved_only")
P2 = dict(grade="S2", reason="SENTINEL-P2-사유", security_marking="confidential", access_scope="designated")
DIRECTIONS = [(EXPERT_A, EXPERT_B), (EXPERT_B, EXPERT_A)]        # (먼저 결정하는 사람, 나중에 결정하는 사람)
# 상태 필터 — 합성 후보는 아래 다섯 상태로만 간다(under_review·grade_fixed_unlocked 는 실문서 전용)
ALL_STATUSES = ("proposed", "approved_proxy", "deferred", "discarded", "out_of_scope")


def decide_as(h, who, doc, profile, **override) -> dict:
    """action=change 로 결정한다. override 값이 None 이면 그 칸은 요청에서 뺀다(한 칸만 보내는 시나리오)."""
    fields = {k: v for k, v in {**profile, **override}.items() if v is not None}
    r = h.post(who, f"/golden/candidates/{doc}/decision", json={"action": "change", **fields})
    assert r.status_code == 200, r.text
    return r.json()


def traces(who, profile) -> list[str]:
    """이 사람의 결정이 응답에 남길 수 있는 문자열 — 신원·사유·M 입력값."""
    return [who[1], profile["reason"], profile["security_marking"], profile["access_scope"]]


def reviewer_views(h, who, docs=(A3,)) -> dict:
    """검수자가 닿는 후보·집계·결정 원장 응답 전부 — (상태코드, 본문). 상태 필터마다 따로 부른다(필터도 통로다)."""
    paths = ["/golden/candidates", "/golden/candidates/summary", "/golden/candidates/decisions"]
    paths += [f"/golden/candidates?status={s}" for s in ALL_STATUSES]
    paths += [f"/golden/candidates/{d}" for d in docs]
    out = {}
    for path in paths:
        r = h.get(who, path)
        out[path] = (r.status_code, r.json())
    return out


def assert_no_trace(payload, needles, where: str) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for needle in needles:
        assert needle not in text, f"{where}: 다른 검수자의 흔적 {needle!r} 이 응답에 있다"
    assert "management_before" not in walk_keys(payload), f"{where}: 저장된 직전 M(management_before)이 나간다"


# ── 불변성: 남이 무엇을 결정해도 내 응답은 바이트 단위로 그대로다 ────────────────────────────────────
#
# 키를 하나씩 막는 시험은 아직 이름 없는 통로를 못 본다. 이 시험은 응답 전체를 결정 전후로 비교하므로 새는 곳이
# 어디든(이벤트·후보·집계·필터·정렬 결과) 잡힌다. 남의 결정은 종류를 다 돌린다 — 등급 확정(M 포함)·폐기·보류·
# 검수 대상 아님·재검토·관리자의 승인.
PEER_DECISIONS = {
    "peer-change-with-M": ("peer", dict(action="change", **P1)),
    "peer-discard": ("peer", dict(action="discard", reason="SENTINEL-폐기")),
    "peer-defer-with-M": ("peer", dict(action="defer", reason="SENTINEL-보류", security_marking="secret")),
    "peer-exclude": ("peer", dict(action="exclude", reason="SENTINEL-제외")),
    "peer-reopen": ("peer", dict(action="reopen")),
    "admin-approve": (ADMIN, dict(action="approve")),
    "admin-change-with-M": (ADMIN, dict(action="change", **P1)),
    "admin-discard": (ADMIN, dict(action="discard", reason="SENTINEL-폐기")),
}
# 내가 이미 결정한 상태에서도 확인하는 것은 결과가 서로 다른 셋(등급 확정+M · 폐기 · 관리자의 등급 확정)이면 충분하다
_OWN_DECIDED_TOO = {"peer-change-with-M", "peer-discard", "admin-change-with-M"}
INVARIANCE_CASES = [(label, False) for label in PEER_DECISIONS] + [(label, True) for label in sorted(_OWN_DECIDED_TOO)]


@pytest.mark.parametrize("label, has_own_decision", INVARIANCE_CASES,
                         ids=[f"{lb}-{'R-decided' if own else 'R-undecided'}" for lb, own in INVARIANCE_CASES])
@pytest.mark.parametrize("reviewer, peer", DIRECTIONS, ids=["a-watches-b", "b-watches-a"])
def test_reviewer_responses_are_byte_identical_before_and_after_anyone_elses_decision(
        h, monkeypatch, reviewer, peer, label, has_own_decision):
    other, body = PEER_DECISIONS[label]
    h.standard_assignments()
    blind(monkeypatch)
    if has_own_decision:
        decide_as(h, reviewer, A3, P2)
    docs = sorted(set(POOL) & (
        {A1, A2, A3} if reviewer == EXPERT_A else {B1, B2, A3}))
    before = reviewer_views(h, reviewer, docs)
    actor = peer if other == "peer" else other
    # 관리자는 이 검수자의 다른 문서에도 결정을 남긴다(목록·집계·batch_summary 가 흔들리면 안 된다)
    targets = docs if actor == ADMIN else [A3]
    for doc in targets:
        r = h.post(actor, f"/golden/candidates/{doc}/decision", json=body)
        assert r.status_code == 200, (label, doc, r.text)
    after = reviewer_views(h, reviewer, docs)
    assert after == before, f"{label}: 남의 결정 뒤 {reviewer[1]} 의 응답이 달라졌다"
    # 이 시험이 공허하지 않다는 증거 — 같은 결정이 관리자 화면에는 실제로 보인다
    assert len(h.decision_lines()) >= len(targets) + int(has_own_decision)


# ── 결함 1 ───────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("first, second", DIRECTIONS, ids=["a-then-b", "b-then-a"])
def test_defect1_foreign_management_before_and_identity_never_reach_the_second_reviewer(
        h, monkeypatch, first, second):
    """먼저 결정한 사람이 M(top_secret)을 적고, 나중 사람이 다른 값(none)으로 결정한다. 나중 사람의 결정 응답·상세·
    이력·결정 원장 어디에도 먼저 사람의 M 입력·신원이 없어야 한다(재현: 결정 응답의 management_before)."""
    h.standard_assignments()
    blind(monkeypatch)
    decide_as(h, first, A3, P1)
    resp = decide_as(h, second, A3, P2, security_marking="none", access_scope=None)
    views = reviewer_views(h, second)
    for where, payload in {"결정 응답": resp, **views}.items():
        assert_no_trace(payload if where == "결정 응답" else payload[1], traces(first, P1), where)

    detail = views[f"/golden/candidates/{A3}"][1]
    own_events = [detail["latest_decision"], *detail["decision_history"], resp["latest_decision"],
                  *views["/golden/candidates/decisions"][1]["events"]]
    assert len(own_events) == 4 and all(e["actor_id"] == second[1] for e in own_events)
    expected = {"security_marking": "none", "access_scope": None, "state": "unknown", "level": None,
                "reason": "icd_no_management_metadata", "recorded_by": second[1]}
    for e in own_events:
        assert {k: v for k, v in e["management_after"].items() if k != "recorded_at"} == expected, e
    assert {k: v for k, v in detail["management"].items() if k != "recorded_at"} == expected

    # 반대쪽도 같다 — 먼저 사람의 화면에 나중 사람의 값·신원이 없다
    first_views = reviewer_views(h, first)
    for where, (_status, payload) in first_views.items():
        assert_no_trace(payload, [second[1], P2["reason"]], f"먼저 결정한 사람 · {where}")
    fm = first_views[f"/golden/candidates/{A3}"][1]["management"]
    assert (fm["security_marking"], fm["access_scope"], fm["recorded_by"]) == ("top_secret", "approved_only", first[1])


# ── 결함 2 ───────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("first, second", DIRECTIONS, ids=["a-then-b", "b-then-a"])
def test_defect2_a_field_the_first_reviewer_wrote_is_not_shown_as_the_second_reviewers(h, monkeypatch, first, second):
    """먼저 사람은 access_scope 만, 나중 사람은 security_marking 만 보낸다. 서버는 이전 값을 이어 병합하므로 저장된
    값에는 두 칸이 다 있다 — 나중 사람의 화면·management_after 에 먼저 사람의 access_scope 가 섞이면 안 된다."""
    h.standard_assignments()
    blind(monkeypatch)
    decide_as(h, first, A3, P1, security_marking=None)                  # access_scope=approved_only 만
    resp = decide_as(h, second, A3, P2, access_scope=None)              # security_marking=confidential 만
    views = reviewer_views(h, second)
    detail = views[f"/golden/candidates/{A3}"][1]
    events = views["/golden/candidates/decisions"][1]["events"]
    for where, payload in {"결정 응답": resp, **{p: v[1] for p, v in views.items()}}.items():
        assert_no_trace(payload, traces(first, P1), where)

    mgmt = detail["management"]
    assert (mgmt["security_marking"], mgmt["access_scope"]) == ("confidential", None), "남의 칸이 내 것처럼 보인다"
    assert (mgmt["state"], mgmt["level"], mgmt["recorded_by"]) == ("present", 1, second[1])
    for e in (resp["latest_decision"], detail["latest_decision"], detail["decision_history"][-1], events[0]):
        after = e["management_after"]
        assert (after["security_marking"], after["access_scope"], after["level"]) == ("confidential", None, 1), e

    # 원장·관리자 시야는 병합 결과 그대로다 — 저장 형식을 바꾸지 않았다
    last = json.loads(h.decision_lines()[-1])
    assert last["management_before"]["access_scope"] == "approved_only" and last["management_before"]["recorded_by"] == first[1]
    assert (last["management_after"]["security_marking"], last["management_after"]["access_scope"]) == (
        "confidential", "approved_only")
    admin_mgmt = h.get(ADMIN, f"/golden/candidates/{A3}").json()["management"]
    assert (admin_mgmt["security_marking"], admin_mgmt["access_scope"], admin_mgmt["recorded_by"]) == (
        "confidential", "approved_only", second[1])
    # 먼저 사람의 화면: 자기 칸(access_scope)만 — 나중 사람의 security_marking 은 안 보인다
    fm = h.get(first, f"/golden/candidates/{A3}").json()["management"]
    assert (fm["security_marking"], fm["access_scope"], fm["recorded_by"]) == (None, "approved_only", first[1])


# ── 결함 3 ───────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("first, second", DIRECTIONS, ids=["a-then-b", "b-then-a"])
def test_defect3_status_grade_fixed_and_counts_follow_only_my_own_decisions(h, monkeypatch, first, second):
    h.standard_assignments()
    blind(monkeypatch)
    visible = 3                                                          # a: A1 A2 A3 · b: B1 B2 A3
    decide_as(h, first, A3, P1)

    def mine():
        detail = h.get(second, f"/golden/candidates/{A3}").json()
        listed = h.get(second, "/golden/candidates").json()
        row = next(c for c in listed["candidates"] if c["doc_id"] == h.alias(A3))
        return detail, row, listed, h.get(second, "/golden/candidates/summary").json()

    detail, row, listed, summary = mine()
    for view in (detail, row):
        assert (view["status"], view["grade_fixed"], view["final_grade"]) == ("proposed", False, None), (
            "다른 검수자가 이미 결정했다는 사실이 status·grade_fixed 로 샌다")
    for agg in (summary, listed["summary"]):
        assert (agg["fixed"], agg["unfixed"], agg["deferred"], agg["discarded"]) == (0, visible, 0, 0), agg
        assert agg["by_status"] == {"proposed": visible} and agg["actual_grade_fixed_unlocked"] == 0
    assert listed["batch_summary"]["terminal"] == 0 and listed["batch_summary"]["pending"] == visible
    assert listed["batch_summary"]["by_status"] == {"proposed": visible}
    # 상태 필터가 남의 결정을 캐는 통로가 되지 않는다
    assert h.listed_ids(second, "?status=approved_proxy") == set()
    assert A3 in h.listed_ids(second, "?status=proposed")

    # 먼저 사람이 폐기해도(폐기는 기본 목록에서 빠진다) 나중 사람의 목록·건수는 그대로다
    h.post(first, f"/golden/candidates/{A3}/decision", json={"action": "discard", "reason": "폐기"})
    detail, row, listed, summary = mine()
    assert row["status"] == "proposed" and listed["total"] == visible and A3 in h.listed_ids(second)
    assert summary["discarded"] == 0 and listed["summary"]["discarded"] == 0
    assert A3 not in h.listed_ids(first), "폐기한 본인에게는 기본 목록에서 빠진다(자기 결정)"
    assert A3 in h.listed_ids(first, "?status=discarded") and A3 not in h.listed_ids(second, "?status=discarded")

    # 나중 사람이 결정하면 그 결정이 자기 상태가 되고 집계가 다시 센다
    resp = decide_as(h, second, A3, P2)
    assert (resp["status"], resp["final_grade"]) == ("approved_proxy", "S2")
    detail, row, listed, summary = mine()
    assert (detail["status"], detail["grade_fixed"], detail["final_grade"]) == ("approved_proxy", True, "S2")
    assert (summary["fixed"], summary["unfixed"]) == (1, visible - 1)
    assert summary["by_status"] == {"approved_proxy": 1, "proposed": visible - 1}
    assert listed["batch_summary"]["terminal"] == 1 and listed["batch_summary"]["pending"] == visible - 1
    # 관리자는 원장 전체 기준(마지막 결정)이다
    adm = h.get(ADMIN, f"/golden/candidates/{A3}").json()
    assert adm["status"] == "approved_proxy" and adm["final_grade"] == "S2"


def test_reviewer_keeps_seeing_their_own_decision_after_someone_else_decides_later(h, monkeypatch):
    """a 가 결정한 뒤 b 가 나중에 결정해도 a 의 화면에는 a 자신의 결정이 그대로다(등급·상태·이력). 저장된
    '마지막 결정'이 b 의 것이어도 a 의 시야에는 들어오지 않는다. 관리자는 마지막 결정(b)을 본다."""
    h.standard_assignments()
    blind(monkeypatch)
    decide_as(h, EXPERT_A, A3, P2)
    decide_as(h, EXPERT_B, A3, P1)
    d = h.get(EXPERT_A, f"/golden/candidates/{A3}").json()
    assert (d["final_grade"], d["status"], d["grade_fixed"]) == ("S2", "approved_proxy", True)
    assert d["latest_decision"]["actor_id"] == EXPERT_A[1] and d["latest_decision"]["reason"] == P2["reason"]
    assert [e["reason"] for e in d["decision_history"]] == [P2["reason"]]
    assert (d["management"]["security_marking"], d["management"]["access_scope"]) == ("confidential", "designated")
    adm = h.get(ADMIN, f"/golden/candidates/{A3}").json()
    assert adm["final_grade"] == "TS" and adm["latest_decision"]["actor_id"] == EXPERT_B[1]
    assert len(adm["decision_history"]) == 2


def test_blind_reviewer_view_never_mutates_the_shared_candidate_cache(h, monkeypatch):
    """시야는 새 dict 로 만든다 — 후보 행은 프로세스 캐시를 공유하므로 제자리에서 바꾸면 관리자 화면이 오염된다."""
    h.standard_assignments()
    blind(monkeypatch)
    decide_as(h, EXPERT_B, A3, P1)
    admin_before = h.get(ADMIN, "/golden/candidates").json()
    for who in (EXPERT_A, EXPERT_B):                                     # 시야를 여러 번 만든 뒤에도
        h.get(who, "/golden/candidates")
        h.get(who, f"/golden/candidates/{A3}")
        h.get(who, "/golden/candidates/summary")
    assert h.get(ADMIN, "/golden/candidates").json() == admin_before
    row = next(c for c in admin_before["candidates"] if c["doc_id"] == A3)
    assert row["final_grade"] == "TS" and row["status"] == "approved_proxy" and row["management"]["recorded_by"] == EXPERT_B[1]


def test_pre_ledger_management_written_by_another_person_is_not_shown_but_a_loaded_value_is(h, monkeypatch):
    """원장에 결정이 없는데 메타데이터의 M 에 recorded_by 가 붙어 있으면(옛 기록·수작업) 그 사람의 판단이다 — 다른
    검수자에게는 안 보이고 적은 사람에게는 보인다. recorded_by 가 없는 값은 문서의 사실이라 모두에게 보인다."""
    meta = h.root / f"{A3}.metadata.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["management"] = {"security_marking": "secret", "recorded_by": EXPERT_B[1], "recorded_at": "2026-01-01T00:00:00+00:00"}
    meta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    h.standard_assignments()
    blind(monkeypatch)
    a = h.get(EXPERT_A, f"/golden/candidates/{A3}").json()["management"]
    b = h.get(EXPERT_B, f"/golden/candidates/{A3}").json()["management"]
    assert (a["security_marking"], a["recorded_by"], a["state"]) == (None, None, "unknown")
    assert (b["security_marking"], b["recorded_by"], b["state"]) == ("secret", EXPERT_B[1], "present")
    assert h.get(ADMIN, f"/golden/candidates/{A3}").json()["management"]["security_marking"] == "secret"


# ── 손잡이·역할별로 바뀌지 않아야 하는 것 ─────────────────────────────────────────────────────────────
def test_admin_like_views_and_the_ledger_keep_the_full_facts_when_blind_is_on(h, monkeypatch):
    """관리자·kl_backend·system 의 시야와 원장은 그대로다 — 원장은 append-only 로 전체 사실(남의 M 입력·병합 전후)을
    갖고, 숨김은 검수자에게 나가는 응답에서만 일어난다."""
    h.standard_assignments()
    blind(monkeypatch)
    decide_as(h, EXPERT_B, A3, P1)
    decide_as(h, EXPERT_A, A3, P2, access_scope=None)                    # 한 칸만 — 병합이 일어난다
    ledger = [json.loads(x) for x in h.decision_lines()]
    assert [e["actor_id"] for e in ledger] == [EXPERT_B[1], EXPERT_A[1]]
    second = ledger[1]
    assert second["management_before"]["recorded_by"] == EXPERT_B[1] and second["management_before"]["security_marking"] == "top_secret"
    assert second["management_after"]["access_scope"] == "approved_only", "병합 결과(남의 칸 포함)가 원장에 그대로 있어야 한다"
    assert ledger[0]["proposed_grade"] == "S2" and second["proposed_grade"] == "S2"
    for who in (ADMIN, KL_BACKEND, SYSTEM):
        assert h.get(who, "/golden/candidates/decisions").json()["events"] == list(reversed(ledger)), who
        d = h.get(who, f"/golden/candidates/{A3}").json()
        assert d["latest_decision"] == second and d["decision_history"] == ledger and d["proposed_grade"] == "S2"
        assert (d["status"], d["final_grade"]) == ("approved_proxy", "S2")
        assert (d["management"]["security_marking"], d["management"]["access_scope"], d["management"]["recorded_by"]) == (
            "confidential", "approved_only", EXPERT_A[1])
        summary = h.get(who, "/golden/candidates/summary").json()
        assert {"by_final_grade", "by_proposed_grade", "quality"} <= set(summary)


def test_without_blind_the_reviewer_still_gets_the_stored_events_unchanged(h, monkeypatch):
    """손잡이를 끄면 종전 그대로다 — 검수자도 저장된 이벤트(남의 것·management_before 포함)를 그대로 받는다."""
    h.standard_assignments()
    set_flags(monkeypatch, assignment=True, blind=False)
    decide_as(h, EXPERT_B, A3, P1)
    decide_as(h, EXPERT_A, A3, P2, access_scope=None)
    ledger = [json.loads(x) for x in h.decision_lines()]
    got = h.get(EXPERT_A, "/golden/candidates/decisions").json()
    assert got["events"] == list(reversed(ledger)) and got["total"] == 2
    d = h.get(EXPERT_A, f"/golden/candidates/{A3}").json()
    assert d["latest_decision"] == ledger[1] and d["decision_history"] == ledger
    assert d["management"]["access_scope"] == "approved_only" and d["proposed_grade"] == "S2"
    assert (d["status"], d["final_grade"]) == ("approved_proxy", "S2")


# ── 알려진 한계 ───────────────────────────────────────────────────────────────────────────────────
def test_known_limits_a_value_identical_to_the_first_reviewers_is_not_shown_back(h, monkeypatch):
    """이벤트는 '이번에 준 값'을 따로 싣지 않고 병합 전·후만 싣는다(저장 형식을 바꾸지 않는다). 그래서 내가 안 보낸
    칸과 **이전과 같은 값을 보낸 칸**을 가를 수 없다. 이전 값이 남의 것이면 그 값이 내 것처럼 보이는 것(결함 2)을
    막아야 하므로 둘 다 안 보이게 한다 — 남과 똑같은 값을 적은 사람은 자기 값이 안 보인다(안전한 쪽의 오차).
    새는 쪽으로는 오차가 없다. 이 시험은 그 한계가 그대로 있다는 것을 고정한다."""
    h.standard_assignments()
    blind(monkeypatch)
    decide_as(h, EXPERT_B, A3, P1, access_scope=None)                   # b: top_secret
    decide_as(h, EXPERT_A, A3, P1, access_scope=None)                   # a: 똑같이 top_secret
    a = h.get(EXPERT_A, f"/golden/candidates/{A3}").json()["management"]
    assert a["security_marking"] is None and a["state"] == "unknown", "한계가 사라졌다 — 보고서 risks 를 고쳐라"
    adm = h.get(ADMIN, f"/golden/candidates/{A3}").json()["management"]
    assert adm["security_marking"] == "top_secret" and adm["recorded_by"] == EXPERT_A[1]
    # 자기 결정 자체(등급·사유)는 그대로 보인다
    d = h.get(EXPERT_A, f"/golden/candidates/{A3}").json()
    assert d["final_grade"] == "TS" and d["latest_decision"]["reason"] == P1["reason"]


# ══ 6. 화면 — 서버가 뺀 위에 조정하고, 조정이 어긋나면 시험이 안다 ═════════════════════════════════

def _render() -> str:
    return golden_api._render_specledger_gold_console_html()


def test_blind_patch_anchors_are_all_alive():
    """치환 앵커가 하나라도 사라지면(화면 JS 가 고쳐지면) 그 조정이 조용히 안 걸린다 — 여기서 잡는다."""
    html = _render()
    for old, _new in golden_api._BLIND_VIEW_PATCHES:
        assert html.count(old) == 1, f"앵커가 화면에 정확히 한 번 있어야 한다: {old[:70]}"


def test_blind_screen_never_mentions_the_proposal(h, monkeypatch):
    blind(monkeypatch)
    r = h.get(EXPERT_A, "/golden/candidates/manage.html")
    assert r.status_code == 200
    html = r.text
    # 화면 JS 에 제안 등급을 쓰는 자리가 하나도 남지 않아야 한다 — 새로 생기면 서버가 뺀 값을 다시 쓰려는 것
    assert "proposed_grade" not in html, "숨김 화면 JS 에 제안 등급을 읽는 코드가 남았다"
    assert "제안 등급 그대로 확정" not in html and "option('approve'" not in html
    assert "['제안'," not in html
    assert '<select id="finalGrade"><option value="">등급을 선택하세요</option>' in html, "미리 고른 등급이 앵커다"
    assert "$('finalGrade').value=c.final_grade||'';" in html
    assert "확정할 등급을 선택하세요" in html, "빈 등급으로 저장되면 서버 422 원문이 화면에 뜬다"
    assert "독립 검수 모드" in html
    assert "#quality,a[href=\"#quality\"]{display:none!important}" in html and "#grade{display:none!important}" in html
    # 관리자 전용 조작을 감추는 기존 검수자 화면 위에 얹힌다
    assert "검수자 권한으로 열었습니다" in html and "#openUpload,#promote,#provBox{display:none!important}" in html


def test_screens_are_unchanged_unless_blind_is_on(h, monkeypatch):
    plain_reviewer = golden_api._as_reviewer_view(_render())
    # 기본(둘 다 꺼짐)과 배정만 켠 상태: 검수자 화면은 종전 그대로(제안 등급이 화면에 있다)
    for flags in (dict(), dict(assignment=True)):
        set_flags(monkeypatch, **flags)
        assert h.get(EXPERT_A, "/golden/candidates/manage.html").text == plain_reviewer, flags
    assert "proposed_grade" in plain_reviewer, "이 비교가 공허하지 않다는 증거: 종전 화면은 제안 등급을 쓴다"
    # 관리자 화면은 어떤 손잡이 조합에서도 그대로
    for flags in (dict(), dict(blind=True), dict(assignment=True, blind=True)):
        set_flags(monkeypatch, **flags)
        for who in (ADMIN, KL_BACKEND):
            assert h.get(who, "/golden/candidates/manage.html").text == _render(), (who, flags)


# 실제 DOM(jsdom)에서 숨김 화면을 눌러 본다 — 문자열 치환이 문법·동작을 깨지 않았는지는 HTML 을 읽어서는
# 모른다(node --check 는 실행 시점 오류를 못 잡는다). node 나 jsdom 이 없으면 이 시험만 건너뛴다 —
# tests/e2e_console 하니스와 같은 규칙이다(파이썬 CI 에 node 를 강제하지 않는다).
_DOM_PROBE = r'''
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(process.argv[3]);
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(process.argv[2], 'utf8');
const blind = true;
const errors = [];
const posts = [];
const cand = (extra = {}) => ({
  doc_id: 'RV-7K2M9Q4XN8D1T5HB', title: '제목 A1', status: 'proposed', document_origin: 'synthetic',
  requires_manual_audit: true, review_batch: 'batch-A', claim_scope: 'fictional', content_revision: 'v1',
  characters: 320, document_sha256: 'a'.repeat(64), grade_fixed: false, extraction: null,
  provenance: {}, source_file_sha256: null, management: { state: 'unknown', security_marking: null, access_scope: null, level: null },
  is_actual_document: false, latest_decision: null, final_grade: null,
  ...(blind ? {} : { proposed_grade: 'TS', proposed_grade_basis: null, document_path: 'x/GOLD-T-TS-001_문서.md' }), ...extra,
});
const summary = { total: 1, fixed: 0, unfixed: 1, deferred: 0, discarded: 0, out_of_scope: 0, by_status: { proposed: 1 },
  by_origin: { synthetic: 1 }, actual_document_intake: 0, actual_provenance_recorded: 0, actual_provenance_partial: 0,
  actual_provenance_legacy: 0, actual_grade_fixed_unlocked: 0, scope: 'assigned',
  ...(blind ? {} : { by_final_grade: {}, by_proposed_grade: { TS: 1 }, quality: { documents: 0 } }) };
const listBody = { total: 1, listed_excludes_discarded: true, summary,
  batch_summary: { total: 1, terminal: 0, pending: 1, deferred: 0, by_status: { proposed: 1 } },
  available_batches: [{ review_batch: 'batch-A', total: 1 }], candidates: [cand()] };
const detailBody = { ...cand(), text: '본문 표식 BODY 본문입니다.', decision_history: [] };

function jsonResp(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
}
const fetchStub = (url, opt = {}) => {
  const u = String(url);
  if (opt.method === 'POST') { posts.push({ url: u, body: JSON.parse(opt.body) }); return jsonResp({ doc_id: 'RV-7K2M9Q4XN8D1T5HB', status: 'approved_proxy', final_grade: JSON.parse(opt.body).grade || null, latest_decision: null }); }
  if (u.endsWith('/session')) return jsonResp({ actor_id: 'expert-a', auth_mode: 'jwt', actor_role: 'reviewer' });
  if (u.includes('/decisions')) return jsonResp({ total: 0, by_action: {}, events: [] });
  if (u.includes('/RV-7K2M9Q4XN8D1T5HB')) return jsonResp(detailBody);
  return jsonResp(listBody);
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
out.rows = d.getElementById('rows').textContent;
out.gradeCellText = d.querySelector('#rows .grade')?.textContent;
d.querySelector('#rows .candidate').click();
await tick(300);
out.metas = [...d.querySelectorAll('#metas span')].map(s => s.textContent);
out.actions = [...d.querySelectorAll('#action option')].map(o => o.value);
out.finalGradeValue = d.getElementById('finalGrade').value;
out.finalGradeOptions = [...d.querySelectorAll('#finalGrade option')].map(o => o.value || o.textContent);
// 등급 없이 저장
d.getElementById('action').value = 'change';
d.getElementById('action').dispatchEvent(new w.Event('change'));
d.getElementById('reason').value = '본문 근거';
d.getElementById('save').click();
await tick(200);
out.postsAfterBlankSave = posts.length;
out.saveMsg = d.getElementById('saveMsg').textContent;
d.getElementById('finalGrade').value = 'S2';
d.getElementById('save').click();
await tick(300);
out.posts = posts.map(p => p.body);
out.errors = errors;
console.log(JSON.stringify(out, null, 1));
w.close();
'''


def _jsdom_package_json():
    from pathlib import Path

    p = Path(__file__).resolve().parent / "e2e_console" / "package.json"
    return p if (p.parent / "node_modules" / "jsdom").is_dir() else None


def test_blind_screen_behaves_in_a_real_dom(h, monkeypatch, tmp_path):
    """숨김 화면을 jsdom 에 띄우고 목록→상세→저장을 눌러 본다(서버 응답은 숨김 모양으로 흉내)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    pkg = _jsdom_package_json()
    if not node or pkg is None:
        pytest.skip("node 또는 tests/e2e_console/node_modules/jsdom 이 없다 — 이 시험만 건너뜀")
    h.standard_assignments()
    blind(monkeypatch)
    html_path = tmp_path / "blind_manage.html"
    html_path.write_text(h.get(EXPERT_A, "/golden/candidates/manage.html").text, encoding="utf-8")
    js_path = tmp_path / "dom_probe.mjs"
    js_path.write_text(_DOM_PROBE, encoding="utf-8")
    run = subprocess.run([node, str(js_path), str(html_path), str(pkg)], capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert run.returncode == 0, run.stderr[-800:]
    out = json.loads(run.stdout)
    assert out["errors"] == [], out["errors"]
    assert out["gradeCellText"] == "–" and "TS" not in out["rows"].replace("RV-7K2M9Q4XN8D1T5HB", "")
    assert not any("제안" in m for m in out["metas"]), out["metas"]
    assert out["actions"] == ["change", "defer", "discard", "exclude"], "approve 선택지가 화면에 남았다"
    assert out["finalGradeValue"] == "" and out["finalGradeOptions"][0] == "등급을 선택하세요"
    assert out["postsAfterBlankSave"] == 0 and "등급을 선택하세요" in out["saveMsg"], (
        "등급을 안 고르고 저장하면 요청이 나가면 안 된다")
    assert out["posts"] == [{"action": "change", "reason": "본문 근거", "grade": "S2"}]


# ══ 7. 남는 것 — 제거할 수 없는 것은 한계로 못 박는다(보고서 risks 와 같은 사실) ═════════════════════

def test_known_limits_body_title_and_order_inputs_still_carry_what_the_server_can_not_remove(h, monkeypatch):
    """doc_id 는 이제 별칭으로 나간다(2026-09-22 — 이 시험이 종전에는 '실제 풀 1,067건 중 988건의 doc_id 가 등급 코드를
    품고 서버가 지울 수 없다'는 한계를 고정했다). 별칭 계층 자체는 tests/test_golden_alias_ids.py 가 잠근다.

    남는 것은 서버가 값을 바꿀 수 없는 문서 자체의 사실이다: 본문에 적힌 표기, 글자 수, 출처, 해시. 서버가 가리지 않는다
    (증거를 바꾸는 일이라 하지 않는다 — 풀 정리는 clean_candidate_answer_leak.py 몫). 이 시험은 그 한계가 **그대로 있다**는
    것을 고정한다. 누가 본문을 가리게 되면 이 시험이 실패해 보고서의 '남는 것' 문단을 고치게 만든다."""
    blind(monkeypatch, assignment=False)
    ids = h.listed_ids(EXPERT_A)
    assert {"GOLD-T-TS-001", "GOLD-T-S1-002", "GOLD-T-S2-003", "GOLD-T-S3-004"} <= ids      # Harness 가 별칭을 실 id 로 되돌린 값
    d = h.get(EXPERT_A, f"/golden/candidates/{A1}").json()
    assert d["doc_id"] == h.alias(A1) and "-TS-" not in d["doc_id"], "doc_id 가 별칭이 아니다"
    assert "BODY-" + A1 in d["text"], "본문은 그대로 나간다(이 시험 풀의 본문에는 자기 doc_id 가 적혀 있다 — 문서의 사실이다)"
    # 제목·글자 수·출처도 남는다 — 서버가 값을 바꾸지 않는다(제목은 자기 doc_id 가 박힌 자리만 별칭으로 바뀐다)
    assert d["title"] == f"제목 {h.alias(A1)}" and d["characters"] > 0 and d["document_origin"] == "synthetic"


# ══ 8. 이 시험이 정말 숨김을 잠그는가 — 무력화하면 잡아내야 한다 ═══════════════════════════════════════


def blind_leaks(h, monkeypatch) -> list[str]:
    blind(monkeypatch)
    leaks: list[str] = []
    payloads = _reviewer_responses(h, EXPERT_A)
    for name, payload in payloads.items():
        for key in sorted(walk_keys(payload) & ANSWER_KEYS):
            leaks.append(f"{name} 에 {key}")
    if h.get(EXPERT_A, "/golden/candidates", params={"grade": "TS"}).status_code != 422:
        leaks.append("grade 필터가 열려 있다")
    if h.post(EXPERT_A, f"/golden/candidates/{A2}/decision", json={"action": "approve"}).status_code != 403:
        leaks.append("approve 가 받아들여진다")
    if "독립 검수 모드" not in h.get(EXPERT_A, "/golden/candidates/manage.html").text:
        leaks.append("화면이 숨김 모드로 안 바뀐다")
    # 다른 검수자의 흔적 — b 가 공유 문서(A3)를 M 입력과 함께 결정하면 a 의 응답 어디에도 그 흔적이 없어야 한다
    h.post(EXPERT_B, f"/golden/candidates/{A3}/decision", json={
        "action": "change", "grade": "TS", "reason": "SENTINEL-B", "security_marking": "top_secret"})
    after = _reviewer_responses(h, EXPERT_A)
    text = json.dumps(after, ensure_ascii=False)
    for needle in ("SENTINEL-B", EXPERT_B[1], "top_secret"):
        if needle in text:
            leaks.append(f"다른 검수자의 흔적 {needle}")
    for name, payload in after.items():
        for key in sorted(walk_keys(payload) & ANSWER_KEYS):
            leaks.append(f"{name} 에 {key}")
    detail = h.get(EXPERT_A, f"/golden/candidates/{A3}").json()
    if detail.get("status") != "proposed" or detail.get("grade_fixed"):
        leaks.append("다른 검수자가 결정했다는 사실(status·grade_fixed)")
    return list(dict.fromkeys(leaks))


def test_blind_probe_finds_nothing_when_blind_is_on(h, monkeypatch):
    h.standard_assignments()
    assert blind_leaks(h, monkeypatch) == []


def test_blind_probe_detects_disabled_blind(h, monkeypatch):
    h.standard_assignments()
    monkeypatch.setattr(access, "resolve_scope", lambda auth: access.UNRESTRICTED)
    monkeypatch.setattr(access, "enforcement_active", lambda: False)
    leaks = blind_leaks(h, monkeypatch)
    for expected in ("list 에 proposed_grade", "list 에 quality", "list 에 by_proposed_grade",
                     f"detail:{A1} 에 proposed_grade", "summary 에 quality", "grade 필터가 열려 있다",
                     "approve 가 받아들여진다", "화면이 숨김 모드로 안 바뀐다",
                     "다른 검수자의 흔적 SENTINEL-B", f"다른 검수자의 흔적 {EXPERT_B[1]}",
                     "다른 검수자의 흔적 top_secret", "다른 검수자가 결정했다는 사실(status·grade_fixed)"):
        assert expected in leaks, f"무력화했는데 탐침이 못 잡았다: {expected} (잡은 것: {leaks})"
