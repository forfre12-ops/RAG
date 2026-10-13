"""Register public notices and rules to diversify the real S3 intake."""
from __future__ import annotations

import json

import requests

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService


AUTHORIZATION = "지식재산처 공개 고시·공고 원문; 내부 평가·검수용 보관, 외부 재배포 금지"
RECORDS = (
    (19666, "BUT0000047", "SCD0200639", "특허·실용신안 우선심사 신청 고시"),
    (19726, "BUT0000047", "SCD0200639", "부정경쟁행위 조사자료 열람·복사 규정"),
    (19727, "BUT0000047", "SCD0200639", "부정경쟁행위 방지 업무처리규정"),
    (20055, "BUT0000021", "SCD0200610", "창업지원 초고속심사 운영규모 공고"),
)


def article_url(sequence: int, approach: str, menu: str) -> str:
    return (
        "https://www.kipo.go.kr/ko/kpoBultnDetail.do?"
        f"aprchId={approach}&menuCd={menu}&ntatcSeq={sequence}&sysCd=SCD02"
    )


def download_url(sequence: int, approach: str) -> str:
    return (
        "https://www.kipo.go.kr/ko/kpoBultnFileDown.do?"
        f"ntatcSeq={sequence}&ntatcAtflSeq=1&sysCd=SCD02&aprchId={approach}"
    )


def main() -> int:
    service = ProxyGoldCandidateService()
    existing = {
        (row.get("provenance") or {}).get("source_reference")
        for row in service._candidates()
        if row.get("document_origin") == "public_real"
    }
    created = []
    for sequence, approach, menu, title in RECORDS:
        source = article_url(sequence, approach, menu)
        if source in existing:
            continue
        response = requests.get(download_url(sequence, approach), timeout=45)
        response.raise_for_status()
        if not response.content.startswith(b"PK"):
            raise RuntimeError(f"{sequence}: expected HWPX attachment")
        row = service.create_uploaded_candidate(
            filename=f"KIPO-{sequence}.hwpx",
            content=response.content,
            actor_id="system:verified-public-intake",
            document_origin="public_real",
            source_reference=source,
            authorization_basis=AUTHORIZATION,
            title=title,
        )
        if row.get("extraction", {}).get("quality", 0) < 0.9:
            raise RuntimeError(f"{sequence}: extraction quality below 0.9")
        created.append({"doc_id": row["doc_id"], "title": title, "characters": row["characters"]})
    print(json.dumps({"created": created}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
