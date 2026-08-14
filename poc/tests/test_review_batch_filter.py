"""검수 배치 필터 — 검수자가 어느 문서를 봐야 하는지 알 수 있어야 한다.

왜(실측 2026-08-14). KL 검수 전달본 ff5a822c 120건을 콘솔에 적재하고 확인해 보니
콘솔 전체 목록이 306건이었다. 배치 표식이 없으면 검수자가 306건 앞에서 어느 120건을
봐야 하는지 알 수 없다. 배포한 뒤에 발견했으면 검수 회차가 그대로 멈췄을 자리다.

같은 점검에서 적재 자체의 구멍도 나왔다 — 120건 중 13건이 콘솔에 없었다
(GOLD-PILOT 8 · 판례 3 · 금융보고서 2). 적재 후 120/120 이 됐다.
"""
from __future__ import annotations

import json

import pytest

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService

BATCH = "ff5a822c"
POOL = "datasets/golden_review/ff5a822c/candidates.jsonl"


def _pool_ids() -> set[str]:
    from pathlib import Path

    p = Path(POOL)
    if not p.exists():
        pytest.skip(f"{POOL} 없음")
    out = set()
    for line in p.read_text("utf-8").splitlines():
        if line.strip():
            i = json.loads(line)["doc_id"]
            out.add(i.split("_")[0] if i.startswith("GOLD-") else i)
    return out


def test_batch_filter_narrows_to_the_delivered_pool():
    svc = ProxyGoldCandidateService()
    everything = svc.list_candidates()
    batch = svc.list_candidates(review_batch=BATCH)
    assert batch["total"] < everything["total"], "필터가 목록을 좁히지 못한다"
    assert batch["total"] == len(_pool_ids()), "전달본 건수와 다르다"


def test_every_delivered_document_is_visible():
    """적재 누락이 있으면 검수자가 그 문서를 영영 못 본다."""
    svc = ProxyGoldCandidateService()
    shown = {c["doc_id"] for c in svc.list_candidates(review_batch=BATCH)["candidates"]}
    missing = _pool_ids() - shown
    assert not missing, f"콘솔에 안 뜨는 전달본 문서 {len(missing)}건: {sorted(missing)[:5]}"


def test_batch_is_grade_balanced():
    """등급별 30건 균형이 깨지면 검수 결과가 한쪽으로 쏠린다."""
    svc = ProxyGoldCandidateService()
    rows = svc.list_candidates(review_batch=BATCH)["candidates"]
    counts: dict[str, int] = {}
    for c in rows:
        counts[c["proposed_grade"]] = counts.get(c["proposed_grade"], 0) + 1
    assert set(counts) == {"TS", "S1", "S2", "S3"}, counts
    assert len(set(counts.values())) == 1, f"등급별 건수가 다르다: {counts}"


def test_unknown_batch_returns_empty_not_everything():
    """오타난 배치명이 전체 목록으로 새면 검수자가 잘못된 집합을 검수한다."""
    svc = ProxyGoldCandidateService()
    assert svc.list_candidates(review_batch="no-such-batch")["total"] == 0
