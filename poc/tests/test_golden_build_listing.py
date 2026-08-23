"""등록할 수 있는 문서 묶음 목록 — 화면이 고르게 한다.

왜(2026-08-20 사용자 지적). 관리자 콘솔의 「골든셋 후보 빌더」 카드에서 등록 칸이 자유
입력이었고, placeholder 로 `datasets/gold_real/builds/build_xxxx.jsonl` 이 **채워진 값처럼
보였다.** 실제로는 빈 칸이라 그대로 [등록]을 누르면 "경로를 입력하세요" 가 떴다. 서버에
어떤 파일이 있는지 화면이 알려주지 않으니, 검수자는 존재하지도 않는 경로를 외워 쳐야 했다.

⚠ 판별을 **파일명이 아니라 첫 줄 내용**으로 하는 것이 요점이다. `build_*.jsonl` 규칙에
  기대면 정작 KL 전달본(`datasets/golden_review/ff5a822c/candidates.jsonl`)이 목록에서
  빠진다 — 그 파일이 실제 검수 대상인데 이름이 build_ 로 시작하지 않는다(2026-08-20 실측).
"""

from __future__ import annotations

import json

import pytest

from koipa.services import golden_build_service as svc


@pytest.fixture
def datasets(tmp_path, monkeypatch):
    """datasets/ 루트를 임시 폴더로 바꿔 실제 저장소를 읽지 않게 한다."""
    root = tmp_path / "datasets"
    root.mkdir()
    monkeypatch.setattr(svc, "_POC_ROOT", tmp_path)
    return root


def _write(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


SLATE = [{"doc_id": "a", "text": "본문", "label": "S2"}, {"doc_id": "b", "text": "본문2", "label": "TS"}]


def test_slate_is_found_by_content_not_by_filename(datasets):
    """이름이 build_ 로 시작하지 않아도 잡혀야 한다 — 실제 전달본이 그렇다."""
    _write(datasets / "golden_review" / "ff5a822c" / "candidates.jsonl", SLATE)
    got = svc.GoldenBuildService().list_registerable_builds()
    assert [b["path"] for b in got] == ["datasets/golden_review/ff5a822c/candidates.jsonl"]
    assert got[0]["records"] == 2
    assert got[0]["records_exact"] is True


def test_non_slate_files_are_skipped(datasets):
    """본문이나 등급이 없는 jsonl 은 검수에 올릴 수 없다 — 목록에 넣으면 골라 놓고 실패한다."""
    gr = datasets / "golden_review"
    _write(gr / "logs.jsonl", [{"event": "x"}])                 # text·label 없음
    _write(gr / "text_only.jsonl", [{"text": "본문"}])           # 등급 없음
    _write(gr / "label_only.jsonl", [{"label": "S1"}])           # 본문 없음
    (gr / "empty.jsonl").write_text("", encoding="utf-8")
    (gr / "broken.jsonl").write_text("{not json", encoding="utf-8")
    assert svc.GoldenBuildService().list_registerable_builds() == []


def test_grade_key_also_counts_as_a_slate(datasets):
    """등급 필드 이름이 label 이 아니라 grade 인 묶음도 있다."""
    _write(datasets / "golden_review" / "g.jsonl", [{"text": "본문", "grade": "S3"}])
    assert len(svc.GoldenBuildService().list_registerable_builds()) == 1


def test_newest_first(datasets):
    import os
    import time
    _write(datasets / "golden_review" / "old.jsonl", SLATE)
    _write(datasets / "golden_review" / "new.jsonl", SLATE)
    now = time.time()
    os.utime(datasets / "golden_review" / "old.jsonl", (now - 86400, now - 86400))
    os.utime(datasets / "golden_review" / "new.jsonl", (now, now))
    got = svc.GoldenBuildService().list_registerable_builds()
    assert [b["path"] for b in got] == [
        "datasets/golden_review/new.jsonl", "datasets/golden_review/old.jsonl"]


def test_limit_is_respected(datasets):
    for i in range(5):
        _write(datasets / "golden_review" / f"s{i}.jsonl", SLATE)
    assert len(svc.GoldenBuildService().list_registerable_builds(limit=3)) == 3


def test_listing_stays_inside_the_datasets_sandbox(datasets, tmp_path):
    """등록(register)과 같은 경계여야 한다 — 고를 수 있는데 못 올리면 화면이 거짓말을 한다."""
    _write(tmp_path / "outside.jsonl", SLATE)
    _write(datasets / "golden_review" / "inside.jsonl", SLATE)
    got = svc.GoldenBuildService().list_registerable_builds()
    assert [b["path"] for b in got] == ["datasets/golden_review/inside.jsonl"]


def test_listed_paths_are_actually_registerable(datasets):
    """목록이 준 경로를 그대로 등록에 넣으면 잡이 만들어져야 한다."""
    _write(datasets / "golden_review" / "x" / "candidates.jsonl", SLATE)
    s = svc.GoldenBuildService()
    path = s.list_registerable_builds()[0]["path"]
    job_id = s.register_build(path, actor_user_id="지재원관리자")
    assert job_id is not None


def test_evaluation_and_training_sets_are_not_listed(datasets):
    """평가셋·학습셋·실험 분할은 목록에 뜨면 안 된다.

    왜(2026-08-23 실측). 목록이 datasets/ 전체를 훑던 시절, 60건 안에 평가 홀드아웃
    (holdout_eval.hardened.jsonl — 시연 근거로 쓴 42건)·누출 격리본·v8 실험 분할
    (dev/calib)이 섞여 있었다. 관리자가 그중 하나를 골라 검수·서명하면 그 문서들이
    locked_gold_eval(평가 정답지)로 승격되고, 그 순간 "모델이 좋아졌다"는 판단 근거가
    무너진다 — 평가셋이 정답지 안으로 들어가기 때문이다.

    이름으로 걸러 봤더니 실험 폴더가 계속 새 이름으로 생겨 따라잡히지 않았다.
    **무엇을 뺄지가 아니라 무엇을 넣을지**로 정의한다(_REVIEW_SOURCE_DIRS).
    """
    _write(datasets / "gold_real" / "holdout_eval.hardened.jsonl", SLATE)
    _write(datasets / "gold_real" / "nohuman_proxy" / "train_leak_nohuman.jsonl", SLATE)
    _write(datasets / "v8" / "dev.jsonl", SLATE)
    _write(datasets / "v8_r14" / "calib.jsonl", SLATE)
    _write(datasets / "golden_review" / "ff5a822c" / "candidates.jsonl", SLATE)
    _write(datasets / "gold_real" / "builds" / "demo_slate_v1.jsonl", SLATE)

    got = [b["path"] for b in svc.GoldenBuildService().list_registerable_builds()]
    assert sorted(got) == [
        "datasets/gold_real/builds/demo_slate_v1.jsonl",
        "datasets/golden_review/ff5a822c/candidates.jsonl",
    ], got


def test_already_promoted_records_are_not_listed(datasets):
    """서명이 끝나 승격된 정본(locked_*.jsonl)은 다시 검수 대상이 아니다.

    실측 2026-08-23(223): 허용 폴더로 좁힌 뒤에도 gold_real/builds 안의 locked_*.jsonl 2건이
    목록에 남아 있었다. 그대로 두면 같은 문서를 두 번 서명하게 된다.
    """
    _write(datasets / "gold_real" / "builds" / "locked_94f310b2.jsonl", SLATE)
    _write(datasets / "gold_real" / "builds" / "demo_slate_v1.jsonl", SLATE)
    got = [b["path"] for b in svc.GoldenBuildService().list_registerable_builds()]
    assert got == ["datasets/gold_real/builds/demo_slate_v1.jsonl"], got
