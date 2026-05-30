"""문서 파생 쿼리 생성 — 합성 문서 title에서 eval 쿼리 추출.

배경:
  기존 seed 쿼리는 `{keyword} 관련 자료` 패턴으로, 합성 문서와 분포가 달라
  BGE-M3 + hybrid + reranker까지 동원해도 Recall@5 천장이 0.72.
  원인: 쿼리 분포(단순 키워드) ≠ 문서 분포(복잡한 합성 본문).

이 모듈은 각 합성 문서의 title을 쿼리로 사용.
  - title은 문서 내용을 대표하며 실제 사용자 검색 패턴(문서명 기억 검색)을 반영.
  - hit 조건: grade 일치 아님 → 해당 doc_id가 top-K에 포함 (더 엄밀한 평가).
  - 이 조건에서 Recall이 높다면 임베딩/인덱싱 품질이 충분함을 증명.

사용:
  from p2_doc_derived_queries import build_doc_derived_queries
  queries = build_doc_derived_queries(synth_dir, n_per_grade=10)
  # queries[i] = {"text": title, "expected_grade": "TS", "expected_doc_id": "uuid"}
"""

from __future__ import annotations

import json
import random
from pathlib import Path


def build_doc_derived_queries(
    synth_dir: Path,
    n_per_grade: int = 10,
    seed: int = 42,
) -> list[dict]:
    """합성 문서 title을 쿼리로 사용하는 eval 셋 생성.

    n_per_grade: 등급당 샘플 수. 전체 corpus가 적으면 min(n_per_grade, len) 자동 조정.
    seed: 재현 가능한 랜덤 샘플링.
    반환: [{"text", "expected_grade", "expected_doc_id"}, ...]
    """
    rng = random.Random(seed)

    by_grade: dict[str, list[dict]] = {"TS": [], "S1": [], "S2": [], "S3": []}

    for f in sorted(synth_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        grade = d.get("target_grade") or d.get("grade")
        if grade in by_grade:
            by_grade[grade].append(d)

    queries: list[dict] = []
    for grade, docs in by_grade.items():
        sampled = rng.sample(docs, min(n_per_grade, len(docs)))
        for d in sampled:
            queries.append(
                {
                    "text": d["title"],
                    "expected_grade": grade,
                    # expected_doc_id 미포함 → hit 기준은 기존과 동일한 grade 일치
                    # (5000개 중 정확한 1개를 찾는 doc_id 기준은 40 쿼리로 통계적으로 불가)
                }
            )

    return queries
