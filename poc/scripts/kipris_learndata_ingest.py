"""Step 2 (doc/32 §3) — ingest KIPRIS Plus '특허 공개공보 학습데이터' (#9) into the
patent_proxy corpus. This is the RICHEST single Korean-patent source: one pickle,
keyed by 출원번호, each record carrying title/abstract/claims/spec + CPC/IPC.

Format (kipo_pub_mainfile_dataset.p, pickle protocol 4)
  {appno: {INV_TITLE, ABS(<summary> xml), PTCLS{claim_no: text}, SPEC{...},
           CPC[list], IPC[list], REG_STATE, ...}}

Grade selection mirrors kipris_bulk_ingest (keyword precision + primary-class IPC/
CPC); the authoritative fix for class-mapping noise is AIHub #71739 국가중점기술
(see kipris_aihub_nkt_ingest.py once that sample lands).

Output = kipris_collect.collect() shape so build_step2_dataset.py runs unchanged:
  {text, label(TS|S1), source:"공개특허", grade_basis, domain:"기술",
   app_no, label_source:"patent_proxy_content"}

Usage
  python scripts/kipris_learndata_ingest.py --pickle "<...>/Main/kipo_pub_mainfile_dataset.p" --dry-run
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# 단일 소스 재사용: 키워드 목록·XML 정리·등급판정 로직.
from kipris_bulk_ingest import clean_xml_text, grade_for  # noqa: E402


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).replace(" ", "") for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.replace(" ", "")]
    return []


def ingest(pkl: Path, min_abstract: int, include_claims: bool) -> tuple[list[dict], dict]:
    with open(pkl, "rb") as f:
        data = pickle.load(f)
    rows, stats = [], Counter()
    for appno, rec in data.items():
        stats["seen"] += 1
        abstract = clean_xml_text(rec.get("ABS", ""))
        if len(abstract) < min_abstract:
            stats["short_abstract"] += 1
            continue
        title = (rec.get("INV_TITLE") or "").strip()
        text = f"{title}\n\n{abstract}"
        if include_claims and isinstance(rec.get("PTCLS"), dict):
            claims = " ".join(clean_xml_text(c) for c in rec["PTCLS"].values())
            if claims:
                text += "\n\n[청구항] " + claims
        # CPC 우선, 없으면 IPC. grade_for는 리스트의 첫 코드를 '주 분류'로 사용.
        codes = _as_list(rec.get("CPC")) or _as_list(rec.get("IPC"))
        grade, basis = grade_for(text, codes)
        if grade is None:
            stats["unmatched_skip"] += 1
            continue
        rows.append({
            "text": text,
            "label": grade,
            "source": "공개특허",
            "grade_basis": f"learndata:{basis}",
            "domain": "기술",
            "app_no": str(appno),
            "label_source": "patent_proxy_content",
            "_cpc": codes[:5],
            "_reg": rec.get("REG_STATE", ""),
        })
        stats[grade] += 1
    return rows, dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pickle", required=True, help="kipo_pub_mainfile_dataset.p 경로")
    ap.add_argument("--out", default="datasets/patent_proxy/raw.jsonl")
    ap.add_argument("--min-abstract", type=int, default=120)
    ap.add_argument("--include-claims", action="store_true", help="본문에 청구항도 합침(콘텐츠 강화)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pkl = Path(args.pickle)
    if not pkl.exists():
        print(f"[learndata] {pkl} 없음.", file=sys.stderr)
        return 2

    rows, stats = ingest(pkl, args.min_abstract, args.include_claims)
    print(f"[learndata] 통계 {stats}")
    print(f"[learndata] 수집 {len(rows)}건  라벨분포={dict(Counter(r['label'] for r in rows))}")
    for r in rows[:6]:
        print(f"  - [{r['label']}|{r['grade_basis']}|{r['_reg']}|cpc={r['_cpc'][:3]}] "
              f"{r['app_no']}  {r['text'][:80].replace(chr(10),' ')}…")

    if args.dry_run:
        print("[learndata] --dry-run: 파일 미기록")
        return 0

    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in clean) + "\n", encoding="utf-8")
    print(f"[learndata] wrote {len(clean)}건 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
