"""Step 2 (doc/32 §3) — ingest AIHub #71739 '국가중점기술 분류 대응 특허데이터' (137)
into the patent_proxy corpus. This is the AUTHORITATIVE high-grade source: every
patent is KIPO-classified into a national-key-technology 소분류, so the grade is
grounded in an official label — not the keyword/IPC heuristic that mislabeled a
clothes-dryer as TS.

Layout (3.개방데이터/1.데이터/{Training,Validation}/01.원천데이터/*.zip.part0)
  Each .zip.part0 is a COMPLETE zip (single part). Its FILENAME encodes the label:
    TS_<L>_<Lname>_<MM>_<Mname>_<SSS>_<Sname>.zip.part0
  so every patent JSON inside shares that (L,M,S) national-key-tech category.
  Source JSON (per documentId):
    {dataset:{invention_title, abstract(plain text), claims, ipc_main/subclass/all,
              application_number, documentId, country_code...}}
  ~92% KR patents, ~8% foreign (machine-translated KO text) — filter with --kr-only.

Grade policy (DEFAULT, domain-confirmable): §9 국가핵심기술 핵심분야 → TS, 그 외
국가중점기술 → S1. The full (L,M,S) label is kept on each record for re-labeling.

Output = kipris_collect.collect() shape so build_step2_dataset.py runs unchanged.

Usage
  python scripts/kipris_aihub_nkt_ingest.py --root "<137 root>" --dry-run --sample-per-zip 20
  python scripts/kipris_aihub_nkt_ingest.py --root "<137 root>" --kr-only
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from kipris_bulk_ingest import clean_xml_text  # noqa: E402  (ABS is plain here, but harmless)

# §9 국가핵심기술 핵심분야 → TS. 분류명(L/M/S)에 substring 매칭. 나머지 137 → S1.
# (도메인 확정 전 기본값. grade_basis에 근거를 남기므로 재라벨 용이.)
TS_NKT_KEYWORDS = (
    "반도체", "디스플레이", "이차전지", "원자력", "핵융합", "가속기",
    "우주발사체", "우주탐사", "우주환경", "국방", "정밀타격", "감시정찰", "사이버",
    "금속소재", "선박", "자동차", "로봇", "신약", "줄기세포", "유전자치료", "양자",
)


def nkt_grade(label_path: str) -> tuple[str, str]:
    """label_path = 'Lname/Mname/Sname' 결합 문자열."""
    for kw in TS_NKT_KEYWORDS:
        if kw in label_path:
            return "TS", f"TS-kw:{kw}"
    return "S1", "S1-default"


def parse_category(zip_name: str) -> dict:
    """TS_<L>_<Lname>_<MM>_<Mname>_<SSS>_<Sname>.zip.part0 → 분류 라벨."""
    parts = zip_name.replace(".zip.part0", "").replace(".zip", "").split("_")
    L = parts[1] if len(parts) > 1 else ""
    # 중분류 코드 = 2글자 대문자 ASCII, 대분류 글자로 시작(AA/BD/CA). 'SW'·'TS' 등 제외.
    mi = next((i for i, t in enumerate(parts)
               if i >= 2 and len(t) == 2 and t.isascii() and t.isalpha() and t.isupper() and t[0] == L), None)
    Mno = parts[mi] if mi is not None else ""
    # 소분류 코드 = 3글자 대문자 ASCII, 중분류 코드로 시작(AAA/CAA). 'ICT' 등 제외.
    si = next((i for i, t in enumerate(parts)
               if i >= 2 and len(t) == 3 and t.isascii() and t.isalpha() and t.isupper() and Mno and t.startswith(Mno)), None)
    if mi is None:
        return {"Lno": L, "Lname": "_".join(parts[2:]), "Mno": "", "Mname": "", "Sno": "", "Sname": ""}
    Lname = "_".join(parts[2:mi])
    Mno = parts[mi]
    Mname = "_".join(parts[mi + 1:si]) if si else ""
    Sno = parts[si] if si else ""
    Sname = "_".join(parts[si + 1:]) if si else ""
    return {"Lno": L, "Lname": Lname, "Mno": Mno, "Mname": Mname, "Sno": Sno, "Sname": Sname}


def iter_records(root: Path, splits: list[str], sample_per_zip: int, kr_only: bool):
    for split in splits:
        srcdir = root / "3.개방데이터" / "1.데이터" / split / "01.원천데이터"
        if not srcdir.exists():
            continue
        for z in sorted(srcdir.glob("*.part0")):
            cat = parse_category(z.name)
            label_path = f"{cat['Lname']}/{cat['Mname']}/{cat['Sname']}"
            grade, basis = nkt_grade(label_path)
            try:
                zf = zipfile.ZipFile(io.BytesIO(z.read_bytes()))
            except Exception as e:  # noqa: BLE001
                print(f"[nkt] zip 열기 실패 {z.name}: {repr(e)[:80]}", file=sys.stderr)
                continue
            names = [n for n in zf.namelist() if n.lower().endswith(".json")]
            if sample_per_zip:
                names = names[:sample_per_zip]
            for n in names:
                try:
                    d = json.loads(zf.read(n).decode("utf-8"))
                except Exception:
                    continue
                rec = d.get("dataset", d)
                # country_code는 라벨데이터에만 있음. 원천은 documentId 접두로 국가 판별(kr/jp/us…).
                if kr_only:
                    docid = (rec.get("documentId") or "").lower()
                    cc = (rec.get("country_code") or "").upper()
                    is_kr = docid.startswith("kr") or cc == "KR"
                    if not is_kr:
                        continue
                yield cat, grade, basis, rec


def build_text(rec: dict, include_claims: bool) -> str:
    title = (rec.get("invention_title") or "").strip()
    abstract = clean_xml_text(rec.get("abstract", ""))
    text = f"{title}\n\n{abstract}".strip()
    if include_claims and rec.get("claims"):
        text += "\n\n[청구항] " + clean_xml_text(str(rec["claims"]))
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="137.국가중점기술... 폴더")
    ap.add_argument("--out", default="datasets/patent_proxy/raw.jsonl")
    ap.add_argument("--min-abstract", type=int, default=120)
    ap.add_argument("--splits", default="Training", help="콤마구분: Training,Validation")
    ap.add_argument("--sample-per-zip", type=int, default=0, help="zip namelist 앞 N개만 읽음(빠른 검증용). 0=전체")
    ap.add_argument("--per-subclass", type=int, default=0, help="소분류(zip)당 채택 상한(필터 후 균형샘플). 0=무제한")
    ap.add_argument("--kr-only", action="store_true", help="한국 특허만(country_code=KR)")
    ap.add_argument("--include-claims", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"[nkt] {root} 없음.", file=sys.stderr)
        return 2
    splits = [s.strip() for s in args.splits.split(",") if s.strip()]

    seen: set[str] = set()
    stats: Counter = Counter()
    by_L: Counter = Counter()
    out = Path(args.out)
    fh = None
    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        fh = open(out, "w", encoding="utf-8")

    samples = []
    cat_count: Counter = Counter()
    for cat, grade, basis, rec in iter_records(root, splits, args.sample_per_zip, args.kr_only):
        stats["seen"] += 1
        text = build_text(rec, args.include_claims)
        if len(clean_xml_text(rec.get("abstract", ""))) < args.min_abstract:
            stats["short_abstract"] += 1
            continue
        doc = rec.get("documentId") or rec.get("application_number") or ""
        if doc in seen:
            stats["dup"] += 1
            continue
        catkey = f"{cat['Lno']}{cat['Mno']}{cat['Sno']}"
        if args.per_subclass and cat_count[catkey] >= args.per_subclass:
            stats["over_cap"] += 1
            continue
        cat_count[catkey] += 1
        seen.add(doc)
        row = {
            "text": text, "label": grade, "source": "공개특허",
            "grade_basis": f"nkt:{cat['Lno']}{cat['Mno']}{cat['Sno']}:{basis}",
            "domain": cat["Lname"], "app_no": rec.get("application_number", ""),
            "label_source": "patent_proxy_nkt",
        }
        stats[grade] += 1
        by_L[f"{cat['Lno']}:{grade}"] += 1
        if fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        elif len(samples) < 8:
            samples.append((row, cat))
    if fh:
        fh.close()

    print(f"[nkt] 통계 {dict(stats)}")
    print(f"[nkt] 라벨분포 TS={stats['TS']} S1={stats['S1']}  (dedup 후 {len(seen)}건)")
    print(f"[nkt] 대분류x등급: {dict(sorted(by_L.items()))}")
    for row, cat in samples:
        print(f"  - [{row['label']}|{cat['Lno']}{cat['Mno']}{cat['Sno']} {cat['Sname']}] "
              f"{row['app_no']}  {row['text'][:70].replace(chr(10),' ')}…")
    if not args.dry_run:
        print(f"[nkt] wrote {len(seen)}건 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
