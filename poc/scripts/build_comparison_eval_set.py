#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""**비교 전용** 평가셋을 만든다 — 지름길(길이·문구·문서종류)을 통제한 4등급 셋.

■ 무엇을 푸는가

감리 지적 넷이 같은 하나에 막혀 있다: 고친 것이 나아졌는지 **잴 자가 없다.**

    골든셋↔학습셋 혼용(177) · 라벨 기준 오류(185) · 지름길 편향(185) · 일치율 35.71%(193)

기존 홀드아웃 4종은 길이만으로 등급이 갈린다(hardened42 는 등급별 길이 중앙값이 **17.18배**,
길이 구간만 보고 61.9% 적중 = 기준선 대비 +28.6%p). 후보 풀 1,055건은 길이는 중립(배수 1.03)
이지만 **되풀이 문구 하나로 91.4%** 가 맞는다. 둘 다 "모델이 나아졌다"를 증명하지 못한다.

■ 무엇을 만드는가 — 그리고 무엇이 아닌가

⛔ **정확도를 재는 셋이 아니다.** 정답이 생성 시 의도 등급이고 **사람 확정 0건**이다.
⛔ **실문서 일반화를 재는 셋도 아니다** — 합성 전용이다([[synthetic-only-fails-real-generalization]]).
✅ **두 모델 중 어느 쪽이 나은지 고르는 데 쓴다.** 지름길이 통제돼 있으므로, 점수 차이를
   "길이를 더 잘 외웠다"로 설명할 수 없다. 그것이 지금 없는 능력이다.

■ 통제하는 방법

    등급 균등        등급마다 같은 건수 → 기준선이 25%로 고정된다(불균형이 점수를 부풀리지 못한다)
    길이 층화        전체를 길이 10분위로 나누고 **각 구간에서 등급마다 같은 수**를 뽑는다
                    → 길이 구간을 알아도 등급 확률이 그대로다
    문구·문서종류     고친 생성기(커밋 `aa8c8222`)가 등급 사이에 겹치게 만든 판만 쓴다
    학습 분리        학습에 쓰지 않은 배치 번호로만 만든다(기본 7·8·9)

만들고 나서 세 축을 **다시 재서 파일에 같이 적는다.** 재지 않고 "통제했다"고 하지 않는다.

사용:
    python scripts/build_comparison_eval_set.py --out datasets/eval_comparison_v1
    python scripts/build_comparison_eval_set.py --per-grade 120 --batches 7 8 9 --dry
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import random
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

GRADES = ("TS", "S1", "S2", "S3")


def _render(batches: list[int]) -> list[dict]:
    """고친 생성기로 문서를 찍어낸다 — 파일로 쓰지 않고 메모리에서."""
    import build_proxy_gold_batch_300 as b
    from build_proxy_gold_pilot_100 import (
        _case_specific_appendix,
        _contextualize_standard_sentences,
        _grade_rationale,
        _repair_mojibake,
    )
    from golden_scaffolding import clean_body

    rows: list[dict] = []
    for batch in batches:
        serials: collections.Counter = collections.Counter()
        for case in b.make_cases(batch):
            serials[case.grade] += 1
            body = _repair_mojibake(_contextualize_standard_sentences(
                _case_specific_appendix(case).strip() + "\n"
                + _grade_rationale(case).strip() + "\n",
                case,
            ))
            # 정답이 본문에 적히는 '등급 제안 사유' 절을 걷는다 — 세척 없이는 100% 가 샌다.
            cleaned = clean_body(body)
            rows.append({
                "doc_id": f"EVALC-B{batch}-{case.grade}-{serials[case.grade]:03d}",
                "text": cleaned,
                "label": case.grade,
                "document_type": case.kind,
                "origin": "synthetic",
                "label_source": "generator_intended_label",
                "human_confirmed": False,
            })
    return rows


def stratified_pick(rows: list[dict], per_grade: int, bins: int, seed: int) -> list[dict]:
    """길이 10분위 구간마다 등급별로 같은 수를 뽑는다 — 길이가 등급을 알려주지 못하게."""
    rng = random.Random(seed)
    ordered = sorted(rows, key=lambda r: len(r["text"]))
    per_bin_per_grade, remainder = divmod(per_grade, bins)
    size = len(ordered) / bins
    picked: list[dict] = []
    shortfall: collections.Counter = collections.Counter()
    for i in range(bins):
        chunk = ordered[int(i * size):int((i + 1) * size)]
        by_grade: dict[str, list[dict]] = collections.defaultdict(list)
        for row in chunk:
            by_grade[row["label"]].append(row)
        want = per_bin_per_grade + (1 if i < remainder else 0)
        for grade in GRADES:
            pool = by_grade.get(grade, [])
            rng.shuffle(pool)
            picked.extend(pool[:want])
            if len(pool) < want:
                shortfall[grade] += want - len(pool)
    if shortfall:
        print("  ⚠ 구간에서 못 채운 몫:", dict(shortfall),
              "— 그 등급은 목표보다 적게 담긴다(부풀리지 않는다)")
    return picked


def audit_set(rows: list[dict]) -> dict:
    """만든 셋의 지름길 세 축을 다시 잰다."""
    from measure_eval_source_inventory import length_leak
    from measure_grade_phrase_leak import sentences

    pairs = [(len(r["text"]), r["label"]) for r in rows]
    length = length_leak(pairs)

    n = len(rows)
    counts = collections.Counter(r["label"] for r in rows)
    base = max(counts.values()) / n if n else 0.0

    by_sentence: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in rows:
        for s in sentences(row["text"], 12):
            by_sentence[s][row["label"]] += 1
    best: dict[str, tuple[float, str]] = {}
    for sentence, dist in by_sentence.items():
        if sum(dist.values()) < 20:
            continue
        grade, cnt = dist.most_common(1)[0]
        purity = cnt / sum(dist.values())
        for row_id in (sentence,):  # noqa: B007
            pass
        best[sentence] = (purity, grade)
    hit = 0
    for row in rows:
        cand = [best[s] for s in sentences(row["text"], 12) if s in best]
        if cand and max(cand)[1] == row["label"]:
            hit += 1
    phrase_rate = hit / n * 100 if n else 0.0

    type_hit: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in rows:
        type_hit[row["document_type"]][row["label"]] += 1
    doctype = sum(c.most_common(1)[0][1] for c in type_hit.values()) / n * 100 if n else 0.0

    return {
        "n": n,
        "by_grade": dict(counts),
        "baseline_rate": round(base * 100, 1),
        "length": {k: length.get(k) for k in
                   ("median_chars", "median_ratio", "length_only_rate", "leak_pp")},
        "phrase_single_clue_rate": round(phrase_rate, 1),
        "document_type_rate": round(doctype, 1),
        "document_type_categories": len(type_hit),
    }


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="비교 전용 평가셋 생성")
    ap.add_argument("--out", default="datasets/eval_comparison_v1")
    ap.add_argument("--per-grade", type=int, default=100)
    ap.add_argument("--batches", type=int, nargs="+", default=[7, 8, 9])
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20260912)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    print("생성기로 찍어내는 중 — 배치 %s" % a.batches)
    rendered = _render(a.batches)
    print("  원재료 %d건 · 등급별 %s"
          % (len(rendered), dict(collections.Counter(r["label"] for r in rendered))))

    picked = stratified_pick(rendered, a.per_grade, a.bins, a.seed)
    report = audit_set(picked)

    print("\n== 만든 셋의 지름길 세 축 (기준선 %.1f%%) ==" % report["baseline_rate"])
    print("  길이   중앙값 배수 %s · 길이만으로 %s%% (누설 %s%%p)"
          % (report["length"]["median_ratio"], report["length"]["length_only_rate"],
             report["length"]["leak_pp"]))
    print("  문구   되풀이 문장 하나로 %.1f%%" % report["phrase_single_clue_rate"])
    print("  종류   문서종류만으로 %.1f%% (범주 %d개)"
          % (report["document_type_rate"], report["document_type_categories"]))
    print("  건수   %d · 등급별 %s" % (report["n"], report["by_grade"]))

    if a.dry:
        print("\n  --dry — 아무것도 쓰지 않았다.")
        return 0

    out_dir = _POC / a.out
    out_dir.mkdir(parents=True, exist_ok=True)
    eval_path = out_dir / "eval.jsonl"
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in picked)
    eval_path.write_text(body, encoding="utf-8")

    # 입력이 git 밖이 아니어도 지문을 남긴다 — 같은 질문에 다른 답이 나오는 일을 막는다.
    manifest = {
        "built_at": "2026-09-12",
        "generator_commit_note": "aa8c8222 이후 판(등급 사이 겹치는 문구·문서종류) + 05a2728a(조사)",
        "batches": a.batches, "per_grade": a.per_grade, "bins": a.bins, "seed": a.seed,
        "sha256_eval_jsonl": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "label_source": "generator_intended_label",
        "human_confirmed_count": 0,
        "use": "모델 A/B 비교 전용. 정확도·실문서 일반화 근거로 쓰지 말 것.",
        "shortcut_audit": report,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  기록: %s (%d건) · %s"
          % (eval_path.relative_to(_POC), len(picked), (out_dir / "manifest.json").name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
