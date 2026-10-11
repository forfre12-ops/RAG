#!/usr/bin/env python
"""Materialize four-way classifier scores for the active human-review batch.

The review corpus is never modified.  This command runs the same serving
preprocessing and inference pipeline used for a direct document submission,
then atomically writes a hash-bound JSONL snapshot beside the candidates.  The
review API serves that snapshot as reference material only; it does not invoke
a model while a reviewer is looking at a document.

Example (from ``poc``)::

    python scripts/score_proxy_gold_review_candidates.py \
      --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b

The default batch is the active 1,000-document RF expert-review delivery.  A
different batch must explicitly supply both ``--review-batch`` and its expected
count so a partial or unintended set cannot silently replace the snapshot.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))

DEFAULT_BATCH = "expert_review_product_1000_20261010_rf"
DEFAULT_CANDIDATE_ROOT = _POC / "datasets" / "proxy_gold" / "single_document_candidates"
GRADE_CODES = ("TS", "S1", "S2", "S3")


def _label_code(value: object) -> str:
    return str(getattr(value, "value", value))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="전수 사람 검수용 모의문서의 TS/S1/S2/S3 참고 점수 스냅샷을 생성합니다.")
    parser.add_argument("--model-dir", required=True,
                        help="실제 분류기 아티팩트 디렉터리 (rule fallback은 허용하지 않음)")
    parser.add_argument("--candidates-root", default=str(DEFAULT_CANDIDATE_ROOT),
                        help="후보 메타·본문 및 score snapshot 디렉터리")
    parser.add_argument("--review-batch", default=DEFAULT_BATCH,
                        help=f"대상 review_batch (기본: {DEFAULT_BATCH})")
    parser.add_argument("--expected-count", type=int, default=1000,
                        help="대상 수가 정확히 이 값일 때만 기록 (기본: 1000)")
    parser.add_argument("--profile", default="onprem-local",
                        help="서빙 설정 프로파일 (기본: onprem-local)")
    parser.add_argument("--device", choices=("cpu", "auto", "cuda"), default="auto",
                        help="전수 산출에 쓸 추론 장치 (기본 auto: 사용 가능한 GPU, 없으면 CPU)")
    parser.add_argument("--dry-run", action="store_true",
                        help="분류와 검증만 수행하고 스냅샷은 교체하지 않음")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    model_dir = Path(args.model_dir).resolve()
    candidates_root = Path(args.candidates_root).resolve()
    if not model_dir.is_dir():
        raise SystemExit(f"분류기 모델 디렉터리가 없습니다: {model_dir}")
    if not candidates_root.is_dir():
        raise SystemExit(f"후보 디렉터리가 없습니다: {candidates_root}")
    if args.expected_count <= 0:
        raise SystemExit("--expected-count 는 1 이상이어야 합니다")

    # Config는 import 시 환경을 읽는다. 명령행 모델·프로필을 먼저 고정해, 실행 결과가
    # 개발자의 우연한 쉘 환경에 따라 달라지지 않게 한다.
    os.environ["CLASSIFIER_MODEL_DIR"] = str(model_dir)
    os.environ["DEPLOY_PROFILE"] = args.profile
    os.environ["CLASSIFIER_DEVICE"] = args.device
    # 이 배치는 후보 파일과 로컬 모델만으로 재현 가능해야 한다. 후보 doc_id는 DB에 적재된
    # UUID가 아니므로 DB 레지스트리를 읽어도 추가 신호가 없고, DB가 꺼진 개발 PC에서는
    # 연결 시간초과만 1,000번 반복하게 된다. TESTING은 여기서 *오프라인 레지스트리 폴백*
    # 용도이며, API 서버의 운영 설정이나 사람 검수 결정을 바꾸지 않는다.
    os.environ["TESTING"] = "1"

    from koipa.modules.m2_preprocess.pipeline import PreprocessPipeline  # noqa: PLC0415
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415
    from koipa.services.candidate_classifier_scores import (  # noqa: PLC0415
        SCORE_SCHEMA_VERSION, write_score_snapshot,
    )
    from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService  # noqa: PLC0415

    pipeline = InferencePipeline(model_dir=model_dir)
    if getattr(pipeline, "_model", None) is None:
        raise SystemExit("실제 분류기 모델을 로드하지 못했습니다. rule fallback 스냅샷은 기록하지 않습니다.")
    preprocessor = PreprocessPipeline()
    service = ProxyGoldCandidateService(root=candidates_root)
    candidates = [
        row for row in service._candidates()  # noqa: SLF001 - batch materialization needs the source text and hash.
        if row.get("review_batch") == args.review_batch
    ]
    if len(candidates) != args.expected_count:
        raise SystemExit(
            f"대상 건수가 다릅니다: batch={args.review_batch!r} actual={len(candidates)} "
            f"expected={args.expected_count}; 기존 snapshot을 건드리지 않습니다."
        )

    scored_at = datetime.now(UTC).isoformat()
    records: list[dict] = []
    started = time.perf_counter()
    model_versions: set[str] = set()
    score_top_differs_from_pipeline_label = 0
    for index, candidate in enumerate(candidates, start=1):
        cleaned = preprocessor.run_text(str(candidate["text"]))
        result = pipeline.run(cleaned, metadata=None, return_evidence=False)
        if set(result.scores) != set(GRADE_CODES):
            raise RuntimeError(
                f"{candidate['doc_id']}: 네 등급 전체 점수가 아닙니다: {sorted(result.scores)}"
            )
        scores = {grade: float(result.scores[grade]) for grade in GRADE_CODES}
        pipeline_label = _label_code(result.label)
        if pipeline_label not in GRADE_CODES:
            raise RuntimeError(f"{candidate['doc_id']}: 알 수 없는 파이프라인 등급 {pipeline_label!r}")
        # 파이프라인의 최종 등급은 FNR-safe 룰·출처 게이트로 점수 최상위와 달라질 수 있다.
        # 이 파일은 **네 막대 점수**를 위한 스냅샷이므로 predicted_label 은 언제나 막대의
        # 최상위 레이블이다. 최종 등급은 사람 검수 UI에 노출하지 않고, 별도 감사/판정 경로가
        # 계속 소유한다.
        predicted_label = max(scores, key=scores.get)
        if pipeline_label != predicted_label:
            score_top_differs_from_pipeline_label += 1
        model_version = str(result.model_version)
        if not model_version or model_version.startswith("rule-fallback"):
            raise RuntimeError(f"{candidate['doc_id']}: rule fallback 결과는 기록할 수 없습니다")
        model_versions.add(model_version)
        records.append({
            "schema_version": SCORE_SCHEMA_VERSION,
            "doc_id": candidate["doc_id"],
            "document_sha256": candidate["document_sha256"],
            "model_version": model_version,
            "scored_at": scored_at,
            "score_basis": "serving_pipeline",
            "predicted_label": predicted_label,
            "scores": scores,
            "calibrated": bool(getattr(pipeline, "calibrated", False)),
        })
        if index % 25 == 0 or index == len(candidates):
            print(f"  {index}/{len(candidates)} · {time.perf_counter() - started:.0f}s", flush=True)

    if len(model_versions) != 1:
        raise RuntimeError(f"한 실행에서 모델 버전이 섞였습니다: {sorted(model_versions)}")
    if args.dry_run:
        print(
            f"[dry-run] {len(records)}건 검증 완료 · model={next(iter(model_versions))} · "
            f"score-top/final-label 상이 {score_top_differs_from_pipeline_label}건 · snapshot은 기록하지 않았습니다.",
            flush=True,
        )
        return 0
    target = write_score_snapshot(candidates_root, records)
    print(
        f"[완료] {len(records)}건 score snapshot 기록: {target} · "
        f"model={next(iter(model_versions))} · score-top/final-label 상이 "
        f"{score_top_differs_from_pipeline_label}건 · elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
