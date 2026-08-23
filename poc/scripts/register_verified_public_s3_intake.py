"""Register a verified public S3 fixture as a real-document intake candidate.

This is deliberately an intake operation, not a golden promotion.  It records
the source page and the limited internal-use basis alongside the original file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "datasets" / "acceptance_pack" / "docs" / "real-S3-ipo-ksensor.hwpx"
SOURCE_URL = (
    "https://www.kipo.go.kr/ko/kpoBultnDetail.do?aprchId=BUT0000029"
    "&menuCd=SCD0200618&ntatcSeq=20967&sysCd=SCD02"
)
AUTHORIZATION = "지식재산처 공개 보도자료; 내부 평가·검수용 보관, 외부 재배포 금지"


def main() -> int:
    content = SOURCE.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    service = ProxyGoldCandidateService()
    for candidate in service._candidates():
        provenance = candidate.get("provenance") or {}
        if candidate.get("document_origin") == "public_real" and provenance.get("source_reference") == SOURCE_URL:
            print(f"already registered: {candidate['doc_id']}")
            return 0
        if candidate.get("source_file_sha256") == digest:
            print(f"already registered by file hash: {candidate['doc_id']}")
            return 0
    created = service.create_uploaded_candidate(
        filename=SOURCE.name,
        content=content,
        actor_id="system:verified-public-intake",
        document_origin="public_real",
        source_reference=SOURCE_URL,
        authorization_basis=AUTHORIZATION,
    )
    print(json.dumps({k: v for k, v in created.items() if k != "text"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
