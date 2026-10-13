"""출처(provenance)가 두 자리에 있는 것을 한 모양으로 읽는다.

왜(실측 2026-08-17, KL 223). 실문서 74건 중 **62건이 "출처 기록 없음"으로 보였다.**
그런데 데이터가 없던 것이 아니라 **읽는 쪽이 안 보는 자리에 있었다.**

    provenance dict        업로드 API 경로가 쓰는 자리          12건
    metadata top-level     적재 스크립트가 쓴 자리              62건
      scripts/load_kl_review_pool_to_console.py:205
        "source_reference": r.get("source") or ""
    읽는 쪽                 meta.get("provenance") 만 본다
      proxy_gold_candidate_service.py:442

62건 전부 실제 값이 있었다("판례(2000+)" 등). 그래서 "출처를 새로 수집해야 한다" 가 아니라
**자리를 합쳐 읽으면 되는 문제**였다.

⚠ 합치되 status 를 함부로 'recorded' 로 올리지 않는다. 출처만 있고 사용 권한 근거
  (authorization_basis)가 없으면 미완이다 — 'partial' 로 구분한다. 이 구분이 없으면
  "기록 74건" 이 되어 실제로는 권한 근거가 없는 62건이 완결된 것처럼 보인다.

⚠ 옛 자리에서 끌어올린 것은 origin='legacy_top_level' 로 표시한다. 적재 스크립트를
  고친 뒤 이 수가 0 이 되면 이관이 끝난 것이다.
"""
from __future__ import annotations

from koipa.services.proxy_gold_candidate_service import _merged_provenance


def test_provenance_dict_wins_when_recorded():
    meta = {
        "provenance": {"status": "recorded", "source_reference": "품질관리/2026",
                       "authorization_basis": "부서 승인"},
        "source_reference": "무시돼야 함",
    }
    got = _merged_provenance(meta)
    assert got["status"] == "recorded"
    assert got["source_reference"] == "품질관리/2026"
    assert "origin" not in got, "정본 dict 를 쓸 때는 legacy 표시가 붙으면 안 된다"


def test_legacy_top_level_is_lifted_but_marked_partial():
    """출처만 있고 권한 근거가 없으면 partial — recorded 로 올리면 안 된다."""
    meta = {"source_reference": "판례(2000+)"}
    got = _merged_provenance(meta)
    assert got["source_reference"] == "판례(2000+)"
    assert got["status"] == "partial", "권한 근거 없이 recorded 가 되면 안 된다"
    assert got["origin"] == "legacy_top_level"


def test_legacy_with_both_fields_becomes_recorded():
    meta = {"source_reference": "공개기관 URL", "authorization_basis": "공개 라이선스"}
    got = _merged_provenance(meta)
    assert got["status"] == "recorded"
    assert got["origin"] == "legacy_top_level"


def test_nothing_anywhere_stays_empty():
    assert _merged_provenance({}) == {}
    assert _merged_provenance({"source_reference": "   "}) == {}


def test_partial_is_counted_separately_in_summary():
    """집계가 partial·legacy 를 따로 세는지 — 안 세면 62건이 '없음'으로 보인다."""
    import inspect

    from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService

    src = inspect.getsource(ProxyGoldCandidateService)
    assert "actual_provenance_partial" in src
    assert "actual_provenance_legacy" in src
    assert "actual_provenance_recorded" in src


def test_recorded_count_did_not_silently_inflate():
    """자리를 합치면서 recorded 가 부풀면 안 된다 — 권한 근거가 판정 기준이다."""
    only_source = [{"source_reference": f"출처{i}"} for i in range(10)]
    got = [_merged_provenance(m) for m in only_source]
    assert all(g["status"] == "partial" for g in got)
    assert not any(g["status"] == "recorded" for g in got)
