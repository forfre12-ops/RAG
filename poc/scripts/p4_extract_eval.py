"""P4 PoC — 문서 추출 품질·속도 평가 + Go/No-Go 자동 판정.

합격 기준 (doc/02 §3.3):
  - 텍스트 추출 누락률 ≤ 5%   (= 추출 실패 또는 길이 0 비율)
  - quality_score 평균 ≥ 0.7   (정규화 기준)

사용:
  python scripts/build_p4_corpus.py
  python scripts/p4_extract_eval.py --in datasets/p4_corpus --out reports/p4_extract_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

# pyproject editable install이 없는 환경 대비
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lloydk.modules.m2_preprocess import (  # noqa: E402
    ExtractResult,
    PreprocessPipeline,
    extract,
    normalize,
    quality_score,
)

SUPPORTED = {".pdf", ".docx", ".hwp", ".hwpx", ".txt", ".md"}


def evaluate(indir: Path) -> dict:
    pipe = PreprocessPipeline()
    rows: list[dict] = []
    for p in sorted(indir.rglob("*")):
        if p.is_dir() or p.suffix.lower() not in SUPPORTED:
            continue
        start = time.perf_counter()
        ext: ExtractResult = extract(p)
        norm = normalize(ext.text) if ext.text else ""
        q = quality_score(ext.text, norm) if ext.text else 0.0
        chunks = pipe.chunk(norm) if norm else []
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        rows.append(
            {
                "file": str(p.relative_to(indir)),
                "suffix": p.suffix.lower(),
                "method": ext.method,
                "ocr_used": ext.ocr_used,
                "raw_len": len(ext.text),
                "clean_len": len(norm),
                "noise_ratio": round(1 - len(norm) / max(len(ext.text), 1), 4),
                "quality": round(q, 3),
                "n_chunks": len(chunks),
                "elapsed_ms": elapsed_ms,
                "ok": bool(norm),
                "error": ext.error,
            }
        )
    return _summarize(rows)


def _summarize(rows: list[dict]) -> dict:
    total = len(rows)
    ok = sum(1 for r in rows if r["ok"])
    fail = total - ok
    miss_rate = fail / total if total else 0.0
    avg_q = sum(r["quality"] for r in rows) / total if total else 0.0
    by_ext = Counter(r["suffix"] for r in rows)
    by_method = Counter(r["method"] for r in rows)
    avg_speed = sum(r["elapsed_ms"] for r in rows) / total if total else 0.0
    return {
        "rows": rows,
        "summary": {
            "total": total,
            "ok": ok,
            "fail": fail,
            "miss_rate": round(miss_rate, 4),
            "avg_quality": round(avg_q, 3),
            "avg_elapsed_ms": round(avg_speed, 1),
            "by_extension": dict(by_ext),
            "by_method": dict(by_method),
        },
    }


def write_report(result: dict, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    s = result["summary"]
    miss_pass = s["miss_rate"] <= 0.05
    qual_pass = s["avg_quality"] >= 0.7
    verdict = "PASS" if (miss_pass and qual_pass) else "FAIL"

    md_lines = [
        "# P4 — 문서 추출 품질 평가 리포트",
        "",
        f"- **판정**: {verdict}",
        f"- 누락률 합격선 ≤ 5%: {s['miss_rate']*100:.2f}% ({'PASS' if miss_pass else 'FAIL'})",
        f"- 평균 품질 ≥ 0.7: {s['avg_quality']:.3f} ({'PASS' if qual_pass else 'FAIL'})",
        f"- 처리 총 {s['total']}건 / 성공 {s['ok']} / 실패 {s['fail']}",
        f"- 평균 처리시간: {s['avg_elapsed_ms']:.1f}ms",
        f"- 포맷별: {s['by_extension']}",
        f"- 추출 메서드별: {s['by_method']}",
        "",
        "## 파일별 결과",
        "",
        "| 파일 | 포맷 | 방법 | 원본 | 정규화 | 노이즈% | 품질 | 청크 | 시간(ms) | OK |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for r in result["rows"]:
        md_lines.append(
            f"| {r['file']} | {r['suffix']} | {r['method']} | "
            f"{r['raw_len']} | {r['clean_len']} | {r['noise_ratio']*100:.1f}% | "
            f"{r['quality']:.2f} | {r['n_chunks']} | {r['elapsed_ms']} | "
            f"{'✅' if r['ok'] else '❌'} |"
        )
    out.write_text("\n".join(md_lines), encoding="utf-8")

    json_out = out.with_suffix(".json")
    json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"verdict": verdict, "miss_pass": miss_pass, "qual_pass": qual_pass}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="indir", default="datasets/p4_corpus")
    ap.add_argument("--out", default="reports/p4_extract_report.md")
    args = ap.parse_args()

    indir = Path(args.indir)
    if not indir.exists():
        print(f"[p4] input not found: {indir}. Run scripts/build_p4_corpus.py first.", file=sys.stderr)
        return 2

    result = evaluate(indir)
    verdict = write_report(result, Path(args.out))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"\n[p4] verdict: {verdict['verdict']} -> {args.out}")
    return 0 if verdict["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
