"""OSS 공개 데이터셋 → P2 평가 코퍼스 3,000건 수집.

전략:
  1순위: 3,000자 이상 문서
  2순위: 3,000건 미달 시 2,000자 이상으로 보충
  3순위: 여전히 미달 시 1,000자 이상으로 보충
  목표: 총 3,000건

소스:
  - joonhok-exo-ai/korean_law_open_data_precedents (판례 85K건)
  - nmixx-fin/synthetic_financial_report_korean (금융보고서 20K건)

실행:
  python scripts/p2_oss_corpus_builder.py \\
      --out datasets/oss_corpus \\
      --queries-out datasets/oss_eval_queries.json \\
      --target 3000
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

import argparse


# ── 텍스트 정제 ──────────────────────────────────────────────

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    # HTML 태그 제거
    text = re.sub(r"<[^>]+>", "", text)
    # 반복 특수문자 제거
    text = re.sub(r"[=\-_]{4,}", " ", text)
    return text.strip()


def extract_title(text: str, max_len: int = 80) -> str:
    """텍스트 첫 문장을 제목으로."""
    first = re.split(r"[。.\n]", text)[0].strip()
    return first[:max_len] if first else text[:max_len]


# ── M3 룰 라벨러 ─────────────────────────────────────────────

def assign_grade(text: str) -> tuple[str, float]:
    """M3 룰 라벨러 기반 등급 부여. (grade, confidence) 반환."""
    try:
        from lloydk.modules.m3_labeling.rule_labeler import RuleLabeler
        labeler = RuleLabeler()
        result = labeler.label(text)
        return result.grade, result.confidence
    except Exception:
        pass

    # 폴백: 키워드 기반 간이 판단
    ts = ["핵심 기술", "제조 공정", "원료 배합", "알고리즘", "소스코드", "설계도",
          "합성방법", "영업비밀", "기술유출", "노하우", "공정 레시피", "조성비율",
          "특허 출원 전", "핵심 노하우", "ALD", "CVD", "etching", "도핑",
          "전구체", "소성", "양극재", "음극재", "전해질", "촉매", "배양 조건"]
    s1 = ["특허 청구", "발명의 설명", "실시예", "균등침해", "권리범위", "특허 무효",
          "연구 개발", "기술 개선", "수율 개선", "성능 향상", "임상", "밸리데이션"]
    s2 = ["상표", "디자인 등록", "병행 수입", "브랜드", "영업 전략", "고객 명단",
          "계약 조건", "단가", "공급업체", "시장 분석", "재무", "매출"]

    text_lower = text.lower()
    for kw in ts:
        if kw.lower() in text_lower:
            return "TS", 0.75
    for kw in s1:
        if kw.lower() in text_lower:
            return "S1", 0.70
    for kw in s2:
        if kw.lower() in text_lower:
            return "S2", 0.65
    return "S3", 0.60


# ── 도메인 추론 ───────────────────────────────────────────────

def infer_domain(text: str) -> str:
    kw_map = {
        "반도체": ["반도체", "웨이퍼", "DRAM", "낸드", "파운드리", "식각", "증착", "ALD", "CVD"],
        "배터리": ["배터리", "이차전지", "양극재", "음극재", "전해질", "충방전", "NCM", "LFP"],
        "화학_제약": ["합성", "촉매", "조성", "시약", "분자", "약물", "의약품", "임상", "화합물"],
        "소프트웨어": ["소프트웨어", "알고리즘", "소스코드", "프로그램", "서버", "데이터베이스", "API"],
        "바이오_농업": ["유전자", "세포", "균주", "배양", "종자", "품종", "DNA", "미생물"],
        "경영정보": ["매출", "영업이익", "고객", "계약", "단가", "공급망", "인수합병"],
    }
    text_lower = text.lower()
    scores = {d: sum(1 for kw in kws if kw.lower() in text_lower)
              for d, kws in kw_map.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "기타"


# ── 판례 데이터 로드 ──────────────────────────────────────────

def load_precedents(min_len: int, max_docs: int) -> list[dict]:
    print(f"[oss] 판례 데이터 로드 중 (최소 {min_len:,}자) ...")
    try:
        from datasets import load_dataset
        ds = load_dataset(
            "joonhok-exo-ai/korean_law_open_data_precedents",
            split="train",
            trust_remote_code=True,
        )
    except Exception as e:
        print(f"[oss] 판례 로드 실패: {e}", file=sys.stderr)
        return []

    print(f"[oss]   전체 판례: {len(ds):,}건")

    # 스키마 확인
    sample = ds[0]
    print(f"[oss]   필드: {list(sample.keys())}")

    # 본문 필드 탐색
    text_fields = []
    for f in ["content", "text", "본문", "판결문", "전문", "판시사항", "판결내용",
              "이유", "주문", "사실", "사건개요"]:
        if f in sample:
            text_fields.append(f)
    if not text_fields:
        # 가장 긴 문자열 필드 선택
        text_fields = [k for k, v in sample.items()
                       if isinstance(v, str) and len(v) > 100]
    print(f"[oss]   본문 필드 후보: {text_fields}")

    docs: list[dict] = []
    # 기술·영업비밀 관련 판례 필터 키워드
    filter_kws = ["영업비밀", "기술유출", "특허", "발명", "제조공정", "노하우",
                  "영업방법", "기술정보", "산업기술", "부정경쟁"]

    for row in ds:
        if len(docs) >= max_docs:
            break

        # 본문 추출
        body_parts = []
        for f in text_fields:
            val = row.get(f, "")
            if isinstance(val, str) and val.strip():
                body_parts.append(val.strip())
        body = clean_text(" ".join(body_parts))

        if len(body) < min_len:
            continue

        # 기술·영업비밀 관련 여부 확인
        if not any(kw in body for kw in filter_kws):
            continue

        grade, conf = assign_grade(body)
        domain = infer_domain(body)

        # 제목 필드
        title_val = ""
        for f in ["사건명", "title", "제목", "사건번호"]:
            if row.get(f):
                title_val = str(row[f])[:80]
                break
        if not title_val:
            title_val = extract_title(body)

        docs.append({
            "synth_id": str(uuid.uuid4()),
            "title": title_val,
            "body": body[:8000],  # 최대 8,000자
            "target_grade": grade,
            "grade_confidence": conf,
            "domain": domain,
            "source": "판례",
            "char_len": len(body),
        })

    print(f"[oss]   판례 수집: {len(docs):,}건")
    return docs


# ── 금융보고서 데이터 로드 ─────────────────────────────────────

def load_financial_reports(min_len: int, max_docs: int) -> list[dict]:
    print(f"[oss] 금융보고서 로드 중 (최소 {min_len:,}자) ...")
    try:
        from datasets import load_dataset
        ds = load_dataset(
            "nmixx-fin/synthetic_financial_report_korean",
            split="train",
            trust_remote_code=True,
        )
    except Exception as e:
        print(f"[oss] 금융보고서 로드 실패: {e}", file=sys.stderr)
        return []

    print(f"[oss]   전체 보고서: {len(ds):,}건")

    sample = ds[0]
    print(f"[oss]   필드: {list(sample.keys())}")

    # 기술 관련 섹터 필터
    tech_sectors = ["반도체", "이차전지", "배터리", "화학", "바이오", "제약",
                    "디스플레이", "소재", "에너지", "IT", "소프트웨어", "통신"]

    docs: list[dict] = []
    for row in ds:
        if len(docs) >= max_docs:
            break

        # 본문 조합
        parts = []
        for f in ["text", "content", "report", "내용", "본문", "분석", "요약"]:
            val = row.get(f, "")
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
        if not parts:
            # 모든 문자열 필드 합치기
            parts = [str(v) for v in row.values()
                     if isinstance(v, str) and len(str(v)) > 50]

        body = clean_text(" ".join(parts))

        if len(body) < min_len:
            continue

        # 기술 관련 여부
        if not any(s in body for s in tech_sectors):
            continue

        grade, conf = assign_grade(body)
        domain = infer_domain(body)

        # 제목
        title_val = ""
        for f in ["title", "제목", "기업명", "company"]:
            if row.get(f):
                title_val = str(row[f])[:80]
                break
        if not title_val:
            title_val = extract_title(body)

        docs.append({
            "synth_id": str(uuid.uuid4()),
            "title": title_val,
            "body": body[:8000],
            "target_grade": grade,
            "grade_confidence": conf,
            "domain": domain,
            "source": "금융보고서",
            "char_len": len(body),
        })

    print(f"[oss]   금융보고서 수집: {len(docs):,}건")
    return docs


# ── 쿼리 생성 ─────────────────────────────────────────────────

def build_eval_queries(docs: list[dict]) -> list[dict]:
    """본문에서 실제 문장 추출 → doc_id 기준 쿼리."""
    queries: list[dict] = []
    for doc in docs:
        body = doc["body"]
        # 문장 분리 (마침표, 줄바꿈)
        sents = re.split(r"(?<=[다했다었다였다됩니다습니다])\s+|\n", body)
        # 50자 이상, 200자 이하 문장 선택
        good = [s.strip() for s in sents
                if 50 <= len(s.strip()) <= 200]
        # 기술 키워드 포함 문장 우선
        tech_kws = ["공정", "기술", "방법", "조성", "농도", "온도", "압력",
                    "알고리즘", "특허", "영업비밀", "개발", "연구", "분석"]
        tech_sents = [s for s in good if any(kw in s for kw in tech_kws)]
        selected = (tech_sents or good)[:2]

        for sent in selected:
            queries.append({
                "text": sent,
                "expected_grade": doc["target_grade"],
                "expected_doc_id": doc["synth_id"],
                "source_doc_title": doc["title"],
                "source": doc["source"],
            })
    return queries


# ── 메인 ─────────────────────────────────────────────────────

def _write_snapshot(docs: list[dict], out_dir: Path) -> None:
    """필터 통과한 원본 행을 raw 스냅샷으로 저장.

    폐쇄망 재현성·감리 증빙 요건 (manifest D7/D8의 auto_collect=true 연동).
    저장 위치: datasets/raw/D7_oss_precedents/ 및 D8_oss_financial_reports/
    각 파일: {synth_id}_raw.json (원본 body + 메타 전체)
    """
    raw_base = out_dir.parent.parent / "raw"
    buckets: dict[str, Path] = {
        "판례": raw_base / "D7_oss_precedents",
        "금융보고서": raw_base / "D8_oss_financial_reports",
    }
    for bucket in buckets.values():
        bucket.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for doc in docs:
        src = doc.get("source", "기타")
        bucket = buckets.get(src, raw_base / "D9_oss_other")
        bucket.mkdir(parents=True, exist_ok=True)
        fpath = bucket / f"{doc['synth_id']}_raw.json"
        fpath.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        counts[src] = counts.get(src, 0) + 1

    # 스냅샷 manifest 기록 (수집일 + 건수)
    import datetime
    snap_meta = {
        "snapshot_at": datetime.datetime.utcnow().isoformat() + "Z",
        "counts": counts,
        "total": sum(counts.values()),
    }
    (raw_base / "oss_snapshot_meta.json").write_text(
        json.dumps(snap_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[oss] raw snapshot: {snap_meta}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="datasets/oss_corpus")
    ap.add_argument("--queries-out", default="datasets/oss_eval_queries.json")
    ap.add_argument("--target", type=int, default=3000)
    ap.add_argument("--min-confidence", type=float, default=0.60,
                    help="M3 라벨러 신뢰도 최소값 (미달 시 제외)")
    ap.add_argument("--snapshot-raw", action="store_true",
                    help="필터 통과 원본을 datasets/raw/D7·D8 에 스냅샷 저장 (재현성·감리 증빙)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    target = args.target
    all_docs: list[dict] = []

    # ── 티어별 수집 ────────────────────────────────────────────
    for min_len in [3000, 2000, 1000]:
        remaining = target - len(all_docs)
        if remaining <= 0:
            break

        print(f"\n[oss] === {min_len:,}자 이상 티어 (목표 {remaining:,}건 추가) ===")

        # 각 소스에서 절반씩 (균형)
        half = remaining // 2 + 1

        tier_docs: list[dict] = []
        tier_docs += load_precedents(min_len=min_len, max_docs=half)
        tier_docs += load_financial_reports(min_len=min_len, max_docs=half)

        # 신뢰도 필터
        tier_docs = [d for d in tier_docs
                     if d["grade_confidence"] >= args.min_confidence]

        # 중복 제거 (body 앞 100자 기준)
        seen_bodies: set[str] = {d["body"][:100] for d in all_docs}
        tier_docs = [d for d in tier_docs
                     if d["body"][:100] not in seen_bodies]

        # 등급 균형 (각 등급 최대 remaining//4 * 1.5)
        grade_cap = int(remaining / 4 * 1.5)
        grade_count: dict[str, int] = {}
        balanced: list[dict] = []
        for d in sorted(tier_docs, key=lambda x: -x["char_len"]):
            g = d["target_grade"]
            if grade_count.get(g, 0) >= grade_cap:
                continue
            balanced.append(d)
            grade_count[g] = grade_count.get(g, 0) + 1
            if len(balanced) >= remaining:
                break

        print(f"[oss]   티어 {min_len:,}자: {len(balanced):,}건 확보")
        all_docs += balanced

        # 현황
        dist: dict[str, int] = {}
        for d in all_docs:
            dist[d["target_grade"]] = dist.get(d["target_grade"], 0) + 1
        print(f"[oss]   누적 {len(all_docs):,}건 | 등급분포: {dist}")

    # ── 저장 ──────────────────────────────────────────────────
    print(f"\n[oss] === 저장 ===")
    for doc in all_docs:
        save = {k: v for k, v in doc.items()
                if k not in ("grade_confidence", "char_len")}
        fpath = out_dir / f"{doc['synth_id']}.json"
        fpath.write_text(
            json.dumps(save, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── 쿼리 ──────────────────────────────────────────────────
    queries = build_eval_queries(all_docs)
    qpath = Path(args.queries_out)
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text(
        json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── 최종 리포트 ───────────────────────────────────────────
    print(f"\n[oss] ==================== 완료 ====================")
    print(f"[oss] 문서: {len(all_docs):,}건 / 목표 {target:,}건")
    print(f"[oss] 쿼리: {len(queries):,}건")

    dist: dict[str, int] = {}
    src_dist: dict[str, int] = {}
    len_dist = {"3000+": 0, "2000-3000": 0, "1000-2000": 0}
    for d in all_docs:
        dist[d["target_grade"]] = dist.get(d["target_grade"], 0) + 1
        src_dist[d["source"]] = src_dist.get(d["source"], 0) + 1
        cl = d["char_len"]
        if cl >= 3000:
            len_dist["3000+"] += 1
        elif cl >= 2000:
            len_dist["2000-3000"] += 1
        else:
            len_dist["1000-2000"] += 1

    print(f"[oss] 등급 분포: {dist}")
    print(f"[oss] 소스 분포: {src_dist}")
    print(f"[oss] 길이 분포: {len_dist}")
    print(f"[oss] 출력: {out_dir}")

    if len(all_docs) < target:
        print(f"\n[oss] WARNING: {target - len(all_docs):,}건 부족")
        print("[oss]   → 소스 데이터 필터링 후 수량 미달. 현재 수집분으로 진행 권장.")

    # ── 원본 스냅샷 (--snapshot-raw) ──────────────────────────────
    if args.snapshot_raw:
        print(f"\n[oss] === 원본 스냅샷 저장 (--snapshot-raw) ===")
        _write_snapshot(all_docs, out_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
