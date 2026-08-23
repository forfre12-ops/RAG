"""비밀관리성(M) 입력 · 출처 게이트 이동 계약 시험 (2026-08-23).

세 가지가 함께 바뀌었다.
  ① 업로드는 출처를 강제하지 않는다 — 등록은 되고 **등급 확정만** 막힌다.
  ② 검수 결정에서 보안표시·접근범위를 받아 M 을 만든다(본문에서 관측되지 않는 축).
  ③ 승격(promote_to_locked)이 실문서의 반출 근거를 검사한다 — 단 표식 없는 레코드는 통과.
"""
import pytest

from koipa.golden_signoff import Signoff, promote_to_locked
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService

_BODY = ("실제 조직 운영 문서이며 등급 검토를 위한 본문을 담고 있습니다. " * 20).encode("utf-8")


def _upload(svc, **kw):
    kw.setdefault("filename", "ops.txt")
    kw.setdefault("content", _BODY)
    kw.setdefault("actor_id", "admin-kim")
    kw.setdefault("document_origin", "organization_real")
    return svc.create_uploaded_candidate(**kw)


# ── ① 업로드 완화 · 게이트 이동 ────────────────────────────────────────────
def test_upload_without_provenance_is_registered_but_cannot_fix_a_grade(tmp_path):
    svc = ProxyGoldCandidateService(tmp_path)
    c = _upload(svc)
    assert c["status"] == "under_review"
    assert c["provenance"]["status"] == "pending"      # 둘 다 없음

    with pytest.raises(ValueError, match="missing_provenance"):
        svc.decide(doc_id=c["doc_id"], action="change", grade="S2",
                   reason="확정 시도", actor_id="admin-kim")

    # 출처를 채우면 같은 확정이 통과한다 — 막던 것은 문서가 아니라 근거 부재였다.
    svc.record_provenance(
        doc_id=c["doc_id"], source_reference="품질관리/운영절차/2026",
        authorization_basis="소유부서 검수용 사용 승인", actor_id="admin-kim",
    )
    fixed = svc.decide(doc_id=c["doc_id"], action="change", grade="S2",
                       reason="근거 확인 완료", actor_id="admin-kim")
    assert fixed["status"] == "grade_fixed_unlocked" and fixed["final_grade"] == "S2"


def test_synthetic_candidate_is_not_subject_to_the_provenance_gate(tmp_path):
    """합성 후보에는 반출 근거라는 개념이 없다 — 게이트가 걸리면 안 된다."""
    svc = ProxyGoldCandidateService(tmp_path)
    c = _upload(svc, document_origin="uploaded_document")
    assert c["is_actual_document"] is False
    out = svc.decide(doc_id=c["doc_id"], action="change", grade="S3",
                     reason="검토 완료", actor_id="admin-kim")
    assert out["final_grade"] == "S3"


# ── ② M 입력 ───────────────────────────────────────────────────────────────
def test_management_defaults_to_unknown_not_absent(tmp_path):
    """아무것도 안 고르면 'unknown' 이다. 'proven_absent'(M=0)와 뭉치면 안 된다."""
    svc = ProxyGoldCandidateService(tmp_path)
    c = _upload(svc)
    assert c["management"]["state"] == "unknown"
    assert c["management"]["level"] is None


def test_security_marking_sets_management_level(tmp_path):
    svc = ProxyGoldCandidateService(tmp_path)
    c = _upload(svc)
    out = svc.decide(doc_id=c["doc_id"], action="defer", reason="관리성만 기록",
                     actor_id="admin-kim", security_marking="confidential")
    assert out["management"]["state"] == "present"
    assert out["management"]["level"] == 1                  # ICD §3.2 대외비
    assert out["management"]["security_marking"] == "confidential"


def test_all_employees_scope_is_proven_absent_not_unknown(tmp_path):
    """전 임직원 열람 = 관리성 요건 미충족이 **입증**됨(M=0). 하향 경로다."""
    svc = ProxyGoldCandidateService(tmp_path)
    c = _upload(svc)
    out = svc.decide(doc_id=c["doc_id"], action="defer", reason="접근범위 기록",
                     actor_id="admin-kim", access_scope="all_employees")
    assert out["management"]["state"] == "proven_absent"
    assert out["management"]["level"] == 0


def test_partial_update_keeps_the_other_axis(tmp_path):
    """한 칸만 고치러 들어와도 다른 칸이 지워지면 안 된다."""
    svc = ProxyGoldCandidateService(tmp_path)
    c = _upload(svc)
    svc.decide(doc_id=c["doc_id"], action="defer", reason="1차",
               actor_id="admin-kim", access_scope="designated")
    out = svc.decide(doc_id=c["doc_id"], action="reopen", reason="2차",
                     actor_id="admin-kim", security_marking="secret")
    assert out["management"]["access_scope"] == "designated"
    assert out["management"]["security_marking"] == "secret"


def test_unsupported_management_values_are_rejected(tmp_path):
    svc = ProxyGoldCandidateService(tmp_path)
    c = _upload(svc)
    with pytest.raises(ValueError, match="security_marking"):
        svc.decide(doc_id=c["doc_id"], action="defer", reason="x",
                   actor_id="admin-kim", security_marking="극비")
    with pytest.raises(ValueError, match="access_scope"):
        svc.decide(doc_id=c["doc_id"], action="defer", reason="x",
                   actor_id="admin-kim", access_scope="everyone")


# ── ③ 승격 게이트 ──────────────────────────────────────────────────────────
def _intake(doc_id, prov=None):
    rec = {"doc_id": doc_id, "text": f"{doc_id} 본문", "label": "S2",
           "label_source": "rule_llm_agreement", "review_status": "gold_candidate",
           "document_origin": "organization_real"}
    if prov is not None:
        rec["provenance"] = prov
    return rec


def test_intake_without_provenance_is_rejected_at_promotion():
    res = promote_to_locked([_intake("d1")], [Signoff("d1", "admin_kim", "S2", "2026-09-10")])
    assert res.stats["locked"] == 0
    assert res.stats["rejected_reasons"] == {"missing_provenance": 1}


def test_intake_with_recorded_provenance_promotes():
    prov = {"source_reference": "품질관리/운영절차/2026",
            "authorization_basis": "소유부서 검수용 사용 승인", "status": "recorded"}
    res = promote_to_locked([_intake("d1", prov)], [Signoff("d1", "admin_kim", "S2", "2026-09-10")])
    assert res.stats["locked"] == 1
    assert res.locked[0]["label_source"] == "human_review"


def test_records_without_an_intake_marker_are_unaffected():
    """기존 검수 전달본(777·200·120·106·120건)에는 document_origin 이 없다 —
    무조건 막으면 KL 전달본 전량이 승격 거부가 된다(실측 2026-08-23)."""
    legacy = {"doc_id": "d1", "text": "본문", "label": "S2", "source": "판례",
              "label_source": "rule_llm_agreement", "review_status": "gold_candidate"}
    res = promote_to_locked([legacy], [Signoff("d1", "admin_kim", "S2", "2026-09-10")])
    assert res.stats["locked"] == 1


# ── 화면 배선 ──────────────────────────────────────────────────────────────
# 서비스가 값을 받아도 화면에 칸이 없으면 검수자는 아무것도 못 한다
# (test_provenance_record 의 2026-08-17 교훈: 유일한 소비처가 테스트였다).
def test_manage_screen_has_the_management_inputs():
    from koipa.api.golden import _render_specledger_gold_console_html

    html = _render_specledger_gold_console_html()
    for el in ("mgmtWrap", "secMarking", "accScope", "mgmtState"):
        assert f'id="{el}"' in html, f"{el} 입력 요소가 없다"
    assert "renderMgmt" in html, "화면이 M 상태를 그리지 않는다"
    assert "security_marking" in html and "access_scope" in html, "결정 본문에 안 실린다"
    # 「확인 안 됨」이 기본이고, 그것이 무엇을 뜻하는지 화면이 말해야 한다.
    assert "확인 안 됨" in html
    assert "전 임직원 열람 가능" in html


def test_upload_modal_no_longer_forces_provenance():
    from koipa.api.golden import _render_specledger_gold_console_html

    html = _render_specledger_gold_console_html()
    assert "등급을 확정할 수 없습니다" in html, "언제 막히는지 모달이 말해야 한다"
    assert "출처와 권한을 남기지 않으면 나중에 평가셋으로 쓸 수 없습니다" not in html, (
        "게이트를 옮겼는데 옛 문구가 남아 있으면 사용자는 여전히 필수로 읽는다"
    )


# ── 요청 계약 ──────────────────────────────────────────────────────────────
def test_decision_request_rejects_values_outside_the_icd_lists():
    import pydantic

    from koipa.schemas.golden import ProxyGoldCandidateDecisionRequest

    ok = ProxyGoldCandidateDecisionRequest(
        action="defer", reason="보류", security_marking="top_secret",
        access_scope="approved_only",
    )
    assert ok.security_marking == "top_secret"
    # 값을 안 주면 None — 「확인 안 됨」이 기본이고 서버는 아무것도 덮지 않는다.
    bare = ProxyGoldCandidateDecisionRequest(action="defer", reason="보류")
    assert bare.security_marking is None and bare.access_scope is None
    for bad in ({"security_marking": "극비"}, {"access_scope": "everyone"}):
        with pytest.raises(pydantic.ValidationError):
            ProxyGoldCandidateDecisionRequest(action="defer", reason="보류", **bad)
