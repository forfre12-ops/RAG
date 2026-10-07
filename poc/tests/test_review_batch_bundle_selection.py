"""지재원에 가는 번들이 싣는 검수 후보가 '검수 요청 대상'과 정확히 같은가.

배경(2026-09-25): "이것만 골든셋 검수 후보에 넣은 거 맞아?" — 아니었다. 빌더가 `MD-*` 를 전부(1,731건) 복사해
품질 결함으로 뺀 20건까지 지재원에 갈 뻔했다. 제외 목록(evidence/review_request_exclusions.jsonl)을 읽어
그 문서의 메타·본문 파일을 빼고 싣는다. 이 시험이 그 선택을 잠근다.

[2026-10-03 결함 수정] doc_id 접두사("MD-*") 로 배치를 가르던 것을 `review_batch` 메타데이터
필드로 바꿨다 — 접두사 하드코딩은 배치가 바뀌면(mock1000 "MK-*") 조용히 0건을 실었다(실측).
이 파일의 본보기도 review_batch 필드를 쓰도록 맞춘다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_offline_bundle import load_review_request_exclusions, select_review_batch_files  # noqa: E402

BATCH = "expert_review_mock1000_20261002"


def _write(root: Path, doc_id: str, *, batch: str = BATCH) -> None:
    (root / f"{doc_id}.metadata.json").write_text(
        json.dumps({"doc_id": doc_id, "review_batch": batch}), encoding="utf-8")
    (root / f"{doc_id}_review.md").write_text("본문", encoding="utf-8")


def _make_batch(root: Path) -> None:
    for i in (1, 2, 3, 43):
        _write(root, f"MK-{i:04d}")
    # 이 배치가 아닌 파일은 어느 경우에도 안 싣는다
    _write(root, "FD-0001", batch="expert_review_1731_20260924")
    _write(root, "GOLD-B1-S1-001", batch="")
    (root / "candidate_decisions.jsonl").write_text("", encoding="utf-8")


def test_excluded_documents_lose_both_their_metadata_and_body_files(tmp_path):
    _make_batch(tmp_path)

    names = [f.name for f in select_review_batch_files(tmp_path, {"MK-0043", "MK-0002"}, BATCH)]

    assert names == ["MK-0001.metadata.json", "MK-0001_review.md", "MK-0003.metadata.json", "MK-0003_review.md"]


def test_a_different_batch_tag_is_never_included(tmp_path):
    """review_batch 값이 다르면(옛 배치 포함) 아이디가 비슷해 보여도 절대 안 실린다."""
    _make_batch(tmp_path)

    names = {f.name for f in select_review_batch_files(tmp_path, set(), BATCH)}

    assert "FD-0001.metadata.json" not in names
    assert "GOLD-B1-S1-001.metadata.json" not in names


def test_metadata_without_a_matching_body_file_is_skipped(tmp_path):
    (tmp_path / "MK-0099.metadata.json").write_text(
        json.dumps({"doc_id": "MK-0099", "review_batch": BATCH}), encoding="utf-8")
    # 본문(_review.md) 없음 — 짝 없는 메타만 싣지 않는다.

    assert select_review_batch_files(tmp_path, set(), BATCH) == []


def test_broken_metadata_json_is_skipped_not_fatal(tmp_path):
    (tmp_path / "MK-0001.metadata.json").write_text("이건 JSON 이 아니다", encoding="utf-8")
    (tmp_path / "MK-0001_review.md").write_text("본문", encoding="utf-8")

    assert select_review_batch_files(tmp_path, set(), BATCH) == []


def test_nothing_is_excluded_when_the_list_is_absent_or_broken(tmp_path):
    _make_batch(tmp_path)
    assert load_review_request_exclusions(tmp_path / "없는 파일.jsonl") == set()

    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"doc_id": "MK-0002"}\n이건 JSON 이 아니다\n\n{"reasons": []}\n', encoding="utf-8")
    assert load_review_request_exclusions(broken) == {"MK-0002"}   # 깨진 줄·doc_id 없는 줄은 건너뛴다
    assert len(select_review_batch_files(tmp_path, set(), BATCH)) == 8    # 제외 없음 → 4문서 × 2파일 전부


def test_the_tool_writes_the_list_the_builder_reads(tmp_path):
    import audit_golden_candidate_pool as agp

    order = {"2_제외(품질 결함)": ["MK-0043", "MK-0002"]}
    quality = {"MK-0043": {"defects": ["판정자 이견"]}, "MK-0002": {"defects": ["길이 이탈", "haiku 작성"]}}
    path = tmp_path / "exclusions.jsonl"

    assert agp.write_exclusions(order, quality, path) == 2
    assert load_review_request_exclusions(path) == {"MK-0043", "MK-0002"}
    assert "\r" not in path.read_text(encoding="utf-8")            # 줄바꿈은 LF 로 고정(윈도에서도 diff 안 흔들림)
