"""KOIPA 지식재산소송변론경연대회 기출문제 CSV → P2 평가 코퍼스 변환.

배경:
  기존 합성 문서는 등급당 title이 "[특급기밀] 내부 보고서"로 동일해
  doc 간 의미 구분이 불가능 → Recall@5 평가가 무의미(grade 매칭만으로 1.0).

  이 스크립트는 data.go.kr 공개 CSV (한국지식재산보호원_지식재산소송변론경연대회_기출문제)를
  읽어 각 기출문제 사안을 "영업비밀 내부 문서"로 재구성한다.
  - 문서마다 실제 IP 사안 내용 → 내용 다양성 확보
  - 쿼리는 문서 본문에서 2문장 추출 → grade 매칭이 아닌 doc_id 매칭 평가

사용:
  # 1단계: CSV를 poc/datasets/raw/koipa_contest/ 에 저장
  # 2단계: 코퍼스 빌드
  python scripts/p2_koipa_corpus_builder.py \\
      --csv datasets/raw/koipa_contest/*.csv \\
      --out datasets/koipa_corpus \\
      --queries-out datasets/koipa_eval_queries.json

  # 3단계: P2 재측정 (기존 스크립트 재사용)
  python scripts/p2_compare_embeddings.py \\
      --mode full \\
      --backends es \\
      --hybrid \\
      --synth-dir datasets/koipa_corpus \\
      --query-style koipa \\
      --report reports/p2_koipa.md
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ── 등급 분류 규칙 ─────────────────────────────────────────────

_TS_PATTERNS = [
    r"핵심\s*기술", r"제조\s*공정", r"원료\s*배합", r"알고리즘",
    r"소스\s*코드", r"설계\s*도면", r"합성\s*방법", r"특허\s*출원 전",
    r"영업\s*비밀", r"기술\s*유출", r"노하우",
]
_S1_PATTERNS = [
    r"특허\s*청구", r"발명의?\s*설명", r"실시\s*예", r"균등\s*침해",
    r"직접\s*침해", r"권리\s*범위", r"특허\s*무효",
]
_S2_PATTERNS = [
    r"상표\s*등록", r"디자인\s*등록", r"유사\s*판단", r"병행\s*수입",
    r"브랜드", r"상품\s*출처", r"오인\s*혼동",
]


def _assign_grade(text: str) -> str:
    """텍스트 내용으로 등급 판단. TS > S1 > S2 > S3 우선순위."""
    for pat in _TS_PATTERNS:
        if re.search(pat, text):
            return "TS"
    for pat in _S1_PATTERNS:
        if re.search(pat, text):
            return "S1"
    for pat in _S2_PATTERNS:
        if re.search(pat, text):
            return "S2"
    return "S3"


# ── CSV 파싱 ──────────────────────────────────────────────────

def _try_encodings(path: Path) -> str:
    """CP949 / UTF-8-BOM / UTF-8 순으로 시도."""
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"인코딩 감지 실패: {path}")


def _extract_text_columns(row: dict) -> str:
    """행에서 텍스트 컬럼을 합쳐 하나의 문자열로 반환."""
    skip_keys = {"연번", "번호", "no", "year", "연도", "구분코드"}
    parts = []
    for k, v in row.items():
        if not v or str(k).lower() in skip_keys:
            continue
        parts.append(f"{k}: {v}")
    return "\n".join(parts)


def _extract_title(row: dict) -> str:
    """제목 컬럼 추출. 없으면 첫 번째 값으로 대체."""
    for key in ("제목", "사건명", "문제제목", "title", "사안"):
        if row.get(key):
            return str(row[key])[:80]
    # 첫 번째 비어있지 않은 값
    for v in row.values():
        if v:
            return str(v)[:80]
    return "무제"


def _extract_domain(row: dict) -> str:
    for key in ("분야", "category", "구분", "유형"):
        v = row.get(key, "")
        if v:
            return str(v)
    return "지식재산"


def _extract_query_sentences(body: str, n: int = 2) -> list[str]:
    """본문에서 의미 있는 문장 n개 추출. 쿼리로 사용."""
    # 마침표/줄바꿈 기준 분리
    sents = re.split(r"(?<=[.。])\s+|\n", body)
    # 20자 이상 문장만
    good = [s.strip() for s in sents if len(s.strip()) >= 20]
    # 앞쪽 문장 우선
    return good[:n] if len(good) >= n else good


def load_csv(path: Path) -> list[dict]:
    raw = _try_encodings(path)
    reader = csv.DictReader(raw.splitlines())
    return list(reader)


# ── 문서 생성 ─────────────────────────────────────────────────

def build_doc(row: dict) -> dict:
    """CSV 행 1개 → P2 코퍼스 문서 1개."""
    full_text = _extract_text_columns(row)
    title_raw = _extract_title(row)
    domain = _extract_domain(row)
    grade = _assign_grade(full_text)

    # 등급 프리픽스 없는 실제 사안 기반 제목
    title = title_raw

    # 본문: 원문 그대로 (임베딩이 내용을 실제로 비교할 수 있도록)
    body = full_text

    return {
        "synth_id": str(uuid.uuid4()),
        "title": title,
        "body": body,
        "target_grade": grade,
        "domain": domain,
        "source": "koipa_contest",
        "_raw_text": full_text,  # 쿼리 추출용 (저장 제외)
    }


# ── 쿼리 생성 ─────────────────────────────────────────────────

def build_eval_queries(docs: list[dict]) -> list[dict]:
    """각 문서에서 본문 문장을 추출해 doc_id 기준 쿼리 생성.

    기존 grade-only 평가와 달리 특정 문서(doc_id)를 찾아야 해서
    임베딩 모델의 실제 내용 매칭 능력을 측정한다.
    """
    queries: list[dict] = []
    for doc in docs:
        sents = _extract_query_sentences(doc.get("_raw_text", doc["body"]), n=2)
        for sent in sents:
            queries.append({
                "text": sent,
                "expected_grade": doc["target_grade"],
                "expected_doc_id": doc["synth_id"],
                "source_doc_title": doc["title"],
            })
    return queries


# ── 메인 ─────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="KOIPA 기출문제 CSV → P2 코퍼스 변환")
    ap.add_argument("--csv", required=True, nargs="+", help="입력 CSV 파일 경로 (glob 가능)")
    ap.add_argument("--out", default="datasets/koipa_corpus", help="출력 디렉터리")
    ap.add_argument("--queries-out", default="datasets/koipa_eval_queries.json", help="쿼리 JSON 출력")
    ap.add_argument("--min-body-len", type=int, default=30, help="본문 최소 길이 (미달 행 제외)")
    args = ap.parse_args()

    # CSV 수집
    csv_paths: list[Path] = []
    for pattern in args.csv:
        p = Path(pattern)
        if p.is_file():
            csv_paths.append(p)
        else:
            csv_paths.extend(sorted(p.parent.glob(p.name)))

    if not csv_paths:
        print(f"[koipa] CSV 파일 없음: {args.csv}", file=sys.stderr)
        print("  → https://www.data.go.kr/data/15118138/fileData.do 에서 다운로드 후")
        print("    poc/datasets/raw/koipa_contest/ 에 저장하세요.")
        return 2

    # 문서 생성
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_docs: list[dict] = []
    for csv_path in csv_paths:
        print(f"[koipa] 읽기: {csv_path}")
        rows = load_csv(csv_path)
        print(f"[koipa]   행 수: {len(rows)}")
        for row in rows:
            doc = build_doc(row)
            if len(doc["body"]) < args.min_body_len:
                continue
            all_docs.append(doc)

    if not all_docs:
        print("[koipa] 유효한 행 없음", file=sys.stderr)
        return 1

    # 등급 분포 출력
    grade_dist: dict[str, int] = {}
    for doc in all_docs:
        grade_dist[doc["target_grade"]] = grade_dist.get(doc["target_grade"], 0) + 1
    print(f"[koipa] 문서 수: {len(all_docs)}")
    for g, cnt in sorted(grade_dist.items()):
        print(f"[koipa]   {g}: {cnt}건")

    # JSON 저장 (_raw_text 제외)
    for doc in all_docs:
        save_doc = {k: v for k, v in doc.items() if not k.startswith("_")}
        fpath = out_dir / f"{save_doc['synth_id']}.json"
        fpath.write_text(json.dumps(save_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[koipa] → {out_dir} 에 {len(all_docs)}건 저장")

    # 쿼리 생성
    queries = build_eval_queries(all_docs)
    queries_path = Path(args.queries_out)
    queries_path.parent.mkdir(parents=True, exist_ok=True)
    queries_path.write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[koipa] → 쿼리 {len(queries)}건: {queries_path}")

    # 샘플 출력
    print("\n[koipa] 샘플 문서:")
    for doc in all_docs[:2]:
        print(f"  title: {doc['title']}")
        print(f"  grade: {doc['target_grade']}, domain: {doc['domain']}")
        print(f"  body[:100]: {doc['body'][:100]}")
        print()

    print("[koipa] 완료. 다음 명령으로 P2 재측정:")
    print("  python scripts/p2_compare_embeddings.py \\")
    print("    --mode full --backends es --hybrid \\")
    print(f"    --synth-dir {out_dir} \\")
    print("    --query-style koipa \\")
    print("    --report reports/p2_koipa.md")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
