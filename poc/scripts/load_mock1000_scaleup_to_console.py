#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수 사이트(ProxyGoldCandidateService)에 KL장점+우리규칙 결합 1,001건(mock1000_scaleup_20261007)을
후보로 적재한다 — 기존 mock1000(expert_review_mock1000_20261002, MK-####)을 대체한다.

load_mock1000_to_console.py 와 같은 방식(파일 기반 후보 폴더에 {doc_id}_review.md +
{doc_id}.metadata.json 쌍을 쓴다). doc_id 접두가 달라(MS1-#### vs MK-####) 같은 폴더에
공존할 수 있지만, 이번 지시는 "옛 모의문서는 남으면 안 된다"이므로 --replace 플래그로
기존 MK-#### 쌍을 지우고 MS1-#### 로만 채운다.

입력: datasets/mock1000_scaleup_20261007/{scaleup_manifest.jsonl, documents/*.txt}
적재: datasets/proxy_gold/single_document_candidates/{doc_id}_review.md + {doc_id}.metadata.json

사용: python scripts/load_mock1000_scaleup_to_console.py --replace [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

POC = Path(__file__).resolve().parents[1]
SRC = POC / "datasets" / "mock1000_scaleup_20261007"
ROOT = POC / "datasets" / "proxy_gold" / "single_document_candidates"
BATCH = "mock1000_scaleup_20261007"
OLD_BATCH_PREFIX = "MK-"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replace", action="store_true", help="기존 MK-#### 후보 쌍을 지우고 MS1-####로만 채운다")
    a = ap.parse_args()

    man = [json.loads(x) for x in (SRC / "scaleup_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    print(f"적재 대상 {len(man)}건 · 배치={BATCH} · 대상 폴더={ROOT}")

    old_pairs = sorted(ROOT.glob(f"{OLD_BATCH_PREFIX}*")) if ROOT.exists() else []
    print(f"기존 {OLD_BATCH_PREFIX}* 파일 {len(old_pairs)}개 발견" + (" — --replace 로 삭제 예정" if a.replace else " — --replace 없어 그대로 둠"))

    if a.dry_run:
        print("--dry-run: 파일을 쓰거나 지우지 않았다.")
        return 0

    ROOT.mkdir(parents=True, exist_ok=True)

    if a.replace:
        for p in old_pairs:
            p.unlink()
        print(f"삭제 완료: {OLD_BATCH_PREFIX}* {len(old_pairs)}개")

    written = 0
    for m in man:
        rid = m["doc_id"]
        text = (SRC / "documents" / f"{rid}.txt").read_text(encoding="utf-8")
        (ROOT / f"{rid}_review.md").write_text(text, encoding="utf-8", newline="\n")
        meta = {
            "doc_id": rid,
            "intended_label": m["true_grade"],
            "document_origin": "synthetic",
            # [2026-10-10] title을 안 채우면 콘솔이 document_type("모의문서(...)")을 제목 대신
            # 보여준다(proxy_gold_candidate_service.py의 title-or-document_type 폴백) — 모든 행이
            # 똑같은 문구로 반복돼 보인다는 지적. 본문 첫 줄은 이미 자연스러운 제목이라 그대로 쓴다.
            "title": text.split("\n", 1)[0].strip(),
            "document_type": f"모의문서({m['department']}/{m['info_type']}, {m['doc_format']})",
            "department": m["department"],
            "info_type": m["info_type"],
            "doc_format": m["doc_format"],
            "authoring_method": "claude_subagent_playbook_scaleup_20261007",
            "requires_manual_audit": True,
            "candidate_status": "proposed",
            "source_reference": "AI 합성(Claude 서브에이전트 41개 작업) — guide40 카탈로그(부서·정보유형→등급)"
                                 " 직접 배정 + 외부 생성기(DataGen Studio) 대비 규칙위반(반복금지어·실명호칭·"
                                 "보고서구조 편중) 전수 해소",
            "claim_scope": "검수 전 후보. 전문가 확정 전까지 등급은 잠정값이며 실 비밀정보 아님(전부 가상 내용)."
                           " 통계적 지름길 검사(TF-IDF 지름길기준선이 모델보다 11.4%p 앞섬, 2026-10-08) 미해소"
                           " — 회귀·규칙준수 시험용으로만 쓸 것.",
            "import_note": "등급명을 작성자에게 알리지 않고 부서·정보유형·민감도 설명만으로 작성시킨 문서"
                           "(mock1000과 동일 부서 비중 1,001건). 상세는 README_읽어주십시오.md 참조.",
            "review_batch": BATCH,
            "review_batch_note": f"KL장점(구체적 상황설정)+우리규칙(반복금지어·실명금지 등) 결합 모의문서"
                                  f"(2026-10-07~08, 1,001건, 제외 0건). 콘솔에서 review_batch={BATCH} 로 필터링.",
        }
        (ROOT / f"{rid}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        written += 1
    print(f"적재 완료: {written}건 (md+metadata.json 쌍)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
