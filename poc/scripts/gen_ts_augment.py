"""RAG Q&A 코퍼스 보강 — 질문 형식 문서 생성.

/answer 검색 0건 문제 해결:
  - 현재 코퍼스: 문서 서술형 (질문-응답 매칭 어려움)
  - 추가: Q&A 형식 문서 (질문 → 관련 내용 직접 답변)

6 도메인 × 4 등급 × 5건 = 120건 Q&A 문서 생성
"""
import json
import os
import sys
import time
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(".env")

from lloydk.modules.m1_synthesis.generator import SyntheticDocGenerator, SynthRequest

QA_DIR = Path("datasets/rag_corpus_qa")
QA_DIR.mkdir(parents=True, exist_ok=True)

# Q&A 형식 도메인 매핑
QA_DOMAINS = {
    "tech":       ("연구노트 FAQ, 기술 문의응답서, 설계 검토 Q&A", 600, 1200),
    "business":   ("M&A 실사 Q&A, 사업전략 질의응답, 시장분석 FAQ", 500, 1000),
    "finance":    ("재무 Q&A, 예산 관련 질의응답, 원가분석 FAQ", 400, 900),
    "hr":         ("인사 Q&A, 평가 기준 FAQ, 보상 제도 질의응답", 400, 800),
    "legal":      ("계약 Q&A, 법무 FAQ, NDA 관련 질의응답", 500, 1000),
    "security":   ("보안 Q&A, 암호화 FAQ, 취약점 대응 질의응답", 500, 1000),
}

GRADES = ["TS", "S1", "S2", "S3"]
PER_COMBO = 5

gen = SyntheticDocGenerator()
total = match = fail = 0
start = time.perf_counter()

for domain, (doc_types, len_min, len_max) in QA_DOMAINS.items():
    for grade in GRADES:
        for i in range(PER_COMBO):
            req = SynthRequest(
                target_grade=grade,
                domain=domain,
                count=1,
                len_min=len_min,
                len_max=len_max,
            )
            try:
                doc = gen.generate_one(req)
                body = doc.body or ""
                fname = QA_DIR / f"{domain}_{grade}_qa_{i:02d}.json"
                fname.write_text(json.dumps({
                    "target_grade": grade,
                    "domain": domain,
                    "title": doc.title,
                    "body": body,
                    "document_type": doc.document_type,
                    "source": "qa_augment",
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                total += 1
                if total % 20 == 0:
                    elapsed = time.perf_counter() - start
                    print(f"  {total}/120건 ({elapsed:.0f}s)...")
            except Exception as e:  # noqa: BLE001
                fail += 1
                print(f"  FAIL {domain}/{grade}/{i}: {e}")

elapsed = time.perf_counter() - start
print(f"\n완료: {total}건 생성 / {fail}건 실패 ({elapsed/60:.1f}분)")
print(f"저장: {QA_DIR}")
