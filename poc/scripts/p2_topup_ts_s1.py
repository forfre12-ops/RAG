"""oss_corpus TS/S1 등급 보충 수집.

현황: TS 443, S1 435 → 목표: 각 900건 (S2/S3 수준 균형)
추가 필요: TS +457, S1 +465

판례 데이터에서 TS/S1 특화 키워드로만 필터링해 추가.
캐시된 HuggingFace 데이터 재사용 (재다운로드 없음).
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ── TS/S1 특화 키워드 ──────────────────────────────────────────

TS_KEYWORDS = [
    "영업비밀", "기술상의 정보", "경영상의 정보", "핵심 기술", "제조 공정",
    "원료 배합", "조성비율", "합성방법", "소스코드", "알고리즘", "설계도면",
    "노하우", "기술 유출", "산업기술", "공정 레시피", "배양 조건",
    "기술정보", "비밀관리", "비밀로 관리", "독립된 경제적 가치",
    "부정경쟁방지", "영업비밀 침해", "영업비밀 보호", "핵심 노하우",
    "제조방법", "기술방법", "생산방법", "생산공정", "핵심공정",
]

S1_KEYWORDS = [
    "특허청구", "청구항", "발명의 설명", "실시예", "균등침해", "직접침해",
    "특허 침해", "권리범위", "특허 무효", "특허권", "발명자", "특허법",
    "특허출원", "특허등록", "진보성", "신규성", "선행기술", "특허발명",
    "실용신안", "특허 분쟁", "침해 여부", "특허 청구범위",
]


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[=\-_]{4,}", " ", text)
    return text.strip()


def assign_grade_strict(text: str) -> str | None:
    """TS/S1만 반환, 해당 없으면 None."""
    ts_count = sum(1 for kw in TS_KEYWORDS if kw in text)
    s1_count = sum(1 for kw in S1_KEYWORDS if kw in text)

    # TS가 1개라도 있으면 TS (영업비밀 관련 판례)
    if ts_count >= 1:
        return "TS"
    # S1은 2개 이상 (특허 관련이 더 일반적이므로 임계값 유지)
    if s1_count >= 2:
        return "S1"
    return None


def infer_domain(text: str) -> str:
    kw_map = {
        "반도체": ["반도체", "웨이퍼", "DRAM", "낸드", "파운드리", "식각", "증착"],
        "배터리": ["배터리", "이차전지", "양극재", "음극재", "전해질", "충방전"],
        "화학_제약": ["합성", "촉매", "조성", "시약", "의약품", "임상", "화합물"],
        "소프트웨어": ["소프트웨어", "알고리즘", "소스코드", "프로그램", "서버"],
        "바이오_농업": ["유전자", "세포", "균주", "배양", "종자", "DNA", "미생물"],
        "경영정보": ["매출", "영업이익", "고객", "계약", "공급망"],
    }
    scores = {d: sum(1 for kw in kws if kw in text) for d, kws in kw_map.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "기타"


def main() -> int:
    corpus_dir = Path("datasets/oss_corpus")
    queries_path = Path("datasets/oss_eval_queries.json")

    # 현재 상태 파악
    existing: list[dict] = []
    seen_bodies: set[str] = set()
    grade_count: dict[str, int] = {}
    for f in corpus_dir.glob("*.json"):
        doc = json.loads(f.read_text(encoding="utf-8"))
        existing.append(doc)
        seen_bodies.add(doc["body"][:120])
        g = doc["target_grade"]
        grade_count[g] = grade_count.get(g, 0) + 1

    print(f"[topup] 현재: {grade_count}")

    target_each = max(grade_count.get("S2", 0), grade_count.get("S3", 0))
    need_ts = max(0, target_each - grade_count.get("TS", 0))
    need_s1 = max(0, target_each - grade_count.get("S1", 0))
    print(f"[topup] 목표: 각 {target_each}건 → TS +{need_ts}, S1 +{need_s1}")

    if need_ts == 0 and need_s1 == 0:
        print("[topup] 이미 균형 상태")
        return 0

    # 판례 데이터 로드 (캐시 재사용)
    print("[topup] 판례 데이터 로드 (캐시) ...")
    from datasets import load_dataset
    ds = load_dataset(
        "joonhok-exo-ai/korean_law_open_data_precedents",
        split="train",
    )

    # 필드명을 고정하지 않고 모든 문자열 필드를 합산
    sample = ds[0]
    all_str_fields = [k for k, v in sample.items() if isinstance(v, str)]
    print(f"[topup] 전체 문자열 필드: {all_str_fields}")

    added_ts = 0
    added_s1 = 0
    new_docs: list[dict] = []

    for row in ds:
        if added_ts >= need_ts and added_s1 >= need_s1:
            break

        # 모든 문자열 필드 합산 (짧은 메타데이터 제외: 50자 이상만)
        parts = [str(v).strip() for k, v in row.items()
                 if isinstance(v, str) and len(str(v).strip()) >= 50]
        body = clean_text(" ".join(parts))

        # 티어별 길이 필터 (3000 → 2000 → 1000)
        if len(body) < 1000:
            continue

        if body[:120] in seen_bodies:
            continue

        grade = assign_grade_strict(body)
        if grade is None:
            continue
        if grade == "TS" and added_ts >= need_ts:
            continue
        if grade == "S1" and added_s1 >= need_s1:
            continue

        # 길이 기준 분류 (로깅용)
        tier = "3000+" if len(body) >= 3000 else "2000+" if len(body) >= 2000 else "1000+"

        title_val = ""
        for f in ["사건명", "사건번호", "제목"]:
            if row.get(f):
                title_val = str(row[f])[:80]
                break
        if not title_val:
            title_val = body[:60].strip()

        doc = {
            "synth_id": str(uuid.uuid4()),
            "title": title_val,
            "body": body[:8000],
            "target_grade": grade,
            "domain": infer_domain(body),
            "source": f"판례({tier})",
        }

        new_docs.append(doc)
        seen_bodies.add(body[:120])
        fpath = corpus_dir / f"{doc['synth_id']}.json"
        fpath.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if grade == "TS":
            added_ts += 1
        else:
            added_s1 += 1

        if (added_ts + added_s1) % 100 == 0:
            print(f"[topup]   진행: TS+{added_ts} S1+{added_s1}")

    # 쿼리 보충
    print(f"\n[topup] 추가 완료: TS+{added_ts}, S1+{added_s1}")

    if new_docs and queries_path.exists():
        existing_queries = json.loads(queries_path.read_text(encoding="utf-8"))

        def extract_queries(docs: list[dict]) -> list[dict]:
            qs = []
            for doc in docs:
                sents = re.split(
                    r"(?<=[다했다었다였다됩니다습니다])\s+|\n", doc["body"]
                )
                good = [s.strip() for s in sents if 50 <= len(s.strip()) <= 200]
                tech_kws = ["공정", "기술", "방법", "조성", "특허", "영업비밀",
                            "발명", "청구", "침해", "개발"]
                tech_s = [s for s in good if any(k in s for k in tech_kws)]
                for sent in (tech_s or good)[:2]:
                    qs.append({
                        "text": sent,
                        "expected_grade": doc["target_grade"],
                        "expected_doc_id": doc["synth_id"],
                        "source_doc_title": doc["title"],
                        "source": doc["source"],
                    })
            return qs

        new_queries = extract_queries(new_docs)
        all_queries = existing_queries + new_queries
        queries_path.write_text(
            json.dumps(all_queries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[topup] 쿼리: {len(existing_queries)} + {len(new_queries)} = {len(all_queries)}건")

    # 최종 등급 분포
    final: dict[str, int] = {}
    for f in corpus_dir.glob("*.json"):
        g = json.loads(f.read_text(encoding="utf-8"))["target_grade"]
        final[g] = final.get(g, 0) + 1
    total = sum(final.values())
    print(f"\n[topup] 최종: {final} | 총 {total:,}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
