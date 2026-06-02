"""Step 2 (doc/32 §3): mine the 85K Korean precedents for REAL trade-secret /
tech-leak cases — court-grounded high-grade content (the only real proxy
accessible in this environment; KIPRIS patents / 공정위 need API keys).

Output: a structured corpus + a small sample file for content QA.

NOTE on the verbatim trap (doc/32 §3): the ruling text itself is public (S3).
This script EXTRACTS the court's substantive characterization (판시사항/판결요지)
of the adjudicated secret — that content is the high-grade signal. Turning it
into training-safe S1/S2 examples still needs a rewrite into internal-doc form
(LLM); this script produces the raw, filtered, court-grounded material.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# 영업비밀/부정경쟁/산업기술 관련 신호어
SECRET_KW = ["영업비밀", "부정경쟁", "산업기술", "전직금지", "비밀유지", "기술유출", "영업비밀침해"]
TECH_KW = ["공정", "노하우", "설계도", "소스코드", "알고리즘", "제조방법", "조성", "수율", "파라미터"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="datasets/trade_secret_cases/raw.jsonl")
    ap.add_argument("--sample", default="reports/step2_secret_cases_sample.json")
    ap.add_argument("--max", type=int, default=0, help="0=all")
    args = ap.parse_args()

    from datasets import load_dataset

    d = load_dataset("joonhok-exo-ai/korean_law_open_data_precedents", split="train")
    cols = d.column_names
    # 컬럼 순서: 사건일련번호, 사건명, 사건번호, 선고일자, 법원명, 사건종류명, 사건종류코드,
    #            판시사항, 판결요지, 참조조문, 참조판례, 전문  (공개 법령 데이터 표준)
    def col(row, idx):
        return str(row.get(cols[idx], "") or "")

    name_i, casetype_i, holding_i, summary_i, full_i = 1, 5, 7, 8, 11

    hits = []
    casetype_counter = Counter()
    for row in d:
        name = col(row, name_i)
        holding = col(row, holding_i)
        summary = col(row, summary_i)
        full = col(row, full_i)
        blob = f"{name}\n{holding}\n{summary}\n{full[:2000]}"
        if not any(k in blob for k in SECRET_KW):
            continue
        tech = sum(1 for k in TECH_KW if k in blob)
        hits.append({
            "case_name": name,
            "case_type": col(row, casetype_i),
            "holding": holding,        # 판시사항 — 법원이 무엇을 비밀로 판단했는지
            "summary": summary,        # 판결요지
            "full_excerpt": full[:1500],
            "tech_signal": tech,
            "secret_kw": [k for k in SECRET_KW if k in blob],
        })
        casetype_counter[col(row, casetype_i)] += 1

    hits.sort(key=lambda r: r["tech_signal"], reverse=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = hits if args.max == 0 else hits[: args.max]
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    # 샘플 (내용 QA용) — 기술신호 상위 12건
    Path(args.sample).parent.mkdir(parents=True, exist_ok=True)
    Path(args.sample).write_text(
        json.dumps(
            {
                "total_precedents": len(d),
                "trade_secret_hits": len(hits),
                "by_case_type": dict(casetype_counter.most_common()),
                "tech_signal_dist": dict(Counter(h["tech_signal"] for h in hits)),
                "top_samples": [
                    {k: (v[:600] if isinstance(v, str) else v) for k, v in h.items()}
                    for h in hits[:12]
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"total={len(d)} trade_secret_hits={len(hits)} wrote={out} sample={args.sample}")
    print(f"by_case_type(top): {dict(casetype_counter.most_common(6))}")
    print(f"tech_signal_dist: {dict(Counter(h['tech_signal'] for h in hits))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
