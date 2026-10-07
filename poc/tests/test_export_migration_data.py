"""서버 재구성용 데이터 내보내기 — 빠뜨리지 않고, 옮긴 뒤 검증되는가.

왜(2026-09-07). OS 를 Rocky 로 바꾸며 테스트 서버를 다시 세운다. 클론만으로는 아무것도
못 돌린다 — 실측: poc/datasets 16,508 파일 중 git 추적은 234개(1.4%)다. 골든셋 검수
후보(datasets/golden_review/ 1,336파일)도 그 밖에 있다.

폐쇄망 번들은 **설치용**이라 코드·이미지·모델만 담고 데이터는 담지 않는다. 그 자리를
이 스크립트가 채운다. 이 시험이 잠그는 것은 둘이다.

  ① git 밖의 파일만 담고, 추적본은 담지 않는다(클론으로 따라오므로)
  ② 옮긴 뒤 **검증된다** — 전송 중 잘린 파일이 조용히 섞이면 나중에 못 가린다
"""
from __future__ import annotations

import sys
from pathlib import Path


_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))
import export_migration_data as exp  # noqa: E402


def test_export_then_verify_roundtrip(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "datasets" / "golden_review").mkdir(parents=True)
    (src / "datasets" / "golden_review" / "candidates.jsonl").write_text("a\n", encoding="utf-8")
    (src / "datasets" / "keep.jsonl").write_text("b\n", encoding="utf-8")

    monkeypatch.setattr(exp, "_POC", src)
    monkeypatch.setattr(exp, "_tracked_files", lambda root: set())

    files = exp.collect(("datasets",))
    assert len(files) == 2

    out = tmp_path / "out"
    exp.export(files, out, dry_run=False)
    assert (out / "datasets" / "golden_review" / "candidates.jsonl").exists()
    assert exp.verify(out) == 0, "막 내보낸 것이 검증에 실패하면 안 된다"


def test_tracked_files_are_not_copied(tmp_path, monkeypatch):
    """git 이 가진 것은 클론으로 따라온다 — 두 번 옮기지 않는다."""
    src = tmp_path / "src"
    (src / "datasets").mkdir(parents=True)
    (src / "datasets" / "tracked.jsonl").write_text("x\n", encoding="utf-8")
    (src / "datasets" / "untracked.jsonl").write_text("y\n", encoding="utf-8")

    monkeypatch.setattr(exp, "_POC", src)
    monkeypatch.setattr(exp, "_tracked_files", lambda root: {"datasets/tracked.jsonl"})

    names = [f.name for f in exp.collect(("datasets",))]
    assert names == ["untracked.jsonl"]


def test_corrupted_transfer_is_caught(tmp_path, monkeypatch):
    """전송 중 잘린 파일을 조용히 통과시키면 안 된다."""
    src = tmp_path / "src"
    (src / "datasets").mkdir(parents=True)
    (src / "datasets" / "a.jsonl").write_text("원본 내용\n", encoding="utf-8")
    monkeypatch.setattr(exp, "_POC", src)
    monkeypatch.setattr(exp, "_tracked_files", lambda root: set())

    out = tmp_path / "out"
    exp.export(exp.collect(("datasets",)), out, dry_run=False)
    (out / "datasets" / "a.jsonl").write_text("망가짐\n", encoding="utf-8")
    assert exp.verify(out) == 1, "내용이 바뀐 파일을 잡지 못했다"


def test_missing_manifest_is_not_a_pass(tmp_path):
    """MANIFEST 가 없으면 '검증했다'가 아니라 '못 했다'다."""
    assert exp.verify(tmp_path) == 2


def test_artifacts_are_not_in_the_default_roots():
    """artifacts/ 는 331GB 이고 배포 모델은 번들이 담는다 — 기본에서 뺀다."""
    assert "artifacts" not in exp.DEFAULT_ROOTS
    assert "datasets" in exp.DEFAULT_ROOTS
