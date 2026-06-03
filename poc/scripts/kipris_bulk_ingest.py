"""Step 2 (doc/32 §3) — alternative collector: ingest KIPO BULK 공개공보 dumps
into the same patent_proxy corpus that kipris_collect.py produces from the API.

Why this exists
---------------
The KIPRIS getWordSearch API needs an active service key (subscription period).
When that is unavailable, the same REAL high-grade technical content can be
obtained from KIPO's downloadable 공개공보 bulk dataset (특허·실용 공개공보), which
ships clean ¶-delimited TXT tables:
  Bibliographic.txt  : 출원번호 .. [15]발명의명칭(국문) [16](영문) [18]등록사항
  Abstract.txt       : 출원번호 ¶ <summary><p>…초록…</p></summary>
  IPC.txt            : 출원번호 ¶ 일련번호 ¶ ipc코드 ¶ 개정일자   (domain selection)
  Claim.txt / Specification.txt … (not required here)
All tables join on 출원번호. Encoding = UTF-8, field delimiter = '¶' (U+00B6).

A bulk gazette is an UNFILTERED mix of every field (semiconductors next to a
clothes-dryer utility model). The API path got only high-grade-domain patents
via targeted 단어검색; here we reproduce that selection with the SAME TS/S1
keyword lists + an (approximate) national-core-tech IPC map. Anything matching
neither is skipped — we do NOT label mundane public patents as secret.

Output is byte-identical in shape to kipris_collect.collect() so the downstream
(build_step2_dataset.py → train → eval_patent_recall.py) runs unchanged:
  {text, label(TS|S1), source:"공개특허", grade_basis, domain:"기술",
   app_no, label_source:"patent_proxy_content"}

Provenance (doc/22 §4.0): a PUBLISHED patent is public (S3) by provenance; we use
its CONTENT to teach the Gate-2 content model. source="공개특허" → gate caps to S3.

Usage
-----
  python scripts/kipris_bulk_ingest.py --root D:/antigravity/rag/sample --dry-run
  python scripts/kipris_bulk_ingest.py --root <bulk_root>            # → datasets/patent_proxy/raw.jsonl
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# Reuse the EXACT grade keyword lists the API collector uses (single source of truth).
from kipris_collect import TS_KEYWORDS, S1_KEYWORDS  # noqa: E402

DELIM = "¶"  # ¶

# 국가핵심기술(산업기술보호법 §9) 근사 IPC 접두 → TS. 공백 제거 후 startswith 비교.
# 정밀 지정목록이 아니라 분야 근사이므로 grade_basis에 'ipc:'로 출처를 남긴다.
TS_IPC_PREFIXES = (
    "H01L",      # 반도체 소자·공정
    "H10K", "H10B", "H10N",  # 반도체/메모리(신 IPC)
    "H01M4", "H01M10", "H01M50",  # 이차전지 전극·셀·팩
    "G02F", "G09G", "H01L51",     # 디스플레이/OLED
    "B60W", "G05D1", "G08G1",     # 자율주행/차량제어
    "C22C",      # 합금(고강도 철강·소재)
    "G21",       # 원자력
    "B64G",      # 우주
)
# 일반 고기술 → S1 (영업비밀급 노하우). 너무 넓지 않게 제한.
S1_IPC_PREFIXES = (
    "C07", "C12N", "C12P", "A61K",  # 정밀화학·바이오·신약
    "G06N",      # 기계학습/AI 모델
    "B25J",      # 로봇
    "F02", "F01D",  # 엔진·터빈
)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_xml_text(s: str) -> str:
    """Strip inline XML/HTML tags (<summary>,<p>,<br>,<sub>…), unescape, collapse ws."""
    if not s:
        return ""
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    return _WS.sub(" ", s).strip()


def _read_table(p: Path) -> list[list[str]]:
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln:
            rows.append(ln.split(DELIM))
    return rows


def _index_by_appno(rows: list[list[str]], value_col: int) -> dict[str, str]:
    """Map 출원번호(col0) → value at value_col (first non-empty wins)."""
    out: dict[str, str] = {}
    for cells in rows[1:]:  # skip header
        if len(cells) <= value_col:
            continue
        appno, val = cells[0].strip(), cells[value_col].strip()
        if appno and val and appno not in out:
            out[appno] = val
    return out


def _ipc_by_appno(rows: list[list[str]]) -> dict[str, list[str]]:
    """출원번호 → IPC 코드 리스트(특허분류일련번호 오름차순). 첫 항목 = 주 분류."""
    tmp: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for cells in rows[1:]:
        if len(cells) >= 3 and cells[0].strip():
            code = cells[2].strip().replace(" ", "")
            if not code:
                continue
            try:
                seq = int(cells[1].strip())
            except ValueError:
                seq = 999
            tmp[cells[0].strip()].append((seq, code))
    return {appno: [c for _, c in sorted(pairs)] for appno, pairs in tmp.items()}


def grade_for(text: str, ipc_codes: list[str]) -> tuple[str | None, str | None]:
    """등급 판정. 텍스트 키워드는 정밀 신호(우선), IPC는 '주 분류(첫 코드)'에만 적용.
    부차 IPC로 판정하면 빨래건조기가 H01M10(부품 배터리)로 TS가 되는 과분류 발생."""
    primary = ipc_codes[0] if ipc_codes else ""
    for kw in TS_KEYWORDS:
        if kw in text:
            return "TS", f"kw:{kw}"
    for pref in TS_IPC_PREFIXES:
        if primary.startswith(pref):
            return "TS", f"ipc:{pref}"
    for kw in S1_KEYWORDS:
        if kw in text:
            return "S1", f"kw:{kw}"
    for pref in S1_IPC_PREFIXES:
        if primary.startswith(pref):
            return "S1", f"ipc:{pref}"
    return None, None


def find_units(root: Path) -> list[Path]:
    """Directories that contain both Bibliographic.txt and Abstract.txt."""
    units = []
    for biblio in root.rglob("Bibliographic.txt"):
        if (biblio.parent / "Abstract.txt").exists():
            units.append(biblio.parent)
    return sorted(set(units))


def ingest_unit(d: Path, min_abstract: int) -> tuple[list[dict], dict]:
    titles = _index_by_appno(_read_table(d / "Bibliographic.txt"), 15)  # 발명의명칭(국문)
    pub_status = _index_by_appno(_read_table(d / "Bibliographic.txt"), 18)  # 등록사항
    abstracts = _index_by_appno(_read_table(d / "Abstract.txt"), 1)
    ipc = _ipc_by_appno(_read_table(d / "IPC.txt")) if (d / "IPC.txt").exists() else {}

    rows, stats = [], Counter()
    for appno, raw_abs in abstracts.items():
        stats["seen"] += 1
        abstract = clean_xml_text(raw_abs)
        if len(abstract) < min_abstract:
            stats["short_abstract"] += 1
            continue
        title = titles.get(appno, "")
        text = f"{title}\n\n{abstract}".strip()
        grade, basis = grade_for(text, ipc.get(appno, []))
        if grade is None:
            stats["unmatched_skip"] += 1
            continue
        rows.append({
            "text": text,
            "label": grade,
            "source": "공개특허",
            "grade_basis": f"bulk:{basis}",
            "domain": "기술",
            "app_no": appno,
            "label_source": "patent_proxy_content",
            "_pub_status": pub_status.get(appno, ""),
            "_ipc": ipc.get(appno, [])[:5],
        })
        stats[grade] += 1
    return rows, dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="bulk 공보 루트(하위에서 Bibliographic.txt 자동 탐색)")
    ap.add_argument("--out", default="datasets/patent_proxy/raw.jsonl")
    ap.add_argument("--min-abstract", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true", help="파일에 쓰지 않고 통계·샘플만 출력")
    args = ap.parse_args()

    root = Path(args.root)
    units = find_units(root)
    if not units:
        print(f"[bulk] {root} 아래에서 Bibliographic.txt+Abstract.txt 쌍을 못 찾음.", file=sys.stderr)
        return 2
    print(f"[bulk] units(공보 묶음) {len(units)}개 발견", file=sys.stderr)

    all_rows: list[dict] = []
    agg: Counter = Counter()
    seen_appno: set[str] = set()
    for d in units:
        rows, stats = ingest_unit(d, args.min_abstract)
        for k, v in stats.items():
            agg[k] += v
        for r in rows:
            if r["app_no"] in seen_appno:
                continue
            seen_appno.add(r["app_no"])
            all_rows.append(r)
        print(f"  [{d}] {stats}", file=sys.stderr)

    print(f"[bulk] 통계 {dict(agg)}")
    print(f"[bulk] 최종 수집 {len(all_rows)}건  라벨분포={dict(Counter(r['label'] for r in all_rows))}")
    for r in all_rows[:5]:
        print(f"  - [{r['label']}|{r['grade_basis']}|{r['_pub_status']}|ipc={r['_ipc']}] "
              f"{r['app_no']}  {r['text'][:90].replace(chr(10),' ')}…")

    if args.dry_run:
        print("[bulk] --dry-run: 파일 미기록")
        return 0

    # 출력 시 내부(_) 필드는 제거 — kipris_collect.collect() 포맷과 동일하게.
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in all_rows]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in clean) + "\n", encoding="utf-8")
    print(f"[bulk] wrote {len(clean)}건 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
