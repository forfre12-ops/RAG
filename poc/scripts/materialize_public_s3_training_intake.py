"""Materialize verified public documents into a licence-hold S3 manifest.

The result is intentionally not a golden-evaluation corpus and not a training
corpus.  Public availability alone does not establish AI-training permission.
It retains real-document provenance while waiting for item-level licence proof.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "datasets" / "proxy_gold" / "real_public_s3" / "license_hold.v1.jsonl"
REPORT = ROOT / "datasets" / "proxy_gold" / "real_public_s3" / "README.md"


def main() -> int:
    service = ProxyGoldCandidateService()
    # [B1-4] status 를 안 주면 **폐기(discarded)가 빠진 목록**이 온다(2026-08-17 변경).
    # 빠지는 것이 옳지만 조용히 바뀌면 산출물이 줄어든 이유를 알 수 없다 — 몇 건이
    # 빠졌는지 세어 출력과 README 에 적는다.
    rows = service.list_candidates(origin="public_real")["candidates"]
    dropped = service.list_candidates(origin="public_real", status="discarded")["total"]
    if dropped:
        print(f"[제외] 폐기된 public_real 후보 {dropped}건은 산출물에서 뺐습니다")
    records = []
    for row in sorted(rows, key=lambda item: item["doc_id"]):
        detail = service.get_candidate(row["doc_id"])
        assert detail is not None
        provenance = detail.get("provenance") or {}
        if detail["proposed_grade"] != "S3" or provenance.get("status") != "recorded":
            raise RuntimeError(f"{detail['doc_id']}: not a verified public S3 candidate")
        records.append({
            "schema_version": 1,
            "doc_id": detail["doc_id"],
            "text": detail["text"],
            "label": "S3",
            "label_source": "public_definitive",
            "document_origin": "public_real",
            "source_reference": provenance["source_reference"],
            "authorization_basis": provenance["authorization_basis"],
            "source_file_sha256": detail["source_file_sha256"],
            "extraction": detail["extraction"],
            "split": "license_hold",
            "license_status": "license_hold",
            "training_eligible": False,
            "evaluation_eligible": False,
            "locked_gold_eligible": False,
            "claim_scope": (
                "real public-document provenance only; training blocked until item-level "
                "AI-training permission is recorded; not human-reviewed Locked Gold"
            ),
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    REPORT.write_text(
        "# 공개 실문서 S3 라이선스 보류 Intake\n\n"
        f"- 문서 수: {len(records)}\n"
        f"- 제외(폐기된 후보): {dropped}건\n"
        "- 라벨: 공개 출처에 따른 `public_definitive` S3 제안\n"
        "- 상태: 원문은 공개이나, 항목별 AI 학습 허가 증적이 없어 라이선스 보류\n"
        "- 금지: 학습·임계값 보정·실운영 정확도 주장·Locked Gold 평가·사람 검수 대체\n"
        f"- 매니페스트 SHA-256: `{digest}`\n",
        encoding="utf-8",
    )
    print(json.dumps({"path": str(OUT), "records": len(records),
                      "excluded_discarded": dropped, "sha256": digest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
