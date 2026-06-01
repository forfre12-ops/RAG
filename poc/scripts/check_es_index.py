"""ES 인덱스 내용 + 합성 데이터 현황 확인."""
import json
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(".env")

from elasticsearch import Elasticsearch

es = Elasticsearch("http://localhost:9200")

print("=== ES 인덱스 현황 ===")
indices = es.cat.indices(format="json")
for idx in sorted(indices, key=lambda x: x["index"]):
    if not idx["index"].startswith("."):
        print(f"  {idx['index']}: {idx['docs.count']}건  ({idx['store.size']})")

print()

# 샘플 도큐먼트 1건 확인
print("=== ES 'docs' 인덱스 샘플 1건 ===")
try:
    res = es.search(index="docs", body={"size": 1, "query": {"match_all": {}}})
    hits = res["hits"]["hits"]
    if hits:
        src = hits[0]["_source"]
        print(f"  fields: {list(src.keys())}")
        print(f"  domain: {src.get('domain','?')}")
        print(f"  grade:  {src.get('grade','?')}")
        print(f"  text preview: {str(src.get('text',''))[:150]}")
except Exception as e:
    print(f"  오류: {e}")

print()
print("=== 합성 데이터 파일 현황 ===")
for dname in ["synthetic_qwen3_800", "synthetic_ts_augment", "synthetic_qwen3", "labeled_5k"]:
    p = Path(f"datasets/{dname}")
    if p.exists():
        files = list(p.glob("*"))
        total = len(files)
        # 샘플 1개 열어서 형식 확인
        sample = None
        for f in files[:1]:
            try:
                sample = json.loads(f.read_text("utf-8"))
            except Exception:
                pass
        fields = list(sample.keys()) if sample else []
        print(f"  {dname}: {total}건  fields={fields}")
    else:
        print(f"  {dname}: 없음")
