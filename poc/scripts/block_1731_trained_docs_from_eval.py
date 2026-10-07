#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""1,731건 검수 배치 중 '이미 학습에 쓴 문서'를 평가정답 편입 차단 목록에 올린다.

이전 시도(`exclude_trained_docs_before_promotion.py`)는 검수자가 확정한 **뒤에** CLI로
reopen 해야 했다 — 관리자 콘솔은 버튼 클릭으로 다 되게 만들어 놨는데 이것만 터미널이
필요해 어긋났다. 이 스크립트는 그 대신 **이미 있는 정식 메커니즘**을 쓴다:

  `evidence/eval_independence_exclusions.jsonl` — console_signoff.build_promotion_inputs()가
  승격(promote) 단계에서 자동으로 읽어 거르는 목록(2026-09-09 도입, scripts/audit_candidate_
  independence.py --block-eval 이 쓰는 것과 같은 파일·같은 스키마). doc_id 만 있으면 되고,
  **검수는 그대로 유효**하며(콘솔에서 계속 보이고 결정도 남는다) 오직 "평가정답으로 승격"
  단계에서만 조용히 빠진다 — 관리자는 버튼을 그대로 누르면 된다, 아무것도 더 안 해도 된다.

  즉 이 스크립트는 **한 번만** 돌리면 끝이다(검수 시작 전이든 도중이든 아무 때나) — 그 뒤로는
  1,177건이 언제 확정되든, 누가 승격 버튼을 누르든 자동으로 걸러진다.

사용:
    python scripts/block_1731_trained_docs_from_eval.py --dry-run
    python scripts/block_1731_trained_docs_from_eval.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

POC = Path(__file__).resolve().parents[1]
MANIFEST = POC / "datasets" / "expert_review_all_1731_20260924" / "internal_manifest.jsonl"
EXCLUSIONS = POC / "evidence" / "eval_independence_exclusions.jsonl"
BLOCK_USES = {"fs_train_loss", "fs_val_holdout"}
TOOL = "block_1731_trained_docs_from_eval.py"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = [json.loads(x) for x in MANIFEST.read_text(encoding="utf-8").splitlines() if x.strip()]
    to_block = sorted({r["review_id"] for r in rows if r["training_use"] in BLOCK_USES})
    print(f"검수 배치 총 {len(rows)}건 중 평가정답 편입 차단 대상(이미 학습에 씀): {len(to_block)}건")

    already = set()
    if EXCLUSIONS.exists():
        for line in EXCLUSIONS.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                already.add(json.loads(line).get("doc_id"))
            except json.JSONDecodeError:
                continue
    new_ids = [d for d in to_block if d not in already]
    print(f"이미 목록에 있는 것: {len(to_block) - len(new_ids)}건 · 새로 추가할 것: {len(new_ids)}건")

    if not new_ids:
        print("→ 추가할 것이 없다(이미 다 등록됨).")
        return 0
    if a.dry_run:
        print("--dry-run: 아무것도 쓰지 않았다. 추가될 doc_id 예시:", new_ids[:10])
        return 0

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    EXCLUSIONS.parent.mkdir(parents=True, exist_ok=True)
    with EXCLUSIONS.open("a", encoding="utf-8", newline="\n") as fh:
        for doc_id in new_ids:
            fh.write(json.dumps({
                "at": now,
                "doc_id": doc_id,
                "reason": "이미 FS7/FS9 학습 손실에 사용됨(train-on-test 방지) — 사실우선 모의문서 1,731건 검수배치",
                "tool": TOOL,
            }, ensure_ascii=False) + "\n")
    print(f"기록 완료: {len(new_ids)}건 추가 → {EXCLUSIONS}")
    print("이제부터 이 문서들은 검수는 그대로 되고, 「평가정답으로 승격」 버튼만 눌러도 자동으로 빠진다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
