"""지재원에 가는 번들이 싣는 검수 후보가 '검수 요청 대상'과 정확히 같은가.

배경(2026-09-25): "이것만 골든셋 검수 후보에 넣은 거 맞아?" — 아니었다. 빌더가 `MD-*` 를 전부(1,731건) 복사해
품질 결함으로 뺀 20건까지 지재원에 갈 뻔했다. 제외 목록(evidence/review_request_exclusions.jsonl)을 읽어
그 문서의 메타·본문 파일을 빼고 싣는다. 이 시험이 그 선택을 잠근다.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_offline_bundle import load_review_request_exclusions, select_review_batch_files  # noqa: E402


def _make_batch(root: Path) -> None:
    for i in (1, 2, 3, 43):
        (root / f"MD-{i:04d}.metadata.json").write_text("{}", encoding="utf-8")
        (root / f"MD-{i:04d}_review.md").write_text("본문", encoding="utf-8")
    # 이 배치가 아닌 파일은 어느 경우에도 안 싣는다
    (root / "FD-0001.metadata.json").write_text("{}", encoding="utf-8")
    (root / "GOLD-B1-S1-001.metadata.json").write_text("{}", encoding="utf-8")
    (root / "candidate_decisions.jsonl").write_text("", encoding="utf-8")


def test_excluded_documents_lose_both_their_metadata_and_body_files(tmp_path):
    _make_batch(tmp_path)

    names = [f.name for f in select_review_batch_files(tmp_path, {"MD-0043", "MD-0002"})]

    assert names == ["MD-0001.metadata.json", "MD-0001_review.md", "MD-0003.metadata.json", "MD-0003_review.md"]


def test_an_id_that_only_starts_the_same_is_not_excluded(tmp_path):
    """MD-0004 를 빼라고 했을 때 MD-00043 같은 다른 문서가 딸려 빠지면 안 된다(접두 일치 오류)."""
    (tmp_path / "MD-0004.metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "MD-00043.metadata.json").write_text("{}", encoding="utf-8")

    names = [f.name for f in select_review_batch_files(tmp_path, {"MD-0004"})]

    assert names == ["MD-00043.metadata.json"]


def test_nothing_is_excluded_when_the_list_is_absent_or_broken(tmp_path):
    _make_batch(tmp_path)
    assert load_review_request_exclusions(tmp_path / "없는 파일.jsonl") == set()

    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"doc_id": "MD-0002"}\n이건 JSON 이 아니다\n\n{"reasons": []}\n', encoding="utf-8")
    assert load_review_request_exclusions(broken) == {"MD-0002"}   # 깨진 줄·doc_id 없는 줄은 건너뛴다
    assert len(select_review_batch_files(tmp_path, set())) == 8    # 제외 없음 → 4문서 × 2파일 전부


def test_the_tool_writes_the_list_the_builder_reads(tmp_path):
    import audit_golden_candidate_pool as agp

    order = {"2_제외(품질 결함)": ["MD-0043", "MD-0002"]}
    quality = {"MD-0043": {"defects": ["판정자 이견"]}, "MD-0002": {"defects": ["길이 이탈", "haiku 작성"]}}
    path = tmp_path / "exclusions.jsonl"

    assert agp.write_exclusions(order, quality, path) == 2
    assert load_review_request_exclusions(path) == {"MD-0043", "MD-0002"}
    assert "\r" not in path.read_text(encoding="utf-8")            # 줄바꿈은 LF 로 고정(윈도에서도 diff 안 흔들림)
