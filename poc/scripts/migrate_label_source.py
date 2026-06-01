"""
기존 JSONL 데이터셋에 label_source 필드 일괄 추가.

source 필드 값 기준으로 label_source를 추론:
  synthetic       → "synthetic_llm"  (구 generator, 키워드 주입 방식)
  oss             → "oss_corpus"     (공개 법령/판례 코퍼스)
  rag_corpus_v2   → "synthetic_llm"
  qa_augment      → "synthetic_llm"
  기타/없음       → "unknown"

이미 label_source가 있는 레코드는 건너뜀.

사용:
  python scripts/migrate_label_source.py
  python scripts/migrate_label_source.py --path datasets/labeled_v2_balanced/train.jsonl
"""
import argparse
import json
from pathlib import Path

SOURCE_TO_LABEL_SOURCE = {
    "synthetic": "synthetic_llm",
    "oss": "oss_corpus",
    "rag_corpus_v2": "synthetic_llm",
    "qa_augment": "synthetic_llm",
    "test_set_v2": "curated",
}


def migrate(path: Path) -> tuple[int, int]:
    """(updated, skipped) 반환."""
    lines = path.read_text(encoding="utf-8").splitlines()
    updated, skipped = 0, 0
    out_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("label_source"):
            skipped += 1
            out_lines.append(json.dumps(rec, ensure_ascii=False))
            continue
        src = rec.get("source", "")
        rec["label_source"] = SOURCE_TO_LABEL_SOURCE.get(src, "unknown")
        updated += 1
        out_lines.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return updated, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", nargs="+",
                    default=[
                        "datasets/labeled_v2_balanced/train.jsonl",
                        "datasets/labeled_oss_v1/train.jsonl",
                        "datasets/labeled_oss_v1/val.jsonl",
                        "datasets/labeled_oss_v1/test.jsonl",
                    ])
    args = ap.parse_args()

    for p_str in args.path:
        p = Path(p_str)
        if not p.exists():
            print(f"[SKIP] {p} 없음")
            continue
        updated, skipped = migrate(p)
        print(f"[OK] {p}: updated={updated}, skipped(already set)={skipped}")


if __name__ == "__main__":
    main()
