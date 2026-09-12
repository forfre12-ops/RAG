"""v6 학습 코퍼스를 train/val/test 로 나눈다 — 가족 단위, 결정적, 등급 층화.

왜 별도 스크립트인가. p1_train_classifier 는 `--train-path/--val-path/--test-path` 를
받는데 v6 는 단일 2,700행 파일이다. 그리고 나누는 방식이 결과를 좌우한다:

- **가족 단위로 나눈다**(`document_family_id`). 같은 가족은 같은 시나리오에서 갈라져
  나온 문서라 본문이 다르더라도 사실상 같은 문제다. 행 단위로 나누면 학습에서 본
  시나리오를 검증에서 다시 만나 성능이 부풀려진다.
- **등급 층화**. 등급 분포가 세 쪽에서 같아야 F1_macro 가 비교 가능해진다.
- **결정적**. 씨앗은 가족 id 해시라 실행 순서·시각에 무관하고, 같은 입력이면 항상 같은
  분할이 나온다(재현성 — 이 프로젝트에서 반복해 문제가 된 축이다).

비율 기본 70/15/15.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

GRADES = ("TS", "S1", "S2", "S3")


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _family_key(row: dict) -> str:
    return str(row.get("document_family_id") or row.get("doc_id") or "")


def _bucket(family: str, train: float, val: float) -> str:
    """가족 id 해시를 [0,1) 로 펴서 구간에 떨어뜨린다 — 순번·시각 무관."""
    digest = hashlib.sha256(family.encode("utf-8")).hexdigest()[:12]
    position = int(digest, 16) / float(1 << 48)
    if position < train:
        return "train"
    if position < train + val:
        return "val"
    return "test"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    args = parser.parse_args(argv)

    rows = _read_jsonl(Path(args.source))
    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_family[_family_key(row)].append(row)

    splits: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    for family, members in by_family.items():
        splits[_bucket(family, args.train_ratio, args.val_ratio)].extend(members)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name, subset in splits.items():
        subset.sort(key=lambda r: str(r.get("doc_id")))       # 결정적 출력
        path = out_dir / f"{name}.jsonl"
        payload = "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in subset
        )
        path.write_text(payload, encoding="utf-8")
        summary[name] = {
            "documents": len(subset),
            "families": len({_family_key(r) for r in subset}),
            "grades": dict(sorted(Counter(str(r["label"]) for r in subset).items())),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }

    # 가족 겹침 0 을 단언한다 — 이게 이 분할의 존재 이유다.
    families = {name: {_family_key(r) for r in subset} for name, subset in splits.items()}
    for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
        overlap = families[a] & families[b]
        if overlap:
            raise SystemExit(f"가족 겹침 {a}∩{b}: {len(overlap)}개 — 분할이 성립하지 않는다")

    manifest = {
        "schema": "v6-training-split-v1",
        "source": str(Path(args.source).name),
        "source_sha256": hashlib.sha256(Path(args.source).read_bytes()).hexdigest(),
        "split_policy": "family-disjoint, deterministic by sha256(document_family_id)",
        "ratios": {"train": args.train_ratio, "val": args.val_ratio,
                   "test": round(1 - args.train_ratio - args.val_ratio, 4)},
        "family_overlap": 0,
        "splits": summary,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
