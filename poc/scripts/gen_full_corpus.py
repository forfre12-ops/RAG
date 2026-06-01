"""864건 전체 데이터 생성 — RAG 코퍼스(720) + 테스트/데모 세트(144).

구조: 6 도메인 × 4 등급 × 36건 (30 RAG + 6 테스트)
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
from lloydk.modules.m3_labeling.pipeline import LabelingPipeline

DOMAINS = ["tech", "business", "finance", "hr", "legal", "security"]
GRADES  = ["TS", "S1", "S2", "S3"]
PER_COMBO_RAG  = 30
PER_COMBO_TEST = 6
LENGTH_BANDS = [(400,800),(600,1200),(800,1600),(500,1000),(700,1400),(600,1100),(900,1800),(400,700)]

RAG_DIR  = Path("datasets/rag_corpus_v2")
TEST_DIR = Path("datasets/test_set_v2")
RAG_DIR.mkdir(parents=True, exist_ok=True)
TEST_DIR.mkdir(parents=True, exist_ok=True)

gen     = SyntheticDocGenerator()
labeler = LabelingPipeline()

total_gen = total_match = total_fail = 0
start_all = time.perf_counter()

for domain in DOMAINS:
    for grade in GRADES:
        combo_total = PER_COMBO_RAG + PER_COMBO_TEST
        print(f"\n[{domain}/{grade}] {combo_total}건 생성 중...")
        for i in range(combo_total):
            band = LENGTH_BANDS[i % len(LENGTH_BANDS)]
            req  = SynthRequest(target_grade=grade, domain=domain, count=1, len_min=band[0], len_max=band[1])
            try:
                doc  = gen.generate_one(req)
                body = doc.body or ""
                lab  = labeler.label(body) if body else None
                pred = lab.grade.value if lab and hasattr(lab.grade, "value") else str(lab.grade) if lab else "?"
                is_match = pred == grade

                record = {
                    "target_grade": grade,
                    "domain": domain,
                    "title": doc.title,
                    "body": body,
                    "document_type": doc.document_type,
                    "predicted_grade": pred,
                    "label_match": is_match,
                }

                out_dir = TEST_DIR if i >= PER_COMBO_RAG else RAG_DIR
                fname   = out_dir / f"{domain}_{grade}_{i:03d}.json"
                fname.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

                total_gen += 1
                if is_match:
                    total_match += 1
                tag = "TEST" if i >= PER_COMBO_RAG else "RAG "
                print(f"  [{tag} {i+1:02d}/{combo_total}] pred={pred} match={is_match} len={len(body)}")
            except Exception as e:  # noqa: BLE001
                total_fail += 1
                print(f"  [FAIL {i+1:02d}] {e}")

elapsed = time.perf_counter() - start_all
print(f"\n{'='*50}")
print(f"생성 완료: {total_gen}건 / 실패: {total_fail}건")
print(f"라벨 일치: {total_match}/{total_gen} = {total_match/max(total_gen,1)*100:.1f}%")
print(f"소요시간: {elapsed/60:.1f}분")
print(f"RAG 코퍼스: {RAG_DIR}")
print(f"테스트 세트: {TEST_DIR}")
