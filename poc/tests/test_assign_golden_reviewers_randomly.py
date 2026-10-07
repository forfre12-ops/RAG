"""무작위·비중복 검수 배정 스크립트 — 이상민(영업비밀보호센터 주임) 요청 #3 대응.

AssignmentLedger.apply() 는 관리자가 누구에게 무엇을 배정할지 직접 정해서 넣는 수동
기능만 제공한다. 이 스크립트는 그 위에 "후보군을 섞어 N명에게 교집합 없이 나눈다"는
로직 하나만 얹는다 — 겹치지 않는 이유는 ledger 가 막아서가 아니라 분배 자체가
파티션(한 문서는 정확히 한 묶음에만 들어감)이기 때문이다. 재실행해도 이미 배정된
문서가 다른 사람에게 다시 가지 않는 것(풀에서 먼저 뺀다)도 확인한다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assign_golden_reviewers_randomly.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("_assign_golden_reviewers_randomly", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_candidate(root: Path, doc_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{doc_id}_review.md").write_text(f"본문 {doc_id}", encoding="utf-8")
    meta = {"doc_id": doc_id, "intended_label": "S2", "document_origin": "synthetic",
             "candidate_status": "proposed"}
    (root / f"{doc_id}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


# ── split_evenly ──────────────────────────────────────────────────────────


def test_split_evenly_divides_without_remainder(script):
    groups = script.split_evenly(list("abcdef"), 3)
    assert [len(g) for g in groups] == [2, 2, 2]
    assert sorted(sum(groups, [])) == sorted("abcdef")


def test_split_evenly_gives_remainder_to_earlier_groups(script):
    groups = script.split_evenly(list(range(7)), 3)
    assert [len(g) for g in groups] == [3, 2, 2]


def test_split_evenly_no_overlap_across_groups(script):
    pool = [f"D{i}" for i in range(23)]
    groups = script.split_evenly(pool, 5)
    seen: set[str] = set()
    for g in groups:
        assert not (seen & set(g)), "묶음 간 교집합이 있으면 안 된다"
        seen |= set(g)
    assert seen == set(pool)


def test_split_evenly_rejects_zero_or_negative_n(script):
    with pytest.raises(ValueError):
        script.split_evenly(["a"], 0)


# ── parse_reviewers ───────────────────────────────────────────────────────


def test_parse_reviewers_requires_at_least_two(script):
    with pytest.raises(SystemExit):
        script.parse_reviewers("혼자")


def test_parse_reviewers_rejects_duplicates(script):
    with pytest.raises(SystemExit):
        script.parse_reviewers("김변호사,이교수,김변호사")


def test_parse_reviewers_normalizes_whitespace(script):
    assert script.parse_reviewers(" 김변호사 , 이교수 ") == ["김변호사", "이교수"]


# ── already_assigned_doc_ids ─────────────────────────────────────────────


def test_already_assigned_doc_ids_covers_batch_assignment(script, tmp_path):
    ledger = script.AssignmentLedger(tmp_path)
    ledger.apply(event=script.EVENT_ASSIGN, reviewer_id="검수자A", actor_id="t",
                 targets=[("doc_id", "D1")])
    ledger.apply(event=script.EVENT_ASSIGN, reviewer_id="검수자B", actor_id="t",
                 targets=[("review_batch", "batch-1")])
    batches = {"D1": None, "D2": "batch-1", "D3": "batch-2", "D4": None}
    assigned = script.already_assigned_doc_ids(ledger, batches)
    assert assigned == {"D1", "D2"}


# ── main(): 종단 ──────────────────────────────────────────────────────────


def test_main_dry_run_writes_nothing(script, tmp_path, capsys):
    for i in range(4):
        _write_candidate(tmp_path, f"TEST-{i}")
    rc = script.main([
        "--root", str(tmp_path), "--reviewers", "A,B", "--actor-id", "t",
        "--seed", "1", "--dry-run",
    ])
    assert rc == 0
    assert not (tmp_path / "candidate_assignments.jsonl").exists()


def test_main_real_run_splits_without_overlap_and_is_idempotent(script, tmp_path):
    for i in range(7):
        _write_candidate(tmp_path, f"TEST-{i}")

    rc = script.main([
        "--root", str(tmp_path), "--reviewers", "A,B,C", "--actor-id", "t", "--seed", "1",
    ])
    assert rc == 0

    ledger = script.AssignmentLedger(tmp_path)
    state = ledger.state()
    all_assigned = [d for slot in state.values() for d in slot["doc_ids"]]
    assert sorted(all_assigned) == [f"TEST-{i}" for i in range(7)]
    assert len(all_assigned) == len(set(all_assigned)), "같은 문서가 두 번 배정되면 안 된다"

    # 새 문서 하나를 더해 재실행 — 기존 7건은 그대로, 새 문서만 누군가에게 간다.
    _write_candidate(tmp_path, "TEST-NEW")

    rc2 = script.main([
        "--root", str(tmp_path), "--reviewers", "A,B,C", "--actor-id", "t", "--seed", "2",
    ])
    assert rc2 == 0
    state2 = ledger.state()
    all_assigned2 = sorted(d for slot in state2.values() for d in slot["doc_ids"])
    assert all_assigned2 == sorted([f"TEST-{i}" for i in range(7)] + ["TEST-NEW"])


def test_main_reports_zero_pool_when_everything_already_assigned(script, tmp_path, capsys):
    _write_candidate(tmp_path, "TEST-0")
    script.main(["--root", str(tmp_path), "--reviewers", "A,B", "--actor-id", "t", "--seed", "1"])
    capsys.readouterr()
    rc = script.main(["--root", str(tmp_path), "--reviewers", "A,B", "--actor-id", "t", "--seed", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "나눌 후보가 없다" in out
