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
    _write(datasets / "logs.jsonl", [{"event": "x"}])                 # text·label 없음
    _write(datasets / "text_only.jsonl", [{"text": "본문"}])           # 등급 없음
    _write(datasets / "label_only.jsonl", [{"label": "S1"}])           # 본문 없음
    (datasets / "empty.jsonl").write_text("", encoding="utf-8")
    (datasets / "broken.jsonl").write_text("{not json", encoding="utf-8")
    assert svc.GoldenBuildService().list_registerable_builds() == []


def test_grade_key_also_counts_as_a_slate(datasets):
    """등급 필드 이름이 label 이 아니라 grade 인 묶음도 있다."""
    _write(datasets / "g.jsonl", [{"text": "본문", "grade": "S3"}])
    assert len(svc.GoldenBuildService().list_registerable_builds()) == 1


def test_newest_first(datasets):
    import os
    import time
    _write(datasets / "old.jsonl", SLATE)
    _write(datasets / "new.jsonl", SLATE)
    now = time.time()
    os.utime(datasets / "old.jsonl", (now - 86400, now - 86400))
    os.utime(datasets / "new.jsonl", (now, now))
    got = svc.GoldenBuildService().list_registerable_builds()
    assert [b["path"] for b in got] == ["datasets/new.jsonl", "datasets/old.jsonl"]


def test_limit_is_respected(datasets):
    for i in range(5):
        _write(datasets / f"s{i}.jsonl", SLATE)
    assert len(svc.GoldenBuildService().list_registerable_builds(limit=3)) == 3


def test_listing_stays_inside_the_datasets_sandbox(datasets, tmp_path):
    """등록(register)과 같은 경계여야 한다 — 고를 수 있는데 못 올리면 화면이 거짓말을 한다."""
    _write(tmp_path / "outside.jsonl", SLATE)
    _write(datasets / "inside.jsonl", SLATE)
    got = svc.GoldenBuildService().list_registerable_builds()
    assert [b["path"] for b in got] == ["datasets/inside.jsonl"]


def test_listed_paths_are_actually_registerable(datasets):
    """목록이 준 경로를 그대로 등록에 넣으면 잡이 만들어져야 한다."""
    _write(datasets / "golden_review" / "x" / "candidates.jsonl", SLATE)
    s = svc.GoldenBuildService()
    path = s.list_registerable_builds()[0]["path"]
    job_id = s.register_build(path, actor_user_id="지재원관리자")
    assert job_id is not None
