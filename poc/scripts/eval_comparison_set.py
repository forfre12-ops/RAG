#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""비교 전용 평가셋으로 모델을 잰다 — 두 모델 중 어느 쪽이 나은지 고르는 데 쓴다.

셋은 `scripts/build_comparison_eval_set.py` 가 만든다(지름길 세 축을 통제하고, 통제 결과를
manifest 에 같이 적는다). 이 도구는 그 셋으로 서빙 파이프라인을 그대로 돌린다.

⛔ 나오는 숫자를 **정확도로 인용하지 말 것.** 정답이 생성 시 의도 등급이고 사람 확정 0건이다.
   여기서 쓸 수 있는 것은 **같은 자로 잰 모델 사이의 차이**다.
⛔ 실문서 일반화 근거도 아니다 — 합성 전용이다.

내는 값:
    일치율          의도 등급과 같은 비율(기준선 25% — 등급이 균등하다)
    미탐률(방향성)   고등급 정답을 **더 낮은 등급**으로 예측한 비율. RFP 핵심 지표와 같은 방향
    등급별 일치      TS/S1/S2/S3 각각
    무음 미탐        미탐인데 검수로도 안 가는 건수(자동확정 문턱 기준)

사용:
    python scripts/eval_comparison_set.py artifacts/classifier_p1_v5_clean/v-fe4b386b
    python scripts/eval_comparison_set.py <모델A> <모델B>        # 두 모델을 한 번에 비교
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import sys
import time
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

GRADES = ("TS", "S1", "S2", "S3")
SEVERITY = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}


def training_style(model_dir: Path, rows: list[dict], max_len: int = 512) -> dict:
    """**학습기가 재던 방식**으로 잰다 — 본문을 max_len 에서 잘라 한 번 통과시킨다.

    왜 따로 재는가(2026-09-13 실측). 같은 모델·같은 589건인데 학습 보고서는 97.8%,
    서빙 경로는 84.9% 였다. 13점 차이는 **재는 방식**에서 온다:

        학습기   본문을 512토큰에서 자르고 한 번 통과 → argmax
        서빙     창을 나눠 각각 통과 → 길이 가중 평균 → 보정 → argmax

    그 셋의 **55.9%가 512토큰을 넘었다**(중앙값 518). 모델은 잘린 앞부분만 보고 배웠는데
    서빙은 뒷부분 점수까지 평균에 섞는다. 기존 평가셋도 같다 — holdout109 56.0% ·
    labeled_p1_v5_clean/test 46.9% 가 창을 넘는다.

    ⇒ **학습 보고서의 F1 은 배포 시스템이 내는 값이 아니다.** 둘을 함께 내지 않으면
       어느 쪽을 인용했는지 알 수 없다. 그래서 이 도구는 두 값을 나란히 낸다.
    """
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(model_dir))
    mdl = AutoModelForSequenceClassification.from_pretrained(str(model_dir)).eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    mdl = mdl.to(device)
    id2label = {int(k): v for k, v in mdl.config.id2label.items()}

    hit = over = 0
    with torch.no_grad():
        for start in range(0, len(rows), 16):
            batch = rows[start:start + 16]
            texts = [r["text"] for r in batch]
            over += sum(1 for t in texts
                        if len(tok(t, add_special_tokens=False)["input_ids"]) > max_len - 2)
            enc = tok(texts, truncation=True, max_length=max_len,
                      padding=True, return_tensors="pt").to(device)
            for row, pred in zip(batch, mdl(**enc).logits.argmax(-1).tolist()):
                hit += (id2label[pred] == row["label"])
    return {
        "agreement": round(hit / len(rows), 4),
        "over_window": over,
        "over_window_pct": round(over / len(rows) * 100, 1),
    }


def evaluate(model_dir: Path, rows: list[dict], auto_confirm_threshold: float) -> dict:
    from eval_p1_model_gold import predict_api_like

    started = time.perf_counter()
    preds = predict_api_like(model_dir, rows)
    elapsed = time.perf_counter() - started

    agree = 0
    per_grade: dict[str, list[int]] = {g: [0, 0] for g in GRADES}
    high_n = under = silent = 0
    confusion: collections.Counter = collections.Counter()
    for row, pred in zip(rows, preds):
        truth, got = row["label"], pred["label"]
        confusion[(truth, got)] += 1
        per_grade[truth][1] += 1
        if truth == got:
            agree += 1
            per_grade[truth][0] += 1
        if SEVERITY[truth] > 0:  # 공개(S3)가 아닌 것만 미탐 대상
            high_n += 1
            if SEVERITY.get(got, 0) < SEVERITY[truth]:
                under += 1
                if (pred.get("confidence") or 0.0) >= auto_confirm_threshold:
                    silent += 1

    return {
        "model": model_dir.name,
        "n": len(rows),
        "seconds": round(elapsed, 1),
        "agreement": round(agree / len(rows), 4),
        "per_grade": {g: f"{ok}/{tot}" for g, (ok, tot) in per_grade.items()},
        "underclass_fnr": round(under / high_n, 4) if high_n else None,
        "underclass_n": f"{under}/{high_n}",
        "silent_miss": silent,
        "confusion": {f"{t}->{p}": c for (t, p), c in sorted(confusion.items())},
    }


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="비교 전용 평가셋으로 모델 재기")
    ap.add_argument("models", nargs="+", help="모델 디렉터리(하나 이상)")
    ap.add_argument("--set", default="datasets/eval_comparison_v1")
    ap.add_argument("--json", default="")
    ap.add_argument("--no-training-style", action="store_true",
                    help="학습 방식 대조를 건너뛴다(빠르게 볼 때만)")
    a = ap.parse_args(argv)

    base = _POC / a.set if not Path(a.set).is_absolute() else Path(a.set)
    # 셋마다 파일 이름이 다르다(비교셋은 eval.jsonl · 생성 코퍼스는 test.jsonl).
    # 이름을 하나로 강제하면 같은 자로 못 재게 된다 — 있는 쪽을 쓴다.
    for name in ("eval.jsonl", "test.jsonl", "val.jsonl"):
        path = base / name
        if path.is_file():
            break
    else:
        raise SystemExit(f"{base}: eval.jsonl·test.jsonl·val.jsonl 중 아무것도 없다")
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    print("파일: %s" % path.name)

    from koipa.config import settings
    threshold = float(getattr(settings, "review_confidence_threshold", 0.5))

    # 지문 필드가 셋마다 다르다 — 하나로 강제하지 않고 있는 쪽을 쓴다.
    digest = manifest.get("sha256_eval_jsonl") or (manifest.get("sha256") or {}).get(path.stem, "")
    print("셋: %s · %d건 · sha256 앞16 %s" % (a.set, len(rows), str(digest)[:16] or "(없음)"))

    audit = manifest.get("shortcut_audit") or {}
    if "axes" in audit:
        # 새 형식 — 축마다 실측/라벨섞음/차이. **차이**로 읽는다.
        print("  통제 확인(실측 → 라벨섞음 = 차이):")
        for name, row in audit["axes"].items():
            print("    %-14s %5.1f%% → %5.1f%% = %+.1f%%p"
                  % (name, row["rate"], row["null"], row["excess_pp"]))
        baseline = audit.get("majority_rate", 25.0)
    elif audit:
        # 옛 형식 — 최빈등급 비율과 비교한 값이라 **부풀려져 있다**
        # ([[permutation-baseline-not-majority-2026-09-12]]). 그대로 믿지 말 것.
        print("  ⚠ 옛 형식 통제 수치(최빈등급 기준 — 부풀려져 있다): 문서종류 %.1f%% · 문구 %.1f%%"
              % (audit.get("document_type_rate", 0), audit.get("phrase_single_clue_rate", 0)))
        baseline = audit.get("baseline_rate", 25.0)
    else:
        baseline = 25.0
    print("  ⛔ 정답은 규칙·생성기가 매긴 라벨이다(사람 확정 0건) — 정확도로 인용하지 말 것\n")

    results = []
    for name in a.models:
        model_dir = Path(name) if Path(name).is_absolute() else _POC / name
        rep = evaluate(model_dir, rows, threshold)
        results.append(rep)
        print("== %s  (%.1f초)" % (rep["model"], rep["seconds"]))
        print("   일치율      %.1f%%   (기준선 %.1f%%)"
              % (rep["agreement"] * 100, baseline))
        print("   미탐률      %.1f%%   %s  (고등급 정답을 더 낮게)"
              % ((rep["underclass_fnr"] or 0) * 100, rep["underclass_n"]))
        print("   무음 미탐   %d건    (미탐인데 conf ≥ %.2f 라 검수로도 안 감)"
              % (rep["silent_miss"], threshold))
        print("   등급별      %s" % rep["per_grade"])
        if not a.no_training_style:
            ts = training_style(model_dir, rows)
            rep["training_style"] = ts
            print("   ⭐학습 방식  %.1f%%   (512에서 잘라 한 번 — 창 초과 %d건 %.1f%%)"
                  % (ts["agreement"] * 100, ts["over_window"], ts["over_window_pct"]))
            print("     → 서빙과 차이 %+.1f%%p. **학습 보고서 F1 은 배포 시스템 값이 아니다**"
                  % ((ts["agreement"] - rep["agreement"]) * 100))
        print()

    if len(results) > 1:
        first, last = results[0], results[-1]
        print("== 차이 (%s → %s)" % (first["model"], last["model"]))
        print("   일치율  %+.1f%%p · 미탐률 %+.1f%%p · 무음 미탐 %+d건"
              % ((last["agreement"] - first["agreement"]) * 100,
                 ((last["underclass_fnr"] or 0) - (first["underclass_fnr"] or 0)) * 100,
                 last["silent_miss"] - first["silent_miss"]))

    if a.json:
        target = _POC / a.json
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(
            {"set": a.set, "sha256": digest, "results": results},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n기록: %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
