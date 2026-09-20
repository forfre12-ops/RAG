#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""labeled_p1_v5_clean 의 'LLM 이 규칙 S3 를 덮어쓴' 행을 S3 로 정정한 후보 학습셋을 만든다.

왜: 배포본 학습셋(labeled_p1_v5_clean)에는 label_source=llm_judge_primary 행이 전부 rule_grade=S3 인데
    그중 65행(train 56 · val 6 · test 3)이 최종 라벨이 S3 보다 높았다. 2026-09-20 에 65행 전부를 읽었다
    (본문 앞 700자 + 끝 150자, 전문 비공개 표지어 스캔 0건): 전부 증권사 시황·산업 가격 동향·경제지표·
    IR 후기·공개 조사 요약이다. holdout109 에서 정정한 22건과 같은 유형이다.
    → 메모리 train-set-llm-overrode-rule-s3-56-candidates-2026-09-20.md

원본은 건드리지 않는다(배포본 v-fe4b386b 재현용). 정정본은 새 폴더에 쓰고 원 라벨과 근거를 행마다 남긴다.
⚠ 정정 주체는 사람이 아니라 AI 정독이다 — 사람 서명이 아니므로 review_status 는 바꾸지 않는다.

사용:  python scripts/build_v5_clean_s3fix.py            (poc 에서)
"""
from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
SRC = POC / "datasets" / "labeled_p1_v5_clean"
DST = POC / "datasets" / "labeled_p1_v5_clean_s3fix"
EXPECT = {"train": 56, "val": 6, "test": 3}   # 2026-09-20 정독 확인 건수 — 어긋나면 멈춘다
REASON = ("llm_judge_primary 가 규칙 판정 S3 를 덮어씀 — 본문이 증권사 시황·산업 통계·IR 후기·공개 조사 요약이라 "
          "비공지성 실패로 S3 (holdout109 정정 22건과 같은 유형). AI 정독 2026-09-20, 사람 서명 아님")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def is_candidate(r: dict) -> bool:
    return (r.get("label_source") == "llm_judge_primary" and r.get("rule_grade") == "S3"
            and r.get("label") != "S3")


def main() -> int:
    DST.mkdir(parents=True, exist_ok=True)
    manifest = {"source_dir": "datasets/labeled_p1_v5_clean", "status": "CANDIDATE — 미학습·미배포",
                "rule": "label_source=llm_judge_primary ∧ rule_grade=S3 ∧ label≠S3 → label=S3",
                "reason": REASON, "splits": {}}
    for sp, want in EXPECT.items():
        src = SRC / f"{sp}.jsonl"
        rows = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]
        before = collections.Counter(r["label"] for r in rows)
        changed = []
        for r in rows:
            if is_candidate(r):
                r["label_before_correction_2026_09_20"] = r["label"]
                r["label_correction_reason"] = REASON
                changed.append({"doc_id": r["doc_id"], "from": r["label"]})
                r["label"] = "S3"
        if len(changed) != want:
            print(f"[중단] {sp}: 정정 대상 {len(changed)}행 ≠ 확인한 {want}행 — 원본이 바뀌었거나 조건이 어긋남")
            return 1
        out = DST / f"{sp}.jsonl"
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        after = collections.Counter(r["label"] for r in rows)
        manifest["splits"][sp] = {
            "rows": len(rows), "changed": len(changed),
            "changed_from": dict(collections.Counter(c["from"] for c in changed)),
            "label_before": dict(before), "label_after": dict(after),
            "source_sha256": sha256(src), "out_sha256": sha256(out),
            "changed_doc_ids": [c["doc_id"] for c in changed],
        }
        print(f"{sp}: {len(rows)}행 · 정정 {len(changed)}행 {dict(collections.Counter(c['from'] for c in changed))}"
              f" · 라벨 {dict(before)} → {dict(after)}")
    (DST / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
