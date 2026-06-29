"""C/Task3 글루 — 앵커 코퍼스를 실서빙으로 통과시켜 평가 카드 dict를 산출(재사용 가능).

eval_anchor_cards.py(CLI)와 training_service(배포 게이트 앵커 연결)가 **같은 측정 경로**를
공유하도록 모델-실행 글루를 패키지에 둔다. 순수 부분(anchor_corpus·eval_cards·deploy_gate)은
그대로 두고, 여기서만 InferencePipeline을 (선택) 생성한다.

반환 dict는 build_eval_cards.to_dict()의 "report" + 코퍼스 요약/표본 캡 메타를 담는다 —
그대로 deploy_gate.evaluate_deploy_gate(anchor_report=...) 에 넣을 수 있다.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Optional, Sequence

from lloydk.modules.m6_evaluation.anchor_corpus import (
    AnchorRecord,
    AnchorSource,
    corpus_summary,
    load_anchor_corpus,
)
from lloydk.modules.m6_evaluation.eval_cards import DEFAULT_MIN_N, build_eval_cards
from lloydk.modules.m6_evaluation.serving_eval import predict_via_serving

LABELS = ("TS", "S1", "S2", "S3")


def cap_per_cell(
    records: Sequence[AnchorRecord],
    cap: int,
    *,
    slice_of: Callable[[AnchorRecord], str] = lambda r: r.source,
    grade_of: Callable[[AnchorRecord], str] = lambda r: r.anchor_grade,
) -> tuple[list[AnchorRecord], dict]:
    """(slice,grade) 칸마다 최대 cap건으로 **결정적** 절단(anchor_id 정렬 앞 cap건).

    절단 통계를 함께 반환 — 호출부가 '무엇을 얼마나 잘랐는지' 남긴다(조용한 캡 금지).
    cap<=0이면 절단 없음.
    """
    if cap <= 0:
        return list(records), {}
    groups: dict[tuple[str, str], list[AnchorRecord]] = defaultdict(list)
    for r in records:
        groups[(slice_of(r), grade_of(r))].append(r)
    kept: list[AnchorRecord] = []
    dropped: dict[str, int] = {}
    for key, rs in groups.items():
        rs_sorted = sorted(rs, key=lambda r: r.anchor_id)
        kept.extend(rs_sorted[:cap])
        if len(rs_sorted) > cap:
            dropped[f"{key[0]}/{key[1]}"] = len(rs_sorted) - cap
    return kept, dropped


def run_anchor_cards(
    *,
    model_dir: Optional[str] = None,
    pipeline: Optional[Any] = None,
    sources: Optional[Sequence[AnchorSource]] = None,
    root: Optional[str] = None,
    cap_per_cell_n: int = 0,
    min_n: int = DEFAULT_MIN_N,
) -> dict:
    """앵커 코퍼스 → 실서빙 예측 → 출처×등급 카드 dict.

    Args:
        model_dir: 미주입 시 InferencePipeline(model_dir) 생성. pipeline 주입 시 우선.
        pipeline:  테스트/재사용용 주입(예측만 필요하면 fake도 가능).
        sources/root: anchor_corpus.load_anchor_corpus 인자(기본=저장소 로컬 앵커).
        cap_per_cell_n: (출처×등급) 칸당 표본 상한(CPU 비용 제어). 0=전수.
        min_n: 카드 INCONCLUSIVE 판정 최소표본.

    Returns dict:
        corpus_summary / evaluated / serving_failed / cap_per_cell / dropped_by_cap / report.
        report = EvalCardReport.to_dict() — deploy_gate(anchor_report=...) 에 그대로 투입 가능.
    """
    records = (
        load_anchor_corpus(sources, root=root) if sources is not None
        else load_anchor_corpus(root=root) if root is not None
        else load_anchor_corpus()
    )
    summary = corpus_summary(records)
    sampled, dropped = cap_per_cell(records, cap_per_cell_n)

    rows = [r.to_card_record() for r in sampled]
    preds = predict_via_serving(
        rows, pipeline=pipeline, model_dir=model_dir, labels=list(LABELS),
    )
    report = build_eval_cards(
        preds,
        slice_of=lambda r: r.get("source", "?"),
        true_of=lambda r: r.get("label"),
        pred_of=lambda r: r.get("pred"),
        min_n=min_n,
    )
    return {
        "model_dir": model_dir,
        "corpus_summary": summary,
        "evaluated": len(preds),
        "serving_failed": sum(1 for p in preds if p.get("serving_failed")),
        "cap_per_cell": cap_per_cell_n,
        "dropped_by_cap": dropped,
        "report": report.to_dict(),
    }
