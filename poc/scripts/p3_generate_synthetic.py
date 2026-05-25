"""
P3 PoC — LLM 기반 합성 학습 데이터 생성.

사용:
  python scripts/p3_generate_synthetic.py \
    --grade TS --count 50 --domain tech --out datasets/synthetic/TS
"""
import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

from lloydk.schemas.common import Grade
from lloydk.modules.m1_synthesis.generator import (
    SyntheticDocGenerator, SynthRequest,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grade", required=True, choices=[g.value for g in Grade])
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--domain", default="mixed")
    ap.add_argument("--guide-dir", default=None,
                    help="가이드 텍스트(.txt) 디렉토리. 생략 시 가이드 없이 생성.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    guide_chunks: list[str] = []
    if args.guide_dir:
        for p in Path(args.guide_dir).glob("*.txt"):
            guide_chunks.append(p.read_text(encoding="utf-8"))

    gen = SyntheticDocGenerator()
    grade = Grade(args.grade)
    fails = 0

    for i in range(args.count):
        req = SynthRequest(target_grade=grade, domain=args.domain,
                           count=1, guide_chunks=guide_chunks)
        try:
            doc = gen.generate_one(req)
        except Exception as e:
            fails += 1
            print(f"[{i+1}/{args.count}] FAILED: {e}")
            continue
        rec = {
            "synth_id": str(uuid4()),
            "target_grade": grade.value,
            "domain": args.domain,
            "title": doc.title,
            "body": doc.body,
            "document_type": doc.document_type,
            "dept_hint": doc.dept_hint,
            "rationale_tags": doc.rationale_tags,
            "llm_provider": doc.llm_provider,
            "generated_at": int(time.time()),
            "status": "pending",
        }
        f = out_dir / f"{rec['synth_id']}.json"
        f.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{i+1}/{args.count}] saved: {f.name}  (chars={len(doc.body)})")

    print(f"\nDone. ok={args.count - fails}, fail={fails}")


if __name__ == "__main__":
    main()
