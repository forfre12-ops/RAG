"""골든 후보 목록 캐시 — 변경분만 다시 읽고, 결정은 덮어쓰기만 다시 하는가.

배경(2026-09-25 실측, Docker Desktop 바인드 마운트, 후보 3,598건·파일 7천 개):
  · 요청마다 디렉터리 전수 stat 3.6초
  · 결정 한 번(원장이 바뀜)마다 캐시가 통째로 무효화돼 다음 요청이 7천 파일을 다시 읽음 30초
그래서 "파일에서 온 부분"과 "원장의 결정 덮어쓰기"를 갈랐다. 이 시험은 그 두 가지가 다시
합쳐지지 않게 잠근다 — 값이 맞는지(정확성)와 다시 읽지 않는지(비용)를 함께 본다.
"""
from __future__ import annotations

import json

import pytest

from koipa.services import proxy_gold_candidate_service as pgs
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService


@pytest.fixture(autouse=True)
def _fresh_cache():
    pgs._CANDIDATE_CACHE.clear()
    yield
    pgs._CANDIDATE_CACHE.clear()


def _candidate(root, doc_id, grade="S1", batch=None):
    (root / f"{doc_id}_검토문서.md").write_text("# 검토 문서\n" + "가" * 100, encoding="utf-8")
    meta = {
        "doc_id": doc_id, "intended_label": grade, "document_origin": "synthetic",
        "document_type": "검토 문서", "candidate_status": "proposed",
        "requires_manual_audit": True, "claim_scope": "synthetic proxy only",
    }
    if batch:
        meta["review_batch"] = batch
    (root / f"{doc_id}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _count_row_builds(monkeypatch):
    """행을 파일에서 새로 만든 횟수(= 파일을 읽은 후보 수)를 센다."""
    built: list[str] = []
    real = ProxyGoldCandidateService._build_row

    def spy(self, meta_name, docs_by_id):
        built.append(meta_name)
        return real(self, meta_name, docs_by_id)

    monkeypatch.setattr(ProxyGoldCandidateService, "_build_row", spy)
    return built


def test_pagination_slices_the_list_but_not_the_numbers(tmp_path):
    for i in range(1, 8):
        _candidate(tmp_path, f"C-{i:03d}", batch="B1" if i <= 5 else "B2")
    svc = ProxyGoldCandidateService(tmp_path)

    whole = svc.list_candidates()
    page = svc.list_candidates(limit=2, offset=1)

    assert [c["doc_id"] for c in page["candidates"]] == ["C-002", "C-003"]
    assert page["total"] == whole["total"] == 7          # 쪽을 넘겨도 전체 건수는 그대로
    assert (page["offset"], page["limit"], page["returned"]) == (1, 2, 2)
    assert page["summary"] == whole["summary"]           # KPI 도 잘라내기 전 기준
    assert page["available_batches"] == whole["available_batches"]
    assert whole["limit"] is None and whole["returned"] == 7   # 생략하면 종전처럼 전부
    # 배치로 좁힌 뒤에도 total 은 좁힌 집합 기준이고, 쪽은 그 위에서 자른다
    b1 = svc.list_candidates(review_batch="B1", limit=2, offset=4)
    assert b1["total"] == 5 and [c["doc_id"] for c in b1["candidates"]] == ["C-005"]
    # 끝을 넘은 쪽은 빈 목록이다(오류 아님)
    assert svc.list_candidates(limit=5, offset=50)["candidates"] == []


def test_a_decision_does_not_reread_any_candidate_file(tmp_path, monkeypatch):
    for i in range(1, 6):
        _candidate(tmp_path, f"C-{i:03d}")
    svc = ProxyGoldCandidateService(tmp_path)
    svc.list_candidates()                                # 캐시를 데운다
    built = _count_row_builds(monkeypatch)

    svc.decide(doc_id="C-002", action="approve", actor_id="admin")
    after = svc.list_candidates()

    # 결정은 원장에만 적는다 — 후보 파일 5개를 다시 읽으면 3천 건에서 30초가 된다
    assert built == []
    row = next(c for c in after["candidates"] if c["doc_id"] == "C-002")
    assert row["status"] == "approved_proxy" and row["final_grade"] == "S1" and row["grade_fixed"] is True
    others = [c for c in after["candidates"] if c["doc_id"] != "C-002"]
    assert all(c["status"] == "proposed" and c["final_grade"] is None for c in others)
    assert after["summary"]["fixed"] == 1


def test_a_second_decision_by_another_process_is_seen_immediately(tmp_path):
    """다른 워커가 원장에 적은 결정은 TTL 을 기다리지 않고 보인다 — 원장은 요청마다 stat 한다."""
    _candidate(tmp_path, "C-001")
    a, b = ProxyGoldCandidateService(tmp_path), ProxyGoldCandidateService(tmp_path)
    assert a.get_candidate("C-001")["status"] == "proposed"
    b.decide(doc_id="C-001", action="defer", reason="나중에", actor_id="rv")
    assert a.get_candidate("C-001")["status"] == "deferred"


def test_added_candidate_appears_and_only_that_row_is_built(tmp_path, monkeypatch):
    for i in range(1, 5):
        _candidate(tmp_path, f"C-{i:03d}")
    svc = ProxyGoldCandidateService(tmp_path)
    assert svc.list_candidates()["total"] == 4
    built = _count_row_builds(monkeypatch)

    _candidate(tmp_path, "C-099")
    svc._expire_scan()                                   # 서비스가 직접 쓴 뒤의 신호(업로드 경로가 부른다)
    out = svc.list_candidates()

    assert out["total"] == 5 and "C-099" in [c["doc_id"] for c in out["candidates"]]
    assert built == ["C-099.metadata.json"]              # 나머지 4건은 다시 읽지 않았다


def test_in_place_edit_is_picked_up_after_expiry_and_only_that_row_is_rebuilt(tmp_path, monkeypatch):
    for i in range(1, 5):
        _candidate(tmp_path, f"C-{i:03d}")
    svc = ProxyGoldCandidateService(tmp_path)
    svc.list_candidates()
    built = _count_row_builds(monkeypatch)

    meta_path = tmp_path / "C-003.metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["intended_label"] = "TS"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")   # 크기가 달라진다
    svc._expire_scan()

    row = next(c for c in svc.list_candidates()["candidates"] if c["doc_id"] == "C-003")
    assert row["proposed_grade"] == "TS"
    assert built == ["C-003.metadata.json"]


def test_removed_candidate_disappears(tmp_path):
    for i in range(1, 4):
        _candidate(tmp_path, f"C-{i:03d}")
    svc = ProxyGoldCandidateService(tmp_path)
    assert svc.list_candidates()["total"] == 3

    (tmp_path / "C-002.metadata.json").unlink()
    svc._expire_scan()
    out = svc.list_candidates()
    assert out["total"] == 2 and [c["doc_id"] for c in out["candidates"]] == ["C-001", "C-003"]


def test_directory_rescan_is_not_repeated_within_the_ttl(tmp_path, monkeypatch):
    """요청마다 디렉터리를 훑지 않는다 — 훑기는 TTL 안에서 한 번뿐이다."""
    for i in range(1, 4):
        _candidate(tmp_path, f"C-{i:03d}")
    svc = ProxyGoldCandidateService(tmp_path)
    scans: list[int] = []
    real = ProxyGoldCandidateService._scan

    def spy(self):
        scans.append(1)
        return real(self)

    monkeypatch.setattr(ProxyGoldCandidateService, "_scan", spy)
    for _ in range(5):
        svc.list_candidates()
        svc.get_candidate("C-001")
    assert len(scans) == 1


def test_clear_forces_a_real_reread_even_inside_the_ttl(tmp_path):
    """시험·운영 도구가 `_CANDIDATE_CACHE.clear()` 로 '재시작'을 흉내 내면 파생 캐시도 비워져야 한다."""
    _candidate(tmp_path, "C-001", grade="S1")
    svc = ProxyGoldCandidateService(tmp_path)
    assert svc.get_candidate("C-001")["proposed_grade"] == "S1"

    meta_path = tmp_path / "C-001.metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["intended_label"] = "S3"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    pgs._CANDIDATE_CACHE.clear()

    assert svc.get_candidate("C-001")["proposed_grade"] == "S3"


def test_quality_is_computed_once_for_the_same_cached_list(tmp_path, monkeypatch):
    for i in range(1, 4):
        _candidate(tmp_path, f"C-{i:03d}")
    svc = ProxyGoldCandidateService(tmp_path)
    calls: list[int] = []
    real = ProxyGoldCandidateService._quality

    def spy(candidates):
        calls.append(len(candidates))
        return real(candidates)

    monkeypatch.setattr(ProxyGoldCandidateService, "_quality", staticmethod(spy))
    first = svc.list_candidates()["summary"]["quality"]
    second = svc.list_candidates(limit=1)["summary"]["quality"]
    assert first == second and len(calls) == 1


def test_a_decision_does_not_rescan_bodies_for_grade_exposure(tmp_path, monkeypatch):
    """결정 직후 첫 목록 조회가 0.55초 걸렸다 — 본문은 안 바뀌는데 3천 건을 정규식으로 다시 훑었다."""
    for i in range(1, 5):
        _candidate(tmp_path, f"C-{i:03d}")
    svc = ProxyGoldCandidateService(tmp_path)
    before = svc.list_candidates()["summary"]["quality"]
    calls: list[int] = []
    real = pgs._exposes_grade

    def spy(text, *, is_real):
        calls.append(1)
        return real(text, is_real=is_real)

    monkeypatch.setattr(pgs, "_exposes_grade", spy)
    svc.decide(doc_id="C-002", action="approve", actor_id="admin")
    after = svc.list_candidates()["summary"]["quality"]
    assert calls == []                                    # 본문 검사를 다시 하지 않았다
    assert after["grade_token_exposed"] == before["grade_token_exposed"]


def test_summary_and_quality_follow_the_selected_batch(tmp_path):
    """이번 회차를 골랐는데 카드에 "전체 후보 3,598건"이 떠 사용자가 "왜 3,598건이냐"고 되물었다.
    배치는 필터가 아니라 검수 범위라서 KPI·품질도 그 배치 기준이어야 한다(원장 전량은 ledger_total)."""
    for i in range(1, 8):
        _candidate(tmp_path, f"C-{i:03d}", batch="B1" if i <= 5 else "B2")
    svc = ProxyGoldCandidateService(tmp_path)

    whole = svc.list_candidates()["summary"]
    b1 = svc.list_candidates(review_batch="B1")["summary"]
    assert (whole["total"], whole["scope"]) == (7, "all")
    assert (b1["total"], b1["scope"], b1["ledger_total"], b1["unfixed"]) == (5, "batch", 7, 5)
    assert b1["quality"]["documents"] == 5                     # 품질도 그 배치의 문서만 센다
    # 상태·등급 필터는 KPI 를 흔들지 않는다 — 범위(배치)와 필터(상태)는 다르다
    assert svc.list_candidates(review_batch="B1", status="deferred")["summary"]["total"] == 5
    # 결정은 그 배치의 확정 수로 잡힌다
    svc.decide(doc_id="C-002", action="approve", actor_id="admin")
    assert svc.list_candidates(review_batch="B1")["summary"]["fixed"] == 1
    assert svc.list_candidates(review_batch="B2")["summary"]["fixed"] == 0


def test_title_prefers_metadata_title_over_document_type(tmp_path):
    """document_type 은 생성 회차 표식("사실우선 모의문서(R7)")이지 문서 제목이 아니다 — 실측
    2026-09-27: 사실우선 배치 1,711건이 이 필드로 title 을 채워서 목록에 제목이 9종뿐이었다
    (본문마다 있는 실제 첫 줄 제목이 안 쓰였다). title 메타 필드가 있으면 그걸 우선한다.
    """
    _candidate(tmp_path, "C-001")  # document_type 만 있고 title 없음 — 옛 배치 그대로
    meta_path = tmp_path / "C-001.metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "title" not in meta
    svc = ProxyGoldCandidateService(tmp_path)
    row = svc.list_candidates()["candidates"][0]
    assert row["title"] == "검토 문서"  # 폴백: document_type 그대로(회귀 방지)

    _candidate(tmp_path, "C-002")
    meta_path2 = tmp_path / "C-002.metadata.json"
    meta2 = json.loads(meta_path2.read_text(encoding="utf-8"))
    meta2["title"] = "법무실 새 식구를 위한 안내 — 분쟁 비용 자료 편"
    meta_path2.write_text(json.dumps(meta2, ensure_ascii=False), encoding="utf-8")
    pgs._CANDIDATE_CACHE.clear()
    svc2 = ProxyGoldCandidateService(tmp_path)
    row2 = next(c for c in svc2.list_candidates()["candidates"] if c["doc_id"] == "C-002")
    assert row2["title"] == "법무실 새 식구를 위한 안내 — 분쟁 비용 자료 편"
