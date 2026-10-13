"""검수 요청 대상이 아닌 후보를 보관 폴더로 옮기는 도구 — 옮기고, 남기고, 되돌릴 수 있는가.

배경(2026-09-25): 콘솔 후보 원장 3,598건 중 검수 요청 대상은 1,711건이다. 나머지 1,887건과 딸린 파일(개정본·업로드 원본·
보기 파일)을 옮기되, 요청 대상의 파일과 후보가 아닌 파일(결정 원장·별칭 솔트)은 한 개도 건드리면 안 된다.
실데이터에 손대기 전에 이 시험이 그 경계를 잠근다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_non_requested_candidates as anc  # noqa: E402
import audit_golden_candidate_pool as agp  # noqa: E402
from koipa.services import proxy_gold_candidate_service as pgs  # noqa: E402
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService  # noqa: E402

BATCH = anc.DELIVERED_BATCH


@pytest.fixture(autouse=True)
def _fresh_cache():
    pgs._CANDIDATE_CACHE.clear()
    yield
    pgs._CANDIDATE_CACHE.clear()


def _write(p: Path, text: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _cand(root: Path, doc_id: str, body_name: str, *, origin="synthetic", batch=None, status="proposed", text=None):
    _write(root / body_name, text or f"본문 {doc_id} " * 20)
    meta = {"doc_id": doc_id, "intended_label": "S1", "document_origin": origin, "candidate_status": status,
            "requires_manual_audit": True, "content_revision_path": body_name}
    if batch:
        meta["review_batch"] = batch
    _write(root / f"{doc_id}.metadata.json", json.dumps(meta, ensure_ascii=False))


@pytest.fixture()
def pool(tmp_path, monkeypatch):
    root = tmp_path / "single_document_candidates"
    root.mkdir()
    # 요청 대상 2건 — 접두 함정: MD-0001 은 MD-00010 의 접두이다
    _cand(root, "MD-0001", "MD-0001_review.md", batch=BATCH)
    _cand(root, "MD-0002", "MD-0002_review.md", batch=BATCH)
    # 품질 결함으로 제외된 검수 배치 문서
    _cand(root, "MD-0003", "MD-0003_review.md", batch=BATCH)
    # 다른 후보들
    _cand(root, "MD-00010", "MD-00010_review.md")                                  # 이름만 MD-0001 로 시작하는 옛 후보
    _cand(root, "FD-0001", "FD-0001.cleaned.md", batch="factfirst_20260921_nonsealed")
    _cand(root, "GOLD-B1-S1-001", "GOLD-B1-S1-001.md")
    _write(root / "revisions" / "GOLD-B1-S1-001.v4.md")
    _write(root / "revisions" / "GOLD-B1-S1-001.v5.md")
    _cand(root, "GOLD-CAND-S1-X-001", "GOLD-CAND-S1-X-001_제목.md")
    _write(root / "GOLD-CAND-S1-X-001_view.v1.html")
    _cand(root, "GOLD-UPL-AAA", "GOLD-UPL-AAA.cleaned.md", origin="public_real", status="under_review")
    _write(root / "uploaded_originals" / "GOLD-UPL-AAA_원본.hwpx")
    # 후보가 아닌 파일 — 어느 경우에도 그대로여야 한다
    _write(root / "candidate_decisions.jsonl", json.dumps({"doc_id": "GOLD-B1-S1-001", "action": "reopen"}) + "\n")
    _write(root / "reviewer_alias.salt", "salt")
    _write(root / "candidate_catalog.v1.html")

    svc = ProxyGoldCandidateService(root)
    monkeypatch.setattr(anc, "ROOT", svc.root)
    monkeypatch.setattr(anc, "load_rows", lambda: agp.load_rows(svc.root))
    monkeypatch.setattr(anc, "load_exclusions", lambda: {"MD-0003"})
    return svc.root


def _all_files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): anc.sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def test_plan_keeps_the_requested_documents_and_groups_the_rest(pool):
    pl = anc.plan(anc.load_rows(), pool)

    assert pl["problems"] == []
    assert pl["keep"] == {"MD-0001", "MD-0002"}
    groups = {mv["doc_id"]: mv["group"] for mv in pl["moves"]}
    assert groups == {"MD-0003": "품질 결함(검수 요청 제외)", "MD-00010": "옛 후보 · 합성", "FD-0001": "FD 사본",
                      "GOLD-B1-S1-001": "옛 후보 · 합성", "GOLD-CAND-S1-X-001": "옛 후보 · 합성",
                      "GOLD-UPL-AAA": "옛 후보 · 공개 실문서"}
    assert pl["protected"] == ["candidate_catalog.v1.html", "candidate_decisions.jsonl", "reviewer_alias.salt"]


def test_files_belong_by_exact_id_not_by_prefix(pool):
    """MD-0001(남김)의 파일이 MD-00010(옮김)에 딸려 가면 안 되고, 그 반대도 안 된다."""
    pl = anc.plan(anc.load_rows(), pool)
    moved = {rel for mv in pl["moves"] for rel in mv["files"]}

    assert "MD-0001.metadata.json" not in moved and "MD-0001_review.md" not in moved
    assert {"MD-00010.metadata.json", "MD-00010_review.md"} <= moved


def test_companion_files_travel_with_their_candidate(pool):
    pl = anc.plan(anc.load_rows(), pool)
    files = {mv["doc_id"]: set(mv["files"]) for mv in pl["moves"]}

    assert files["GOLD-B1-S1-001"] == {"GOLD-B1-S1-001.metadata.json", "GOLD-B1-S1-001.md",
                                       "revisions/GOLD-B1-S1-001.v4.md", "revisions/GOLD-B1-S1-001.v5.md"}
    assert "GOLD-CAND-S1-X-001_view.v1.html" in files["GOLD-CAND-S1-X-001"]
    assert "uploaded_originals/GOLD-UPL-AAA_원본.hwpx" in files["GOLD-UPL-AAA"]


def test_apply_leaves_only_requested_candidates_and_restore_puts_everything_back(pool, tmp_path):
    before = _all_files(pool)
    pl = anc.plan(anc.load_rows(), pool)
    archive = tmp_path / "archive"

    anc.apply(pl, archive, pool)

    after = _all_files(pool)
    # 남은 것 = 요청 대상 2건의 파일 + 후보가 아닌 파일 — 한 글자도 안 바뀌었다
    assert sorted(after) == ["MD-0001.metadata.json", "MD-0001_review.md", "MD-0002.metadata.json", "MD-0002_review.md",
                             "candidate_catalog.v1.html", "candidate_decisions.jsonl", "reviewer_alias.salt"]
    assert all(after[k] == before[k] for k in after)
    # 화면이 세는 곳에서도 요청 대상 2건뿐이다(캐시는 파일 변경을 스스로 알아챈다)
    pgs._CANDIDATE_CACHE.clear()
    assert {r["doc_id"] for r in anc.load_rows()} == {"MD-0001", "MD-0002"}
    # 보관 폴더에는 옮긴 파일이 sha256 그대로 있고 MANIFEST 가 묶음·복원법을 적는다
    man = json.loads((archive / "MANIFEST.json").read_text(encoding="utf-8"))
    assert (man["keep_count"], man["move_count"]) == (2, 6)
    assert man["by_group"]["FD 사본"] == 1 and "restore" in man
    assert {k: v for k, v in _all_files(archive).items() if k != "MANIFEST.json"} == {
        k: v for k, v in before.items() if k not in after}
    # 원장은 그대로다
    assert (pool / "candidate_decisions.jsonl").exists()

    assert anc.restore(archive, pool) == 0
    assert _all_files(pool) == before


def test_a_candidate_a_human_touched_stops_everything(pool):
    meta = pool / "GOLD-B1-S1-001.metadata.json"
    d = json.loads(meta.read_text(encoding="utf-8"))
    d["candidate_status"] = "approved_proxy"
    meta.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    pgs._CANDIDATE_CACHE.clear()

    pl = anc.plan(anc.load_rows(), pool)

    assert any("GOLD-B1-S1-001" in p and "사람이 손댔다" in p for p in pl["problems"])


def test_a_file_that_fits_two_candidates_stops_everything(pool):
    _cand(pool, "GOLD-X", "GOLD-X.md")
    _cand(pool, "GOLD-X_1", "GOLD-X_1.md")           # GOLD-X_1.md 는 GOLD-X 와 GOLD-X_1 둘 다로 읽힌다

    pl = anc.plan(anc.load_rows(), pool)

    assert any("두 후보에 걸린다" in p and "GOLD-X_1.md" in p for p in pl["problems"])


def test_main_refuses_and_moves_nothing_when_the_expected_counts_differ(pool, monkeypatch, tmp_path):
    before = _all_files(pool)
    archive = tmp_path / "archive"
    monkeypatch.setattr(sys, "argv", ["x", "--apply", "--expect-keep", "1711", "--archive-dir", str(archive)])

    assert anc.main() == 1
    assert _all_files(pool) == before and not archive.exists()


def test_apply_refuses_to_overwrite_an_existing_archive(pool, tmp_path):
    pl = anc.plan(anc.load_rows(), pool)
    archive = tmp_path / "archive"
    archive.mkdir()
    with pytest.raises(FileExistsError):
        anc.apply(pl, archive, pool)
    assert (pool / "GOLD-B1-S1-001.md").exists()


def test_restore_refuses_a_tampered_archive(pool, tmp_path):
    pl = anc.plan(anc.load_rows(), pool)
    archive = tmp_path / "archive"
    anc.apply(pl, archive, pool)
    (archive / "FD-0001.cleaned.md").write_text("바뀐 내용", encoding="utf-8")
    with pytest.raises(SystemExit, match="sha256 불일치"):
        anc.restore(archive, pool)
