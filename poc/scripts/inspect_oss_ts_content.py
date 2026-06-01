"""OSS TS 테스트 샘플 내용 직접 출력 — 라벨 품질 확인용."""
import json
from pathlib import Path

lines = Path("datasets/labeled_oss_v1/test.jsonl").read_text("utf-8").splitlines()
ts_samples = [json.loads(l) for l in lines if json.loads(l).get("label") == "TS"]

print(f"TS 샘플 총 {len(ts_samples)}건\n")
print("=" * 60)

for i, s in enumerate(ts_samples[:15]):
    text = s.get("text", "")
    domain = s.get("domain", "?")
    source = s.get("source", "?")
    print(f"\n[{i+1:02d}] domain={domain}  source={source}")
    print(f"  {text[:250]}")
    print("-" * 60)
