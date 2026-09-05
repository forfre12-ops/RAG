# -*- coding: utf-8 -*-
"""[W6] 검수 승인 합성 샘플(DB) → 학습셋 jsonl. noop_fallback/llm_nonjson 배제.

generate → queue → review → **train** 루프의 마지막 칸. 지재원 관리자가 검수 큐
(tb_sample_documents)에서 승인한 합성 문서를, 본문 출처가 정상인 것만 골라 학습 행으로
방출한다(합성 placeholder·비-JSON 노이즈의 학습 유입 차단 — P0#1이 보존한 label_source 소비).

스냅샷 시맨틱: 승인 전체에서 매번 재빌드(결정적). 출력 jsonl은 build_labeled_dataset.py
--extra 로 그대로 합류 가능한 {doc_id,text,label,source,domain} 스키마.

[2026-09-05] 검사 두 가지를 여기서 돈다. 생성 시점 배치 게이트
(synth_quality.screen_batch)는 **요청 한 건**만 재고, 24건 미만이면 코퍼스 지표를 아예
계산하지 않는다. 그래서 20건씩 나눠 요청하면 게이트는 매번 "표본 부족"으로 통과시키고
합쳐진 학습 코퍼스는 검사받은 적이 없다(실측: 리포 학습셋 23개가 그 상태다).

  · corpus_leakage         합쳐진 학습셋 전체의 길이 누출·등급 전용 문장. 항상 인쇄한다.
  · holdout_independence   --holdout 로 준 평가셋과 같은 문서·문장을 공유하는가.
                           이 축은 짝이 있어야 잴 수 있어 인자로 받는다. 미지정이면
                           "미검사"라고 적는다 — 키를 빼면 통과로 읽힌다.

--strict 를 주면 둘 중 하나라도 걸릴 때 exit 1.

사용:
  python scripts/build_synth_training_set.py --out datasets/synth_approved/train.jsonl

  python scripts/build_synth_training_set.py       --out datasets/synth_approved/train.jsonl       --holdout datasets/gold_real/holdout_eval.hardened.jsonl       --report reports/synth_training_set.json --strict
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="검수 승인 합성 샘플 → 학습셋 jsonl(노이즈 마커 배제)"
    )
    ap.add_argument(
        "--out",
        default="datasets/synth_approved/train.jsonl",
        help="출력 jsonl 경로(디렉토리 자동 생성)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="최대 행 수(기본=전량). 스모크 확인용.",
    )
    ap.add_argument(
        "--stamp",
        action="store_true",
        help=(
            "방출한 판 이름을 연결 표(tb_sample_dataset_membership)에 append-only 로 "
            "기록한다. '이 문서가 어느 셋들에 들어갔나'를 되짚기 위한 것이며 학습을 "
            "돌리지는 않는다. 칼럼 되쓰기가 아니라 행 추가라 재방출해도 앞선 판이 남는다."
        ),
    )
    ap.add_argument(
        "--holdout",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "이 학습셋과 **계보 독립인지 확인할 홀드아웃** jsonl. 여러 번 줄 수 있다. "
            "생성 시점 배치 게이트는 배치 안만 보므로 '학습셋과 평가셋이 같은 문서·문장을 "
            "공유하는가'는 여기서만 잡힌다. 미지정이면 검사하지 않고, 그 사실을 요약에 적는다."
        ),
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help=(
            "코퍼스 누출이 임계를 넘거나 홀드아웃과 계보 독립이 아니면 exit 1. "
            "파이프라인이 조용히 지나가지 않게 한다(report_holdout_independence.py 와 같은 규약)."
        ),
    )
    ap.add_argument(
        "--report",
        default=None,
        metavar="PATH",
        help="요약 JSON 을 파일로도 남긴다(표준출력은 그대로).",
    )
    args = ap.parse_args(argv)

    # 표준 스크립트 관례: src 를 import 경로에 추가(standalone 실행 지원).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from koipa.services.synthesis_service import SynthesisService

    result = SynthesisService().build_training_rows(
        limit=args.limit, stamp_version=args.stamp,
    )
    rows = result["rows"]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ascii-safe 요약(cp949 콘솔 안전). 무음 드롭 금지 — 사유별 카운트 노출.
    summary = {
        "out": str(out),
        # 판 이름 = 내용 해시. 같은 승인 집합이면 같은 값 — 시각이 아니라 내용으로 가른다.
        "dataset_version": result["dataset_version"],
        "stamped": bool(args.stamp),
        "approved_total": result["approved_total"],
        "included": result["included"],
        "excluded_noise": result["excluded_noise"],
        "excluded_empty": result["excluded_empty"],
        # [2026-09-05] 누출 게이트를 **못 돌린** 배치의 건수. 어제 이 카운트를 만들어
        # 놓고 여기서 인쇄하지 않아, "게이트가 몇 번 깨졌나"를 세는 유일한 값이
        # 계산만 되고 버려지고 있었다. 0 이 아니면 게이트가 깨진 적이 있다는 뜻이다.
        "excluded_gate_error": result.get("excluded_gate_error", 0),
        "grade_corrected": result["grade_corrected"],
    }

    problems: list[str] = []

    # ── 코퍼스 누출 — 합쳐진 학습셋을 통째로 잰다 ────────────────────────────
    # 생성 시점 배치 게이트(synth_quality.screen_batch)는 요청 한 건만 보고, 24건 미만
    # 배치는 코퍼스 지표를 아예 계산하지 않는다. 20건씩 나눠 요청하면 매번 "표본 부족"으로
    # 통과하고 합집합은 검사받은 적이 없다.
    from koipa.dataset_leakage import DEFAULT_MAX_LENGTH_LEAK, DEFAULT_MAX_TELL_COVERAGE

    leak = result.get("corpus_leakage") or {}
    summary["corpus_leakage"] = {
        "documents": leak.get("documents", 0),
        "length_only_1nn": leak.get("length_only_1nn"),
        "length_theils_u": leak.get("length_theils_u"),
        "tell_coverage": leak.get("tell_coverage"),
        "grade_token_exposed": leak.get("grade_token_exposed"),
    }
    if leak.get("error"):
        # 재지 못한 것을 통과로 읽지 않는다.
        summary["corpus_leakage"]["error"] = leak["error"]
        problems.append("corpus_leakage: 계량 실패 — %s" % leak["error"])
    elif leak.get("documents"):
        if float(leak.get("length_only_1nn") or 0.0) > DEFAULT_MAX_LENGTH_LEAK:
            problems.append(
                "corpus_leakage: 길이만으로 등급 %.3f 적중 > 임계 %.2f"
                % (leak["length_only_1nn"], DEFAULT_MAX_LENGTH_LEAK)
            )
        if float(leak.get("tell_coverage") or 0.0) > DEFAULT_MAX_TELL_COVERAGE:
            problems.append(
                "corpus_leakage: 등급 전용 문장이 문서 %.1f%% 에 존재 > 임계 %.0f%%"
                % (leak["tell_coverage"] * 100, DEFAULT_MAX_TELL_COVERAGE * 100)
            )

    # ── 홀드아웃 계보 독립 — 학습셋과 평가셋이 같은 문서·문장을 공유하는가 ──────
    # 이 축은 한쪽 셋만 봐서는 절대 잡히지 않는다. 배치 게이트도, 위 코퍼스 계량도
    # 학습셋 안만 본다. 짝을 줘야 잴 수 있어 인자로 받는다.
    if args.holdout:
        from koipa.holdout_independence import assess

        summary["holdout_independence"] = {}
        for hp in args.holdout:
            p = Path(hp)
            if not p.exists():
                summary["holdout_independence"][hp] = {"error": "파일 없음"}
                problems.append("holdout %s: 파일 없음" % hp)
                continue
            with io.open(p, "r", encoding="utf-8") as f:
                hrows = [json.loads(x) for x in f if x.strip()]
            rep = assess(rows, hrows, label=p.name)
            ss = rep["overlap"]["shared_sentences"]
            summary["holdout_independence"][hp] = {
                "holdout_documents": rep["holdout_documents"],
                "lineage_independent": rep["lineage_independent"],
                "usable_for_comparison": rep["usable_for_comparison"],
                "shared_document_text": rep["overlap"]["document_text"]["shared"],
                "shared_sentence_coverage": ss["coverage"],
                "concerns": rep["concerns"],
            }
            if not rep["usable_for_comparison"]:
                problems.append(
                    "holdout %s: 비교에 쓸 수 없다 — %s"
                    % (hp, "; ".join(rep["concerns"]) or "계보 독립 아님")
                )
    else:
        # **검사하지 않았다는 사실을 적는다.** 키를 빼면 "통과"로 읽힌다.
        summary["holdout_independence"] = "미검사 — --holdout 미지정"

    summary["problems"] = problems
    print(json.dumps(summary, ensure_ascii=False))

    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        with io.open(rp, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    if problems and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
