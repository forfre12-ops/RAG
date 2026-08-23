"""Build a blinded comparison pack for curated versus existing high-grade text."""

from __future__ import annotations

import json
from pathlib import Path
import random


SEED = "curated-vs-existing-quality-pilot-v1"
GRADES = ("TS", "S1", "S2")


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    curated_path = root / "datasets/proxy_gold/curated_master/pilot_30.v3.jsonl"
    existing_path = root / "datasets/gold_real/classification_gold.jsonl"
    curated = [json.loads(line) for line in curated_path.read_text(encoding="utf-8").splitlines()]
    existing = [json.loads(line) for line in existing_path.read_text(encoding="utf-8").splitlines()]
    selected: list[tuple[str, dict]] = []
    for grade in GRADES:
        authored = [row for row in curated if row["label"] == grade]
        historic = sorted(
            (row for row in existing if row["label"] == grade),
            key=lambda row: str(row["doc_id"]),
        )[:10]
        if len(authored) != 10 or len(historic) != 10:
            raise ValueError(f"insufficient comparison sample for {grade}")
        selected.extend(("curated", row) for row in authored)
        selected.extend(("existing", row) for row in historic)
    random.Random(SEED).shuffle(selected)

    review_rows: list[dict] = []
    key_rows: list[dict] = []
    for index, (kind, row) in enumerate(selected, 1):
        review_id = f"BQ{index:03d}"
        review_rows.append(
            {
                "review_id": review_id,
                "document_type": row.get("document_type", "미상"),
                "text": row["text"],
                "rating_scale": "각 항목 1(매우 부족)~5(매우 좋음)",
                "questions": [
                    "업무 문서로서 자연스럽고 완결적인가?",
                    "사실·수치·일정·통제 근거가 서로 일관적인가?",
                    "제시된 등급 판단에 필요한 근거를 검수자가 확인할 수 있는가?",
                    "검수 후 실제 학습·평가 후보로 검토할 가치가 있는가?",
                ],
            }
        )
        key_rows.append(
            {
                "review_id": review_id,
                "hidden_population": kind,
                "original_doc_id": row["doc_id"],
                "label": row["label"],
                "label_source": row.get("label_source"),
                "authoring_method": row.get("authoring_method"),
                "text_chars": len(row["text"]),
            }
        )
    out = root / "datasets/proxy_gold/blind_quality_pilot"
    _write_new(
        out / "review_pack.v1.jsonl",
        b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
            for row in review_rows
        ),
    )
    _write_new(
        out / "blind_key.v1.json",
        (json.dumps({"seed": SEED, "records": key_rows}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    _write_new(
        out / "REVIEW_INSTRUCTIONS.v1.md",
        (
            "# 블라인드 문서 품질 평가 안내\n\n"
            "`review_pack.v1.jsonl`만 검수자에게 제공하십시오. `blind_key.v1.json`은 결과 집계 전까지 제공하지 마십시오.\n\n"
            "각 문서에 대해 네 질문을 각각 1~5점으로 평가하고, 사실 불일치·근거 부족·반복 문구가 있으면 자유 의견에 기록하십시오. "
            "문서의 출처나 등급을 추정하려 하지 말고 본문만 평가하십시오.\n"
        ).encode("utf-8"),
    )
    print(json.dumps({"records": len(review_rows), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
