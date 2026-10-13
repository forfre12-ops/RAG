# -*- coding: utf-8 -*-
"""출처 증거 기반 S3 정답 — 증거 검사와 **한계 기록**을 함께 잠근다.

왜(2026-09-14). 정답 계층이 전부 막혀 있는데(GOLD 0건 · 채점면이 BRONZE/NONE)
한쪽 축은 사람 없이 확정된다 — 공개됐다는 사실은 URL·기관·수집시각·해시로 검증 가능하고,
비공지성 결여는 법령 정의에서 바로 따라온다.

다만 그렇게 모은 1,650건이 **출처 호스트 1종**(www.korea.kr)이다. 이 면은
'공개문서' 가 아니라 그 매체의 문체를 잰다. 그 한계를 명세에 적지 않으면
다음 사람이 '공개문서 과탐 N%' 로 인용한다 — 그래서 시험이 한계 기록을 강제한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "scripts"))

from build_public_s3_truth import LABEL_SOURCE, check_evidence  # noqa: E402

_OK = {
    "source_reference": "https://www.korea.kr/briefing/pressReleaseView.do?newsId=1",
    "source_agency": "소방청",
    "retrieved_at": "2026-08-07T19:31:57+00:00",
    "source_sha256": "e" * 64,
}


def _row(**kw):
    r = dict(_OK)
    r.update(kw)
    return r


# ── 증거 검사 — 있다고 다 되는 것이 아니다 ──────────────────────────────

def test_full_evidence_passes() -> None:
    ev, why = check_evidence(_row())
    assert ev is not None and why == ""
    assert ev["source_host"] == "www.korea.kr"


@pytest.mark.parametrize("kw,expect", [
    ({"source_reference": ""}, "URL 없음"),
    ({"source_reference": "korea.kr/x"}, "URL 형식 불량"),       # 스킴 없음
    ({"source_reference": "ftp://x/y"}, "URL 형식 불량"),        # http(s) 아님
    ({"source_agency": "   "}, "발행기관 없음"),
    ({"retrieved_at": ""}, "수집시각 없음"),
    ({"retrieved_at": "어제"}, "수집시각 파싱 실패"),
    ({"source_sha256": ""}, "원문해시 없음"),
    ({"source_sha256": "deadbeef"}, "원문해시 형식 불량(64자 16진수 아님)"),
    ({"source_sha256": "z" * 64}, "원문해시 형식 불량(64자 16진수 아님)"),
])
def test_bad_evidence_is_rejected_with_reason(kw, expect) -> None:
    """버릴 때 **사유를 남긴다** — 분모를 못 적으면 손으로 고른 목록과 다르지 않다."""
    ev, why = check_evidence(_row(**kw))
    assert ev is None
    assert why == expect


def test_raw_html_sha256_is_accepted_as_digest() -> None:
    r = _row()
    del r["source_sha256"]
    r["raw_html_sha256"] = "a" * 64
    ev, why = check_evidence(r)
    assert ev is not None and ev["source_sha256"] == "a" * 64


# ── 산출물 ───────────────────────────────────────────────────────────────

def _load():
    p = POC / "datasets/public_s3_truth/eval.jsonl"
    if not p.exists():
        pytest.skip("정답셋 없음 — scripts/build_public_s3_truth.py --out 로 생성")
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_every_row_is_s3_with_provenance_source() -> None:
    rows = _load()
    assert rows
    for r in rows:
        assert r["label"] == "S3"
        assert r["label_source"] == LABEL_SOURCE
        assert r["provenance"]["source_sha256"]
        assert r["lineage"]["from"], "어느 파일에서 왔는지 남아야 한다"


def test_rows_are_silver_not_none() -> None:
    """NONE(출처 기록 없음) → SILVER(출처가 공개임이 확정) 로 올라갔는지."""
    sys.path.insert(0, str(POC / "scripts"))
    from audit_eval_ground_truth import tier_of
    rows = _load()
    tiers = {tier_of(r)[0] for r in rows}
    assert tiers == {"SILVER"}, f"정답등급이 {tiers} 다"


def test_no_duplicate_text() -> None:
    rows = _load()
    hashes = [r["text_sha256"] for r in rows]
    assert len(hashes) == len(set(hashes)), "본문 중복이 남았다"


def test_manifest_records_the_homogeneity_limit() -> None:
    """⭐ 한계를 안 적으면 다음 사람이 '공개문서 과탐 N%' 로 인용한다."""
    p = POC / "datasets/public_s3_truth/manifest.json"
    if not p.exists():
        pytest.skip("manifest 없음")
    m = json.loads(p.read_text(encoding="utf-8"))
    assert m.get("known_limitations"), "한계 기록이 비었다"
    joined = " ".join(m["known_limitations"])
    assert "대표성" in joined, "대표성 주장 금지가 안 적혀 있다"
    assert m.get("source_host_count") is not None
    if m["source_host_count"] <= 1:
        assert "1종" in joined or "매체" in joined, (
            "호스트가 1종인데 그 사실이 한계에 안 적혀 있다"
        )


def test_manifest_states_claim_ceiling() -> None:
    p = POC / "datasets/public_s3_truth/manifest.json"
    if not p.exists():
        pytest.skip("manifest 없음")
    m = json.loads(p.read_text(encoding="utf-8"))
    assert "SILVER" in str(m.get("claim_ceiling") or "")
    assert m.get("dropped"), "버린 사유(분모)가 안 적혀 있다"
