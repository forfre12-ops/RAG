"""기존 학습셋에 실문서 S3 를 병합한다 — **봉인 유출은 fail-closed 로 막는다.**

봉인 400건이 학습셋에 한 건이라도 들어가면 그 뒤의 모든 측정이 무의미해진다. 그래서
병합 결과를 봉인셋의 doc_id·본문해시와 대조하고, 하나라도 걸리면 파일을 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        import io as _io
        setattr(sys, _s, _io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))


def rows(p: str | Path) -> list[dict]:
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def text_sha(r: dict) -> str:
    return hashlib.sha256((r.get("text") or "").encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="실문서 S3 병합(봉인 유출 fail-closed)")
    ap.add_argument("--base-dir", default="datasets/labeled_p1_v5_clean")
    ap.add_argument("--add", default="datasets/real_s3_forms/train_s3.jsonl")
    ap.add_argument("--sealed", default="datasets/real_s3_forms/sealed_s3.jsonl")
    ap.add_argument("--out-dir", default="datasets/labeled_p1_v5_realS3")
    args = ap.parse_args(argv)

    base = Path(args.base_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    add = rows(args.add)
    sealed = rows(args.sealed)
    sealed_ids = {r.get("doc_id") for r in sealed}
    sealed_sha = {r.get("text_sha256") or text_sha(r) for r in sealed}
    print(f"추가 {len(add)}건 · 봉인 {len(sealed)}건(대조용)")

    manifest = {"base_dir": args.base_dir, "added": args.add, "splits": {}}
    for split in ("train", "val", "test"):
        src = base / f"{split}.jsonl"
        if not src.exists():
            print(f"  [건너뜀] {src} 없음")
            continue
        merged = rows(src) + (add if split == "train" else [])

        # fail-closed: 봉인 유출 검사
        leak_id = [r for r in merged if r.get("doc_id") in sealed_ids]
        leak_sha = [r for r in merged if text_sha(r) in sealed_sha]
        if leak_id or leak_sha:
            print(f"  [FATAL] {split} 에 봉인 문서 유입: id {len(leak_id)}건 · 본문 {len(leak_sha)}건")
            print("  파일을 쓰지 않는다. 봉인이 깨지면 이후 측정이 전부 무의미하다.")
            return 2

        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n"
        (out / f"{split}.jsonl").write_text(body, "utf-8")
        c = Counter(r.get("label") for r in merged)
        w = Counter()
        for r in merged:
            w[r.get("label")] += float(r.get("sample_weight") if r.get("sample_weight") is not None else 1.0)
        manifest["splits"][split] = {
            "n": len(merged),
            "by_label": dict(c),
            "weight_sum": {k: round(v, 1) for k, v in w.items()},
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
        print(f"\n[{split}] {len(merged)}건")
        print(f"  등급     {dict(c)}")
        print(f"  가중치합 {dict((k, round(v,1)) for k,v in w.items())}")

    tr = manifest["splits"].get("train", {})
    ws = tr.get("weight_sum", {})
    if ws.get("S3"):
        share = sum(r.get("sample_weight", 1.0) for r in add) / ws["S3"]
        print(f"\n신규 서식이 S3 학습 신호에서 차지하는 비중: {share:.1%}")
        manifest["new_forms_share_of_s3_weight"] = round(share, 4)

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[out] {out}")
    print("[ok] 봉인 유출 0건 확인")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
