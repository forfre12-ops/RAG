#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수 사이트(ProxyGoldCandidateService)에 사실 우선 모의문서를 후보로 적재한다 — 1,711건(전달 패키지 1,731건에서
품질 결함 20건을 뺀 것).

[2026-09-25] 검수를 요청하는 것은 1,711건이다. 지재원행 번들(build_offline_bundle.py)은 evidence/review_request_exclusions.jsonl
의 20건을 이미 빼고 싣는다 — 이 스크립트도 같은 목록을 빼야 두 경로로 적재한 콘솔이 똑같다(전에는 이 스크립트만 1,731건을
전부 적재해 번들의 1,711건과 어긋났다).

입력: datasets/expert_review_all_1731_20260924/{documents_all.jsonl, internal_manifest.jsonl}
적재: datasets/proxy_gold/single_document_candidates/{doc_id}_review.md + {doc_id}.metadata.json
  doc_id = review_id(MD-####, 등급 정보 없음) 그대로 재사용. review_batch="expert_review_1731_20260924"로 태그해
  기존 후보(4,082개 파일)와 섞이지 않고 콘솔에서 이 배치만 필터링해 볼 수 있게 한다.
  intended_label = 우리 잠정 등급(곱셈표 계산값) — 콘솔의 '제안 등급' 자리이고, golden_review_blind_enforced 를
  켜면 검수자 화면에서 숨겨진다(끄면 보인다 — 반드시 켠 상태에서 검수를 시작할 것).
사용:  python scripts/load_expert_review_1731_to_console.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from audit_golden_candidate_pool import load_exclusions  # scripts/ 는 스크립트 실행 시 sys.path 에 들어간다

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
SRC = POC / "datasets" / "expert_review_all_1731_20260924"
ROOT = POC / "datasets" / "proxy_gold" / "single_document_candidates"
BATCH = "expert_review_1731_20260924"


def select_ids(docs: dict, excluded: set[str]) -> list[str]:
    """적재할 review_id — 검수 요청에서 뺀 문서는 뺀다(지재원행 번들과 같은 규칙)."""
    return [rid for rid in docs if rid not in excluded]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    docs = {json.loads(x)["review_id"]: json.loads(x)["text"] for x in (SRC / "documents_all.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()}
    man = {json.loads(x)["review_id"]: json.loads(x) for x in (SRC / "internal_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()}
    assert set(docs) == set(man), "documents_all 과 internal_manifest 의 review_id 가 안 맞는다"
    ids = select_ids(docs, load_exclusions())
    print(f"적재 대상 {len(ids)}건(전달 패키지 {len(docs)}건 중 검수 요청에서 뺀 {len(docs) - len(ids)}건 제외) · "
          f"배치={BATCH} · 대상 폴더={ROOT}")

    if a.dry_run:
        print("--dry-run: 파일을 쓰지 않았다.")
        return 0

    ROOT.mkdir(parents=True, exist_ok=True)
    written = 0
    for rid in ids:
        text, m = docs[rid], man[rid]
        (ROOT / f"{rid}_review.md").write_text(text, encoding="utf-8", newline="\n")
        meta = {
            "doc_id": rid,
            "intended_label": m["grade"],
            "document_origin": "synthetic",
            "document_type": f"사실우선 모의문서({m['round']})",
            "authoring_method": "factfirst_docgen_r1_r9",
            "requires_manual_audit": True,
            "candidate_status": "proposed",
            "source_reference": "AI 합성(작성자 다중 모델) — 가이드 12쪽 곱셈표(S×V×M) 계산 잠정 라벨",
            "claim_scope": "검수 전 후보. 전문가 확정 전까지 등급은 잠정값이며 실 비밀정보 아님(전부 가상 내용).",
            "import_note": "S/V/M 사실을 먼저 정해 본문에 녹인 문서. label_basis 는 내부 대장에만 보관(검수자 비공개).",
            "review_batch": BATCH,
            "review_batch_note": "전문가 검수 전달본(2026-09-24, 품질 결함 20건 제외 1,711건). 콘솔에서 review_batch=" + BATCH + " 로 필터링.",
        }
        (ROOT / f"{rid}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        written += 1
    print(f"적재 완료: {written}건 (md+metadata.json 쌍)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
