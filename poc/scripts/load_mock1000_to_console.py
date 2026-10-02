#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수 사이트(ProxyGoldCandidateService)에 guide40 구조 확장 모의문서 1,001건을 후보로 적재한다.

load_expert_review_1731_to_console.py 와 같은 방식(파일 기반 후보 폴더에 {doc_id}_review.md +
{doc_id}.metadata.json 쌍을 쓴다) — review_batch 태그만 달리해 기존 1,731건 배치와 섞이지 않는다.

입력: datasets/expert_review_mock1000_20261002/{reviewer_documents.jsonl, internal_manifest.jsonl}
적재: datasets/proxy_gold/single_document_candidates/{doc_id}_review.md + {doc_id}.metadata.json
  doc_id = review_id(MK-####) 그대로 재사용. review_batch="expert_review_mock1000_20261002" 로 태그.
  intended_label = true_grade(가이드 카탈로그 직접 배정 — 사람 검수 전 잠정값). golden_review_blind_enforced
  를 켜면 검수자 화면에서 숨겨진다(끄면 보인다 — 반드시 켠 상태에서 검수를 시작할 것).

이 배치는 README_읽어주십시오.md 가 명시한 대로 "실문서 정확도의 근거"가 아니라
회귀·규칙준수 시험용 + 지재원 검수를 통한 라벨 신뢰도 확보용이다. 제외 목록 없음(1,001건 전수,
품질 게이트 전수 통과 — README 참조).

사용: python scripts/load_mock1000_to_console.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

POC = Path(__file__).resolve().parents[1]
SRC = POC / "datasets" / "expert_review_mock1000_20261002"
ROOT = POC / "datasets" / "proxy_gold" / "single_document_candidates"
BATCH = "expert_review_mock1000_20261002"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    docs = {
        json.loads(x)["review_id"]: json.loads(x)["text"]
        for x in (SRC / "reviewer_documents.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()
    }
    man = {
        json.loads(x)["review_id"]: json.loads(x)
        for x in (SRC / "internal_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()
    }
    missing = set(docs) ^ set(man)
    assert not missing, f"reviewer_documents 과 internal_manifest 의 review_id 가 안 맞는다: {sorted(missing)[:5]}"

    ids = sorted(docs, key=lambda rid: int(rid.split("-")[1]))
    print(f"적재 대상 {len(ids)}건(제외 목록 없음 — README 전수 통과 확인본) · 배치={BATCH} · 대상 폴더={ROOT}")

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
            "intended_label": m["true_grade"],
            "document_origin": "synthetic",
            "document_type": f"모의문서({m['department']}/{m['info_type']}, {m['doc_format']})",
            "authoring_method": "mock1000_guide40_structure_20261002",
            "requires_manual_audit": True,
            "candidate_status": "proposed",
            "source_reference": "AI 합성(서브에이전트 64명) — guide40 카탈로그(부서·정보유형→등급) 직접 배정",
            "claim_scope": "검수 전 후보. 전문가 확정 전까지 등급은 잠정값이며 실 비밀정보 아님(전부 가상 내용).",
            "import_note": "등급명을 작성자에게 알리지 않고 부서·정보유형·심각도 설명만으로 작성시킨 문서"
                           "(guide40 64건의 구조를 1,001건으로 확장). 상세는 README_읽어주십시오.md 참조.",
            "review_batch": BATCH,
            "review_batch_note": f"guide40 구조 확장 모의문서(2026-10-02, 1,001건, 제외 0건)."
                                  f" 콘솔에서 review_batch={BATCH} 로 필터링.",
        }
        (ROOT / f"{rid}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        written += 1
    print(f"적재 완료: {written}건 (md+metadata.json 쌍)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
