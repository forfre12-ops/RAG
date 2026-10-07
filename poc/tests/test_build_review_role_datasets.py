"""검수 결과를 역할별(골든셋·학습셋·평가셋)로 나눠 재학습 데이터셋으로 만드는 도구.

배경(2026-09-25): 검수 뒤 재학습이 목적인데 결정 원장을 학습 데이터로 바꾸는 코드가 없었다. 이 시험이 지키는 것:
  · 역할은 문서마다 고정이다(학습 문서는 학습셋, 봉인 문서는 골든셋) — 검수가 역할을 못 바꾼다
  · 전문가가 확정한 문서만 들어가고, 나머지는 사유와 함께 검토_필요.jsonl 로 간다
  · 골든셋은 --include-golden 없이는 파일로 안 만들어진다(한 번만 연다)
  · 역할이 다른 두 문서의 본문이 같으면 멈춘다
  · 결정 원장은 읽기만 한다(바이트 그대로)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_review_role_datasets as brd  # noqa: E402
from koipa.services import proxy_gold_candidate_service as pgs  # noqa: E402
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService  # noqa: E402

BATCH = brd.DELIVERED_BATCH
# doc_id, training_use, AI 잠정 등급
DOCS = [
    ("MD-0001", "fs_train_loss", "S1"),          # 전문가가 S1 로 확인(AI 와 같음)
    ("MD-0002", "fs_train_loss", "S2"),          # 전문가가 S3 로 바꿈
    ("MD-0003", "fs_train_loss", "S3"),          # 아직 검수 전
    ("MD-0004", "fs_val_holdout", "TS"),         # 검증
    ("MD-0005", "never_trained", "S1"),          # 개발 평가 — 전문가가 S2 로 바꿈
    ("MD-0006", "never_trained_sealed", "S2"),   # 봉인
    ("MD-0007", "fs_train_loss", "S1"),          # 두 전문가가 다르게 판정
    ("MD-0008", "fs_train_loss", "S3"),          # 검수 뒤 본문이 바뀜
    ("MD-0009", "fs_train_loss", "S2"),          # 보류
]


@pytest.fixture(autouse=True)
def _fresh_cache():
    pgs._CANDIDATE_CACHE.clear()
    yield
    pgs._CANDIDATE_CACHE.clear()


def _cand(root: Path, doc_id: str, text: str) -> None:
    (root / f"{doc_id}.cleaned.md").write_text(text, encoding="utf-8")
    meta = {"doc_id": doc_id, "intended_label": "S1", "document_origin": "synthetic", "candidate_status": "proposed",
            "review_batch": BATCH, "content_revision_path": f"{doc_id}.cleaned.md"}
    (root / f"{doc_id}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def pool(tmp_path):
    root = tmp_path / "pool"
    root.mkdir()
    for d, _, _ in DOCS:
        _cand(root, d, f"문서 {d} 의 본문입니다. " * 10)
    manifest = tmp_path / "internal_manifest.jsonl"
    manifest.write_text("\n".join(json.dumps({
        "review_id": d, "training_use": use, "grade": g, "round": "R1", "family_id": f"F{i}"})
        for i, (d, use, g) in enumerate(DOCS)), encoding="utf-8")
    svc = ProxyGoldCandidateService(root)
    svc.decide(doc_id="MD-0001", action="change", grade="S1", actor_id="kim.expert", reason="확인")
    svc.decide(doc_id="MD-0002", action="change", grade="S3", actor_id="kim.expert", reason="AI 보다 낮다")
    svc.decide(doc_id="MD-0004", action="change", grade="TS", actor_id="kim.expert", reason="확인")
    svc.decide(doc_id="MD-0005", action="change", grade="S2", actor_id="lee.expert", reason="AI 보다 낮다")
    svc.decide(doc_id="MD-0006", action="change", grade="S2", actor_id="kim.expert", reason="확인")
    svc.decide(doc_id="MD-0007", action="change", grade="S1", actor_id="kim.expert", reason="A")
    svc.decide(doc_id="MD-0007", action="change", grade="S2", actor_id="lee.expert", reason="B — 서로 다르다")
    svc.decide(doc_id="MD-0008", action="change", grade="S3", actor_id="kim.expert", reason="확인")
    svc.decide(doc_id="MD-0009", action="defer", actor_id="kim.expert", reason="애매하다")
    (root / "MD-0008.cleaned.md").write_text("검수 뒤에 누가 본문을 고쳤다. " * 10, encoding="utf-8")   # 결정 당시 본문과 다르다
    pgs._CANDIDATE_CACHE.clear()
    return root, manifest


def _build(tmp_path, pool, **kw):
    root, manifest = pool
    return brd.build(tmp_path / "out", root=root, manifest_path=manifest, verify_roles=False, **kw)


def _rows(out: Path, rel: str) -> list[dict]:
    return [json.loads(x) for x in (out / rel).read_text(encoding="utf-8").splitlines() if x.strip()]


def test_only_expert_confirmed_documents_enter_and_each_keeps_its_fixed_role(tmp_path, pool):
    s = _build(tmp_path, pool)
    out = tmp_path / "out"

    train = _rows(out, "1_학습셋/train.jsonl")
    assert {(r["doc_id"], r["label"]) for r in train} == {("MD-0001", "S1"), ("MD-0002", "S3")}
    assert [r["doc_id"] for r in _rows(out, "3_평가셋/val.jsonl")] == ["MD-0004"]
    dev = _rows(out, "3_평가셋/dev.jsonl")
    assert [(r["doc_id"], r["label"], r["ai_label"], r["label_changed"]) for r in dev] == [("MD-0005", "S2", "S1", True)]
    # 라벨 출처와 검수자가 행에 남는다
    r2 = next(r for r in train if r["doc_id"] == "MD-0002")
    assert (r2["label_origin"], r2["expert_id"], r2["ai_label"], r2["label_changed"]) == ("expert_review", "kim.expert", "S2", True)
    assert s["roles"]["학습셋"]["label_changed"] == 1 and s["roles"]["학습셋"]["by_grade"] == {"S1": 1, "S3": 1}


def test_documents_that_are_not_confirmed_go_to_the_review_file_with_a_reason(tmp_path, pool):
    s = _build(tmp_path, pool)

    excluded = {e["doc_id"]: e["reason"] for e in _rows(tmp_path / "out", "검토_필요.jsonl")}
    assert excluded == {"MD-0003": "pending", "MD-0007": "reviewer_conflict",
                        "MD-0008": "text_changed_after_review", "MD-0009": "deferred"}
    assert s["not_included"] == {"deferred": 1, "pending": 1, "reviewer_conflict": 1, "text_changed_after_review": 1}


def test_the_golden_set_is_written_only_when_asked(tmp_path, pool):
    s = _build(tmp_path, pool)
    assert not (tmp_path / "out" / "2_골든셋").exists()
    assert s["roles"]["골든셋"]["count"] == 1 and s["roles"]["골든셋"]["file"] is None

    s2 = brd.build(tmp_path / "out2", root=pool[0], manifest_path=pool[1], verify_roles=False, include_golden=True)
    assert [(r["doc_id"], r["label"]) for r in _rows(tmp_path / "out2", "2_골든셋/golden.jsonl")] == [("MD-0006", "S2")]
    assert s2["roles"]["골든셋"]["file"] == "2_골든셋/golden.jsonl"


def test_three_seed_train_commands_point_at_the_role_files(tmp_path, pool):
    s = _build(tmp_path, pool)
    out = tmp_path / "out"

    assert len(s["next_train_commands"]) == 3
    c = s["next_train_commands"][0]
    assert "--seed 42" in c and f"--train-path {out / '1_학습셋' / 'train.jsonl'}" in c
    assert f"--val-path {out / '3_평가셋' / 'val.jsonl'}" in c and f"--test-path {out / '3_평가셋' / 'dev.jsonl'}" in c
    assert "golden" not in " ".join(s["next_train_commands"])       # 골든셋은 학습·비교 명령에 안 들어간다


def test_provisional_rehearsal_splits_by_role_but_is_marked_and_never_emits_training(tmp_path, pool):
    s = _build(tmp_path, pool, label_source="ai_provisional")

    assert s["provisional_do_not_train"] is True and s["next_train_commands"] == []
    train = _rows(tmp_path / "out", "1_학습셋/train.jsonl")
    assert sorted(r["doc_id"] for r in train) == ["MD-0001", "MD-0002", "MD-0003", "MD-0007", "MD-0008", "MD-0009"]
    assert all(r["label_origin"] == "ai_provisional" for r in train)
    with pytest.raises(SystemExit, match="ai_provisional"):
        brd.build(tmp_path / "out3", root=pool[0], manifest_path=pool[1], verify_roles=False,
                  label_source="ai_provisional", include_golden=True)


def test_identical_text_across_roles_stops_the_build(tmp_path, pool):
    root, manifest = pool
    same = (root / "MD-0001.cleaned.md").read_text(encoding="utf-8")
    _cand(root, "MD-0010", same)                                 # 개발 평가 문서인데 학습 문서와 본문이 같다
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n" + json.dumps(
        {"review_id": "MD-0010", "training_use": "never_trained", "grade": "S1", "round": "R1", "family_id": "F9"}),
        encoding="utf-8")
    ProxyGoldCandidateService(root).decide(doc_id="MD-0010", action="change", grade="S1", actor_id="kim.expert", reason="x")
    pgs._CANDIDATE_CACHE.clear()

    with pytest.raises(SystemExit, match="train-on-test"):
        _build(tmp_path, pool)


def test_a_machine_account_decision_is_not_used(tmp_path, pool):
    root, manifest = pool
    ledger = root / "candidate_decisions.jsonl"
    events = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    last = next(e for e in reversed(events) if e["doc_id"] == "MD-0002")
    ledger.write_text(ledger.read_text(encoding="utf-8") + json.dumps({**last, "event_id": "x", "actor_id": "ai_assist:bot"},
                                                                       ensure_ascii=False) + "\n", encoding="utf-8")
    pgs._CANDIDATE_CACHE.clear()

    _build(tmp_path, pool)

    excluded = {e["doc_id"]: e["reason"] for e in _rows(tmp_path / "out", "검토_필요.jsonl")}
    assert excluded["MD-0002"] == "machine_actor"


def test_the_decision_ledger_is_only_read_and_a_reopen_voids_earlier_judgments(tmp_path, pool):
    root, _ = pool
    before = (root / "candidate_decisions.jsonl").read_bytes()
    _build(tmp_path, pool)
    assert (root / "candidate_decisions.jsonl").read_bytes() == before

    svc = ProxyGoldCandidateService(root)
    svc.decide(doc_id="MD-0007", action="reopen", actor_id="kim.expert", reason="다시 본다")
    svc.decide(doc_id="MD-0007", action="change", grade="S2", actor_id="lee.expert", reason="조정 결과")
    pgs._CANDIDATE_CACHE.clear()
    brd.build(tmp_path / "again", root=root, manifest_path=pool[1], verify_roles=False)
    assert ("MD-0007", "S2") in {(r["doc_id"], r["label"]) for r in _rows(tmp_path / "again", "1_학습셋/train.jsonl")}


def test_a_non_empty_output_folder_is_not_overwritten(tmp_path, pool):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("남의 파일", encoding="utf-8")
    with pytest.raises(SystemExit, match="비어 있지 않다"):
        _build(tmp_path, pool)
    assert (out / "keep.txt").exists()
