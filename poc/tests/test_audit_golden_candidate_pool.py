"""이번 회차 문서가 실제 학습·검증 파일에 들어 있는지 세는 도구 — 대장 라벨이 아니라 본문으로 대조하는가.

배경(2026-09-25): "1,731건은 학습 안 한 건가?"에 대장(internal_manifest.jsonl)의 training_use 라벨만
읽고 답하면, 라벨이 틀렸을 때 그대로 틀린다. 도구는 파일 본문의 sha256 을 직접 대조하고, 라벨과
어긋난 문서 수를 함께 알린다 — 그 수가 0 이 아니면 대장을 믿을 수 없다는 신호다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_golden_candidate_pool as agp  # noqa: E402
from koipa.services import proxy_gold_candidate_service as pgs  # noqa: E402

BATCH = agp.DELIVERED_BATCH
TEXTS = {"MD-0001": "학습에 쓴 문서 " * 20, "MD-0002": "검증에 쓴 문서 " * 20,
         "MD-0003": "개발 평가에 쓴 문서 " * 20, "MD-0004": "아무 데도 안 쓴 문서 " * 20}


@pytest.fixture(autouse=True)
def _fresh_cache():
    pgs._CANDIDATE_CACHE.clear()
    yield
    pgs._CANDIDATE_CACHE.clear()


def _jsonl(path: Path, texts: list[str]) -> Path:
    path.write_text("\n".join(json.dumps({"text": t}, ensure_ascii=False) for t in texts), encoding="utf-8")
    return path


@pytest.fixture()
def world(tmp_path, monkeypatch):
    root = tmp_path / "cands"
    root.mkdir()
    for doc_id, text in TEXTS.items():
        (root / f"{doc_id}.cleaned.md").write_text(text, encoding="utf-8")
        (root / f"{doc_id}.metadata.json").write_text(json.dumps({
            "doc_id": doc_id, "intended_label": "S1", "document_origin": "synthetic",
            "candidate_status": "proposed", "review_batch": BATCH,
            "content_revision_path": f"{doc_id}.cleaned.md",
        }, ensure_ascii=False), encoding="utf-8")
    files = {
        "학습": ("train", str(_jsonl(tmp_path / "train.jsonl", [TEXTS["MD-0001"], "관계 없는 학습 문서"]))),
        "검증": ("val", str(_jsonl(tmp_path / "val.jsonl", [TEXTS["MD-0002"]]))),
        "개발": ("dev", str(_jsonl(tmp_path / "dev.jsonl", [TEXTS["MD-0003"]]))),
        "배포": ("deployed", str(_jsonl(tmp_path / "deployed.jsonl", ["옛 배포모델 학습 문서"]))),
    }
    labels = {"MD-0001": "fs_train_loss", "MD-0002": "fs_val_holdout",
              "MD-0003": "never_trained", "MD-0004": "never_trained_sealed"}
    monkeypatch.setattr(agp, "review_uses", lambda: labels)
    return agp.load_rows(root), files, labels


def test_counts_come_from_the_files_not_from_the_labels(world):
    rows, files, _ = world
    t = agp.training_overlap(rows, files)

    assert t["delivered"] == 4
    assert (t["used"], t["not_used"]) == (2, 2)          # 학습 1 + 검증 1 / 개발 평가용·봉인은 안 씀
    assert t["files"]["학습"] == {"role": "train", "rows": 2, "overlap": 1}
    assert t["files"]["개발"]["overlap"] == 1
    assert t["deployed_overlap"] == 0                      # 지금 배포된 모델 학습셋과 안 겹친다
    assert t["label_mismatch"] == 0


def test_a_wrong_label_shows_up_as_a_mismatch(world, monkeypatch):
    rows, files, labels = world
    # 대장이 학습에 쓴 문서를 "안 썼다"고 잘못 적은 경우
    monkeypatch.setattr(agp, "review_uses", lambda: {**labels, "MD-0001": "never_trained"})
    t = agp.training_overlap(rows, files)
    assert t["label_mismatch"] == 1
    assert t["used"] == 2                                  # 사용 건수는 라벨이 아니라 파일이 정한다


def test_overlap_with_the_deployed_models_training_set_is_reported(world, tmp_path):
    rows, files, _ = world
    files["배포"] = ("deployed", str(_jsonl(tmp_path / "deployed2.jsonl", [TEXTS["MD-0004"]])))
    assert agp.training_overlap(rows, files)["deployed_overlap"] == 1


def _round_files(reports: Path, docs: dict, judged: list) -> None:
    """한 라운드 생성물 폴더(R1)를 흉내낸다 — 문서 표식 파일과 판정자 판독 파일."""
    d = reports / agp.DOCGEN_ROUNDS["R1"]
    d.mkdir(parents=True)
    lines = [json.dumps({"doc_key": k, "flags": f}) for k, f in docs.items()]
    (d / "pilot_docs_checked.jsonl").write_text("\n".join(lines), encoding="utf-8")
    (d / "pilot_judge_rows.json").write_text(json.dumps(judged), encoding="utf-8")


def test_doc_quality_flags_disagreeing_judges_length_and_haiku_but_not_alias(tmp_path, monkeypatch):
    man = tmp_path / "internal_manifest.jsonl"
    rows = [  # review_id, doc_key, writer — S·V·M 명세는 모두 (2,1,2)
        ("MD-0001", "A", "opus"), ("MD-0002", "B", "opus"), ("MD-0003", "C", "fable"),
        ("MD-0004", "D", "haiku"), ("MD-0005", "E", "sonnet")]
    lines = [json.dumps({"review_id": r, "round": "R1", "doc_key": k, "S": 2, "V": 1, "M": 2, "writer_model": w})
             for r, k, w in rows]
    man.write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr(agp, "REVIEW_MANIFEST", man)
    agree = {"S": [2, 2], "V": [1, 1], "M": [2, 2]}
    _round_files(tmp_path / "reports",
                 {"A": [], "B": [], "C": ["length_off"], "D": [], "E": ["alias_code"]},   # E: 별칭 의심은 결함이 아니다
                 [{"doc_key": "A", **agree}, {"doc_key": "B", **{**agree, "S": [2, 1]}},   # B: 판정자 한 명이 S 를 다르게 읽음
                  {"doc_key": "C", **agree}, {"doc_key": "D", **agree}, {"doc_key": "E", **agree}])

    q = agp.doc_quality(tmp_path / "reports")

    assert {k: v["defects"] for k, v in q.items()} == {
        "MD-0001": [], "MD-0002": ["판정자 이견"], "MD-0003": ["길이 이탈"],
        "MD-0004": ["haiku 작성"], "MD-0005": []}
    assert (q["MD-0002"]["judge_points"], q["MD-0002"]["judge_total"]) == (5, 6)


def test_review_order_picks_by_quality_not_by_training_use(tmp_path, monkeypatch):
    root = tmp_path / "cands2"
    root.mkdir()

    def cand(doc_id, origin="synthetic", batch=None, grade="S3"):
        (root / f"{doc_id}.cleaned.md").write_text(f"본문 {doc_id} " * 20, encoding="utf-8")
        meta = {"doc_id": doc_id, "intended_label": grade, "document_origin": origin,
                "candidate_status": "under_review" if origin == "public_real" else "proposed",
                "content_revision_path": f"{doc_id}.cleaned.md"}
        if batch:
            meta["review_batch"] = batch
        (root / f"{doc_id}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    for d in ("MD-0001", "MD-0002", "MD-0003", "MD-0004"):
        cand(d, batch=BATCH)
    cand("FD-0009", batch="old_copy")                    # 사본 — 어느 묶음에도 안 들어간다
    cand("GOLD-B1-S1-001")                               # 옛 합성 후보 — 넣지 않는다
    cand("real-1", origin="public_real")                 # S3 제안 — 요청 안 함
    cand("real-2", origin="public_real", grade="S1")     # S1 제안 — 감리가 지적한 쪽, 선택
    quality = {  # MD-0001 은 학습에 쓴 문서지만 품질이 좋으면 요청 대상이다(재학습이 목적)
        "MD-0001": {"defects": []}, "MD-0002": {"defects": ["판정자 이견"]},
        "MD-0003": {"defects": []}}                       # MD-0004 는 품질 기록이 없다
    monkeypatch.setattr(agp, "review_uses", lambda: {"MD-0001": "fs_train_loss"})

    order = agp.review_order(agp.load_rows(root), quality)

    assert order == {"1_요청(품질 통과)": ["MD-0001", "MD-0003"], "2_제외(품질 결함)": ["MD-0002"],
                     "3_공개 실문서 S1·S2 제안(선택)": ["real-2"], "4_공개 실문서 S3 제안(요청 안 함)": ["real-1"],
                     "9_품질 기록 없음": ["MD-0004"]}


def test_a_missing_file_is_reported_not_crashed_on(world, tmp_path):
    rows, files, _ = world
    files["없는 파일"] = ("train", str(tmp_path / "nope.jsonl"))
    t = agp.training_overlap(rows, files)
    assert t["files"]["없는 파일"] == {"role": "train", "rows": None, "overlap": None}
    assert t["used"] == 2
