"""Ingest a small, source-verified public S3 batch from KIPO attachments.

The source pages and original attachments remain traceable.  This script creates
review candidates only; no row is promoted to Locked Gold.
"""
from __future__ import annotations

import json

import requests

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService


BASE = "https://www.kipo.go.kr/ko/kpoBultnDetail.do?aprchId=BUT0000029&menuCd=SCD0200618"
DOWNLOAD = "https://www.kipo.go.kr/ko/kpoBultnFileDown.do?ntatcAtflSeq=1&sysCd=SCD02&aprchId=BUT0000029"
AUTHORIZATION = "지식재산처 공개 보도자료 원문; 내부 평가·검수용 보관, 외부 재배포 금지"
RECORDS = (
    (20910, "인공지능으로 특허·기술정보 쉽게 분석한다"),
    (20932, "인공지능 기반 스마트제조 미래기술 발굴 협력"),
    (20762, "2026년 특허심사 처리계획"),
    (20961, "첨단기술 유출 대응 전문수사조직 가동"),
    (20969, "특허 빅데이터 기반 산업혁신 지원사업"),
    (20927, "인공지능 활용 발명 특허출원 안내"),
    (20888, "한-WIPO AI·IP 교육과정"),
)


def article_url(sequence: int) -> str:
    return f"{BASE}&ntatcSeq={sequence}&sysCd=SCD02"


def attachment_url(sequence: int) -> str:
    return f"{DOWNLOAD}&ntatcSeq={sequence}"


def main() -> int:
    service = ProxyGoldCandidateService()
    existing = {
        (row.get("provenance") or {}).get("source_reference")
        for row in service._candidates()
        if row.get("document_origin") == "public_real"
    }
    created: list[dict] = []
    skipped: list[int] = []
    for sequence, title in RECORDS:
        source = article_url(sequence)
        if source in existing:
            skipped.append(sequence)
            continue
        response = requests.get(attachment_url(sequence), timeout=45)
        response.raise_for_status()
        content = response.content
        if not content.startswith(b"PK"):
            raise RuntimeError(f"{sequence}: expected HWPX attachment")
        row = service.create_uploaded_candidate(
            filename=f"KIPO-{sequence}.hwpx",
            content=content,
            actor_id="system:verified-public-intake",
            document_origin="public_real",
            source_reference=source,
            authorization_basis=AUTHORIZATION,
            title=title,
        )
        if row.get("extraction", {}).get("quality", 0) < 0.9:
            raise RuntimeError(f"{sequence}: extraction quality below 0.9")
        created.append({
            "doc_id": row["doc_id"], "title": row["title"],
            "characters": row["characters"], "source": source,
        })
    print(json.dumps({"created": created, "skipped": skipped}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
