"""콘솔 검수 결정이 평가정답(locked_gold_eval)까지 도달한다 — 끊겨 있던 배선.

무엇이 끊겨 있었나(2026-09-09 실측). 서명 경로가 두 벌인데 이어져 있지 않았다.

    decide()  → candidate_decisions.jsonl 에 이벤트 append. label_source 를 쓰지 않는다.
              원장을 읽는 코드는 콘솔 자기 화면 표시뿐 — tier 로 넘기는 코드 0건.
    tier_of() → label_source 를 보고 tier 를 판정. 원장은 보지 않는다.

그래서 **콘솔에서 등급을 확정해도 평가정답이 한 건도 생기지 않았다.** 대상도 겹치지
않았다 — 콘솔 후보 1,067건 중 tier 코퍼스에 있는 것은 67건뿐이었다.

두 번째 구멍(②): 조직 실문서의 출처 어휘가 두 벌이었다. 콘솔은 organization_real,
golden_tiers 는 customer_real 을 쓰는데 매핑이 없어 explicit 값이 unknown 으로 떨어졌다.
사람이 실문서에 서명해도 is_real_locked_eval 이 False 였다 — 감리 회신에 적는 '실문서
평가정답 몇 건'이 구조적으로 0이 되는 자리다.
"""

from __future__ import annotations

import json

import pytest

from koipa.golden_tiers import TIER_LOCKED, is_real_locked_eval, tier_of
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService


def _candidate(root, doc_id: str, grade: str, *, origin: str = "synthetic") -> None:
    (root / f"{doc_id}_검토문서.md").write_text(
        "# 검토 문서\n" + "가" * 200, encoding="utf-8"
    )
    (root / f"{doc_id}.metadata.json").write_text(json.dumps({
        "doc_id": doc_id, "intended_label": grade, "document_origin": origin,
        "document_type": "검토 문서", "candidate_status": "proposed",
        "requires_manual_audit": True, "claim_scope": "synthetic proxy only",
    }, ensure_ascii=False), encoding="utf-8")


def _locked(svc) -> list[dict]:
    return [
        json.loads(ln)
        for ln in svc.locked_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


@pytest.fixture
def svc(tmp_path):
    for i, g in enumerate(("TS", "S1", "S2", "S3"), start=1):
        _candidate(tmp_path, f"CAND-00{i}", g)
    return ProxyGoldCandidateService(tmp_path)


def test_확정한_결정이_평가정답이_된다(svc):
    """이 시험이 배선 그 자체다 — 원복하면 locked 0 건으로 실패한다."""
    svc.decide(doc_id="CAND-001", action="approve", actor_id="지재원관리자")
    svc.decide(doc_id="CAND-002", action="change", grade="S2",
               reason="본문 근거가 S2에 가깝다", actor_id="지재원관리자")

    out = svc.promote_decisions_to_locked()

    assert out["locked"] == 2
    assert out["locked_by_grade"] == {"TS": 1, "S2": 1}
    rows = _locked(svc)
    assert {r["doc_id"] for r in rows} == {"CAND-001", "CAND-002"}
    # tier 판정까지 도달해야 의미가 있다. label_source 만 붙고 envelope 가 비면 held 다.
    assert [tier_of(r) for r in rows] == [TIER_LOCKED, TIER_LOCKED]
    # 등급 변경 결정은 **바꾼 등급**이 정답이다(제안 등급 S1 이 아니다).
    assert {r["doc_id"]: r["label"] for r in rows}["CAND-002"] == "S2"


def test_서명자는_승격_실행자가_아니라_결정자다(svc):
    """승격은 결정을 옮기는 행위다. 옮긴 사람이 서명자가 되면 검수 책임이 뒤바뀐다."""
    svc.decide(doc_id="CAND-001", action="approve", actor_id="검수자A")
    svc.promote_decisions_to_locked()

    row = _locked(svc)[0]
    assert row["reviewer_id"] == "검수자A"
    assert row["reviewer_ids"] == ["검수자A"]
    # 서명 시각도 결정 시각이다 — 승격을 언제 돌렸는지가 아니라 언제 판단했는지가 근거다.
    decided_at = svc.get_candidate("CAND-001")["latest_decision"]["decided_at"]
    assert row["signed_at"] == decided_at
    assert row["note"] == ""  # approve 는 사유가 필수가 아니다


def test_본문이_실린다(svc):
    """locked jsonl 이 평가면 그 자체다. 본문 없는 평가정답은 평가에 못 쓴다."""
    svc.decide(doc_id="CAND-001", action="approve", actor_id="지재원관리자")
    svc.promote_decisions_to_locked()

    row = _locked(svc)[0]
    assert row["text"].startswith("# 검토 문서")
    # 원장 부산물은 싣지 않는다 — 나중에 읽는 쪽이 그것을 정답의 근거로 읽는다.
    assert "latest_decision" not in row
    assert "grade_fixed" not in row
    # 대신 어느 결정에서 왔는지는 event_id 로 되짚을 수 있어야 한다.
    assert row["console_decision_event_id"]


def test_등급을_확정하지_않은_결정은_승격되지_않는다(svc):
    """보류·폐기·범위밖은 등급을 확정하지 않는다. 승격하면 판단하지 않은 것이 정답이 된다."""
    svc.decide(doc_id="CAND-001", action="defer", reason="추가 확인 필요",
               actor_id="지재원관리자")
    svc.decide(doc_id="CAND-002", action="discard", reason="중복 문서",
               actor_id="지재원관리자")
    svc.decide(doc_id="CAND-003", action="exclude", reason="검수 범위 밖",
               actor_id="지재원관리자")

    out = svc.promote_decisions_to_locked()

    assert out["promotable"] == 0
    assert out["locked"] == 0
    # 미결정 후보가 '거부'로 잡히면 남은 건수가 늘 과대값이 된다.
    assert out["rejected"] == 0


def test_기계_결정자는_서명이_되지_않는다(svc):
    """자칭 서명 경로를 하나 더 여는 것이 아니다 — is_human_reviewer 가 그대로 막아야 한다."""
    svc.decide(doc_id="CAND-001", action="approve", actor_id="ai_assist")

    out = svc.promote_decisions_to_locked()

    assert out["locked"] == 0
    assert out["rejected"] == 1
    assert out["rejected_reasons"] == {"machine_reviewer": 1}


def test_여러_번_돌려도_결과가_같다(svc):
    """원장 투영이라 재실행이 정상 운용이다. 누적이 부풀면 readiness 수치가 거짓이 된다."""
    svc.decide(doc_id="CAND-001", action="approve", actor_id="지재원관리자")
    first = svc.promote_decisions_to_locked()
    second = svc.promote_decisions_to_locked()

    assert first["locked_total"] == second["locked_total"] == 1
    assert len(_locked(svc)) == 1


def test_나중에_바꾼_등급이_이긴다(svc):
    """원장은 append-only 다. 최신 결정이 정답이어야 한다."""
    svc.decide(doc_id="CAND-001", action="approve", actor_id="지재원관리자")
    svc.promote_decisions_to_locked()
    svc.decide(doc_id="CAND-001", action="change", grade="S3",
               reason="재검토 결과 공개 수준", actor_id="지재원관리자")
    svc.promote_decisions_to_locked()

    rows = _locked(svc)
    assert len(rows) == 1
    assert rows[0]["label"] == "S3"


def test_승격_뒤에_물린_판단은_평가정답에서_빠진다(svc):
    """원장은 append-only 라 승격 뒤에도 폐기가 올라온다. 누적만 하면 되돌린 것이 정답으로 남는다."""
    svc.decide(doc_id="CAND-001", action="approve", actor_id="지재원관리자")
    svc.decide(doc_id="CAND-002", action="approve", actor_id="지재원관리자")
    assert svc.promote_decisions_to_locked()["locked_total"] == 2

    svc.decide(doc_id="CAND-001", action="discard", reason="근거 문서가 잘못됐다",
               actor_id="지재원관리자")
    out = svc.promote_decisions_to_locked()

    assert out["locked_total"] == 1
    assert {r["doc_id"] for r in _locked(svc)} == {"CAND-002"}


def test_보류로_되돌려도_평가정답에서_빠진다(svc):
    """폐기만이 아니다 — 보류·재검토도 '아직 정답이 아니다' 라는 판단이다."""
    svc.decide(doc_id="CAND-001", action="approve", actor_id="지재원관리자")
    svc.promote_decisions_to_locked()
    svc.decide(doc_id="CAND-001", action="defer", reason="소유부서 확인 필요",
               actor_id="지재원관리자")

    assert svc.promote_decisions_to_locked()["locked_total"] == 0


def test_조직_실문서가_실문서_평가정답으로_잡힌다(tmp_path):
    """②: 출처 어휘 매핑. golden_tiers._ORIGIN_ALIASES 를 빼면 real_locked 0 으로 실패한다.

    이 값이 감리 회신의 '실문서 평가정답 몇 건'이다. 어휘가 어긋난 채로는 사람이 아무리
    서명해도 0 이라, 협조요청의 근거 숫자 자체가 만들어지지 않는다.
    """
    _candidate(tmp_path, "REAL-001", "S2", origin="organization_real")
    _candidate(tmp_path, "SYN-001", "TS", origin="synthetic")
    svc = ProxyGoldCandidateService(tmp_path)
    svc.decide(doc_id="REAL-001", action="change", grade="S2",
               reason="소유부서 확인", actor_id="지재원관리자")
    svc.decide(doc_id="SYN-001", action="approve", actor_id="지재원관리자")

    out = svc.promote_decisions_to_locked()

    assert out["locked"] == 2
    # 합성 서명도 locked tier 에는 들어간다(is_locked_eval 2026-08-06 결정).
    assert out["locked_total"] == 2
    # 그러나 **실문서 평가정답**은 실문서 한 건뿐이다.
    assert out["real_locked"] == 1
    real = [r for r in _locked(svc) if r["doc_id"] == "REAL-001"][0]
    assert is_real_locked_eval(real)
    # 출처 어휘는 콘솔 값 그대로 실린다 — golden_signoff._provenance_ok 가 그 어휘를 본다.
    assert real["document_origin"] == "organization_real"


def test_승격은_후보_원장을_바꾸지_않는다(svc):
    """decide() 의 쓰기 경로를 건드리지 않는다는 계약. 투영이지 이동이 아니다."""
    svc.decide(doc_id="CAND-001", action="approve", actor_id="지재원관리자")
    before = svc.ledger_path.read_text(encoding="utf-8")

    svc.promote_decisions_to_locked()

    assert svc.ledger_path.read_text(encoding="utf-8") == before
    assert svc.get_candidate("CAND-001")["status"] == "approved_proxy"


def test_공유_API_키로는_평가정답을_만들_수_없다():
    """승격 엔드포인트도 포털 로그인을 요구한다 — 공유 키로 되면 '누가 승격했나'가 안 남는다.

    엔드포인트 함수를 직접 부른다. 게이트가 빠지면 여기서 서비스까지 내려가 예외가 안 난다.
    """
    from fastapi import HTTPException

    from koipa.api.golden import proxy_gold_candidate_promote
    from koipa.schemas.golden import ProxyGoldPromoteRequest

    with pytest.raises(HTTPException, match="portal JWT"):
        proxy_gold_candidate_promote(
            ProxyGoldPromoteRequest(dry_run=True),
            auth={"mode": "api_key", "actor_role": "admin"},
        )


def test_dry_run_은_아무것도_쓰지_않는다(svc):
    svc.decide(doc_id="CAND-001", action="approve", actor_id="지재원관리자")

    out = svc.promote_decisions_to_locked(dry_run=True)

    assert out["locked"] == 1 and out["dry_run"] is True
    assert not svc.locked_path.exists()
