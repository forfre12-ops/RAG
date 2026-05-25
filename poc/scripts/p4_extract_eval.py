"""
P4 PoC — 문서 추출 품질 평가.
입력 디렉토리의 .pdf/.docx/.txt를 추출하여 길이·노이즈 비율을 리포트.

사용:
  python scripts/p4_extract_eval.py --in datasets/raw/aihub_admin_docs --out reports/p4.json
"""
import argparse
import json
from pathlib import Path

from lloydk.modules.m2_preprocess.pipeline import (
    PreprocessPipeline, extract_file, normalize,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="indir", required=True)
    ap.add_argument("--out", default="reports/p4.json")
    args = ap.parse_args()

    pipe = PreprocessPipeline()
    rows = []
    indir = Path(args.indir)
    for p in indir.rglob("*"):
        if p.suffix.lower() not in {".pdf", ".docx", ".txt", ".md"}:
            continue
        try:
            raw = extract_file(p)
            clean = normalize(raw)
            rows.append({
                "file": str(p.relative_to(indir)),
                "raw_len": len(raw),
                "clean_len": len(clean),
                "noise_ratio": round(1 - len(clean) / max(len(raw), 1), 4),
                "n_chunks": len(pipe.chunk(clean)),
                "ok": True,
            })
        except Exception as e:
            rows.append({"file": str(p.relative_to(indir)), "ok": False, "error": str(e)})

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for r in rows if r.get("ok"))
    print(f"total={len(rows)}, ok={ok}, fail={len(rows) - ok}")
    print(f"report: {args.out}")


if __name__ == "__main__":
    main()
