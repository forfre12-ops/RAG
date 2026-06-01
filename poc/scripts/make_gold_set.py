"""
test_set_v2/ 144개 JSON → datasets/gold/classification_gold.jsonl 변환.

Gold set 규칙:
  - 학습(train/val)에 절대 사용 금지
  - 합성 generator 프롬프트 seed로 사용 금지
  - 룰 라벨러 키워드 seed로 사용 금지

전처리:
  1. 제목: "(1급 비밀)", "(S2 대외비)" 같은 등급 표기 제거
  2. 본문: 등급 분류 선언 문장/구절을 [등급분류] 로 마스킹
     → 룰 라벨러가 본문 키워드로 직접 정답을 맞추는 leakage 차단
     → 문서의 내용/맥락은 보존하되 분류 라벨 자체는 제거
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# 제목 패턴: "(1급 비밀)", "[TS]", "(S2 대외비)" 같은 괄호형 등급 표기
# ---------------------------------------------------------------------------
_TITLE_GRADE_PATTERN = re.compile(
    r"\s*[\(\[]?\s*"
    r"(?:특급기밀|1급\s*비밀|2급\s*비밀|3급\s*공개"
    r"|TS|S1|S2|S3"
    r"|대외비|내부용|공개|영업비밀"
    r"|극비|비밀|기밀)"
    r"\s*[\)\]]?"
    r"(?:\s*등급)?"
    r"(?=\s|$|[,.])"
)

# ---------------------------------------------------------------------------
# 본문 패턴: 등급 분류를 선언하는 구절
#   "1급 비밀로 분류된 자료입니다" → "[등급분류]"
#   "대외비로 지정된" → "[등급분류]"
#   "특급기밀 등급을 부여받은" → "[등급분류]"
#   단, "보도자료", "채용공고", "공시" 같이 S3 문서 유형 자체인 단어는 보존
# ---------------------------------------------------------------------------
_BODY_GRADE_SUBS: list[tuple[re.Pattern, str]] = [
    # 등급 코드/이름 + 분류 동사 구절
    (re.compile(
        r"(?:특급기밀|1급\s*비밀|2급\s*비밀|3급\s*공개|대외비|극비)"
        r"(?:\s*(?:등급을?|로|으로|이|자료|문서|수준))*"
        r"\s*(?:부여받은?|분류된?|지정된?|취급되는?|관리되는?|해당하는?)"
    ), "[등급분류]"),
    # "이 문서는 ○○ 등급입니다" 형태
    (re.compile(
        r"(?:본\s*문서는?|이\s*자료는?|해당\s*문서는?)"
        r"\s*(?:\[가상기업\w\]의\s*)?"
        r"(?:특급기밀|1급\s*비밀|2급\s*비밀|3급\s*공개|대외비)"
        r"\s*(?:등급|자료|문서|수준)?"
    ), "[등급분류]"),
    # "S1/S2/S3/TS 등급으로" 코드 직접 언급
    (re.compile(r"\b(?:TS|S1|S2|S3)\s*(?:등급|수준|분류)"), "[등급코드]"),
    # standalone "특급기밀", "1급 비밀" (문맥 없이 단독 출현)
    (re.compile(r"특급\s*기밀"), "[기밀자료]"),
    (re.compile(r"1급\s*비밀"), "[기밀자료]"),
    (re.compile(r"2급\s*비밀"), "[내부자료]"),
    (re.compile(r"(?<![보채])대외비"), "[내부자료]"),  # "보도대외비" 등 오탐 방지
]


def _clean_title(title: str) -> str:
    return _TITLE_GRADE_PATTERN.sub("", title).strip().rstrip(",.")


def _mask_body(body: str) -> tuple[str, int]:
    """등급 분류 선언 구절을 마스킹. (처리된 body, 마스킹 횟수) 반환."""
    count = 0
    for pat, repl in _BODY_GRADE_SUBS:
        new_body, n = pat.subn(repl, body)
        body = new_body
        count += n
    return body, count


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--src", default="datasets/test_set_v2")
    p.add_argument("--out", default="datasets/gold/classification_gold.jsonl")
    p.add_argument("--exclude", default="smoke_samples.json")
    p.add_argument("--no-mask", action="store_true",
                   help="본문 마스킹 비활성화 (leakage 수치 비교용)")
    return p.parse_args()


def main():
    args = parse_args()
    exclude = set(args.exclude.split(","))
    src_files = sorted(glob.glob(os.path.join(args.src, "*.json")))
    src_files = [f for f in src_files if os.path.basename(f) not in exclude]

    if not src_files:
        print(f"[ERROR] {args.src} 에서 JSON 파일을 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    records = []
    grade_counts: dict[str, int] = {}
    total_masked = 0

    for f in src_files:
        d = json.load(open(f, encoding="utf-8"))
        label = d.get("target_grade", "")
        raw_title = d.get("title", "")
        body = d.get("body", "")

        clean_title = _clean_title(raw_title)
        if not args.no_mask:
            clean_body, n_masked = _mask_body(body)
            total_masked += n_masked
        else:
            clean_body = body

        text = f"{clean_title}\n\n{clean_body}".strip()
        doc_id = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

        rec = {
            "doc_id": doc_id,
            "text": text,
            "label": label,
            "label_source": "curated",
            "domain": d.get("domain", ""),
            "document_type": d.get("document_type", ""),
            "source": "test_set_v2",
            "original_file": os.path.basename(f),
        }
        records.append(rec)
        grade_counts[label] = grade_counts.get(label, 0) + 1

    with open(args.out, "w", encoding="utf-8") as fout:
        for rec in records:
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")

    _write_readme(os.path.dirname(args.out), mask_applied=not args.no_mask)

    mask_info = f", 본문 마스킹 {total_masked}회" if not args.no_mask else " (마스킹 없음)"
    print(f"[OK] {len(records)}건 → {args.out}{mask_info}")
    print("     등급별 분포:", dict(sorted(grade_counts.items())))


def _write_readme(gold_dir: str, mask_applied: bool = True) -> None:
    path = os.path.join(gold_dir, "READONLY.md")
    mask_note = (
        "- 제목: 괄호형 등급 표기 제거 (`(1급 비밀)`, `[TS]` 등)\n"
        "- 본문: 등급 분류 선언 구절을 `[등급분류]`, `[기밀자료]` 등으로 마스킹\n"
        "  → 룰 라벨러가 등급 키워드로 직접 정답을 맞추는 leakage 차단\n"
        "  → 문서의 내용/맥락은 보존"
    ) if mask_applied else "- 마스킹 미적용 (--no-mask 옵션)"

    content = f"""\
# Gold Evaluation Set — 읽기 전용

## 규칙 (위반 금지)

1. **학습 데이터로 사용 금지** — train/val에 포함하지 않는다
2. **합성 generator 프롬프트 seed로 사용 금지**
3. **룰 라벨러 키워드 seed로 사용 금지**
4. **이 디렉토리 파일 수정은 반드시 `make_gold_set.py` 또는 명시적 수작업 검수 후 진행**

## 출처 및 전처리

- 원본: `datasets/test_set_v2/` (144개 JSON, 6 domains × 4 grades × 6 docs)
- `label_source: "curated"` — rule_labeler / synthetic_llm과 구분
- 전처리:
{mask_note}

## 한계

- 원본(test_set_v2)이 구 generator(키워드 주입 방식)로 생성됨
- 마스킹으로 명시적 등급 선언은 제거했으나, 문서 내용 자체가 등급을 암시함
- 진짜 독립 gold set은 generator V2 + 수작업 검수로 별도 구축 필요
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    main()
