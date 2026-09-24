#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""학습량 곡선 — 새 모의문서 1,000건 5분할에서 학습 문서를 25%(180건)·50%(360건)로 줄여 다시 학습하고, 같은 평가 200건×5 로 잰다.

왜: 사전 품질시험 점수(macro F1 61.9~66.3%)가 낮은 이유가 (가) 학습량 부족 (나) 라벨 오류·모호 (다) 문서에서 등급 근거 읽기의 어려움 중 무엇인지 가른다.
    학습량을 줄였을 때 점수가 뚜렷이 떨어지면 학습량이 제약이고, 720건과 차이가 없으면 더 모아도 이 조건에서는 오르지 않는다는 뜻이다.
설계(측정 전 고정 — reports/mock1000_cv_20260921/prereg_curve.json)
  · 평가 문서·분할·검증 80건은 주 시험과 같다. 학습만 분할별 학습 720건에서 등급별 층화·중첩 부분집합(180 ⊂ 360 ⊂ 720)으로 뽑는다.
  · 학습 = 주 시험과 같은 레시피(p1_train_classifier.py --mode full --epochs 5 --seed 42) — 에폭 고정이라 작은 학습셋은 갱신 횟수도 적다(한계).
  · 평가 = pipe.run + τ=0.30, 주 시험 결과 표와 같은 지표. 720건 값은 주 시험(시드 42: macro F1 65.2%, 시드 범위 61.9~66.3%)을 그대로 쓴다.
  · 판정 규칙(측정 전): F1(720) − F1(360) ≥ 5pt 면 학습량이 뚜렷한 제약 · ≤ 2pt 면 이 범위에서는 학습량이 병목이 아님 · 그 사이는 판정 유보(시드 추가).
사용:  python scripts/run_mock1000_curve.py prepare | fold K PCT | tfidf | aggregate
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import G, K, RANK, TAU, WORK, POC, load_jsonl, prf, sha256_file  # noqa: E402

PCTS = (25, 50)


def prepare() -> int:
    info = {}
    for k in range(K):
        d = WORK / f"fold{k}"
        tr = load_jsonl(d / "train.jsonl")
        rng = random.Random(20260921 + k)
        by = {g: [r for r in tr if r["label"] == g] for g in G}
        for g in G:
            rng.shuffle(by[g])
        for pct in PCTS:
            sub = [r for g in G for r in by[g][: round(len(by[g]) * pct / 100)]]
            with (d / f"train_p{pct}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
                for r in sub:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            info[f"fold{k}_p{pct}"] = {"n": len(sub), "by_label": {g: sum(1 for r in sub if r["label"] == g) for g in G}}
    prereg = {"purpose": "학습량 곡선 — 낮은 점수의 원인 가르기(학습량 vs 그 밖)", "fractions_of_train_720": list(PCTS),
              "subset": "분할별·등급별 층화, 중첩(25%⊂50%⊂100%)", "training": "p1_train_classifier.py --mode full --epochs 5 --seed 42 (주 시험과 동일, 에폭 고정)",
              "eval": "주 시험과 같은 평가 200건×5, pipe.run τ=0.30", "reference_720": "주 시험 시드 42 (macro F1 65.2%), 시드 43/44 포함 범위 61.9~66.3%",
              "decision_rule": "F1(720)-F1(360)>=5pt 학습량 뚜렷한 제약 · <=2pt 학습량이 병목 아님 · 사이는 판정 유보(시드 추가)",
              "main_prereg_sha256": sha256_file(WORK / "prereg.json"), "subset_sizes": info}
    (WORK / "prereg_curve.json").write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("prereg_curve sha256", sha256_file(WORK / "prereg_curve.json"))
    print({k: v["n"] for k, v in info.items()})
    return 0


def fold(k: int, pct: int) -> int:
    d = WORK / f"fold{k}"
    out = d / f"model_p{pct}"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TESTING="1")
    cmd = [sys.executable, str(POC / "scripts" / "p1_train_classifier.py"), "--mode", "full", "--epochs", "5", "--seed", "42",
           "--train-path", str(d / f"train_p{pct}.jsonl"), "--val-path", str(d / "val.jsonl"), "--test-path", str(d / "test.jsonl"),
           "--output-dir", str(out), "--no-mlflow"]
    if not list(out.glob("v-*/model.safetensors")):
        with (d / f"train_p{pct}.log").open("w", encoding="utf-8") as lg:
            rc = subprocess.run(cmd, cwd=str(POC), env=env, stdout=lg, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            print(f"fold{k} p{pct} 학습 실패 exit={rc}")
            return rc
    mdir = sorted(out.glob("v-*"))[-1]
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415

    settings.classifier_escalation_tau = TAU
    pipe = InferencePipeline(model_dir=str(mdir))
    preds = []
    for r in load_jsonl(d / "test.jsonl"):
        res = pipe.run(r["text"], metadata=None)
        code = res.label.value if hasattr(res.label, "value") else str(res.label)
        preds.append({"doc_id": r["doc_id"], "label": r["label"], "pred": code})
    (d / f"preds_p{pct}.json").write_text(json.dumps(preds, ensure_ascii=False), encoding="utf-8")
    print(f"fold{k} p{pct} 완료: 학습 {sum(1 for _ in open(d / f'train_p{pct}.jsonl', encoding='utf-8'))}건 · 평가 {len(preds)}건", flush=True)
    return 0


def tfidf() -> int:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    for k in range(K):
        d = WORK / f"fold{k}"
        te = load_jsonl(d / "test.jsonl")
        for pct, name in ((25, "train_p25"), (50, "train_p50"), (100, "train")):
            tr = load_jsonl(d / f"{name}.jsonl")
            vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
            clf = LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(vec.fit_transform([r["text"] for r in tr]), [r["label"] for r in tr])
            pred = clf.predict(vec.transform([r["text"] for r in te]))
            (d / f"preds_tfidf_p{pct}.json").write_text(json.dumps(
                [{"doc_id": r["doc_id"], "label": r["label"], "pred": p} for r, p in zip(te, pred)], ensure_ascii=False), encoding="utf-8")
    print("TF-IDF 곡선 완료")
    return 0


def aggregate() -> int:
    def pool(fmt: str) -> list[dict]:
        return [r for k in range(K) for r in json.loads((WORK / f"fold{k}" / fmt).read_text(encoding="utf-8"))]

    L = ["학습량 곡선 — 평가 200건×5 = 1,000건 고정 · τ=0.30 · 학습 시드 42(720건 열은 주 시험값)", "  학습건수/분할  모델 macro P/R/F1        정확도   고등급 미탐        TF-IDF(글자 n-gram) macro F1"]
    f1s = {}
    for pct, n, fm, tm in ((25, 180, "preds_p25.json", "preds_tfidf_p25.json"), (50, 360, "preds_p50.json", "preds_tfidf_p50.json"),
                           (100, 720, "preds.json", "preds_tfidf_p100.json")):
        m = prf(pool(fm), lambda r: r["pred"])
        t = prf(pool(tm), lambda r: r["pred"])
        f1s[n] = m["macroF1"]
        L.append(f"  {n:>4}건 ({pct:>3}%)   {m['macroP']:.1%}/{m['macroR']:.1%}/{m['macroF1']:.1%}   {m['acc']:.1%}   {m['hi_miss']}/{m['hi_n']} = {m['hi_miss'] / m['hi_n']:.1%}   {t['macroF1']:.1%}")
        L.append("        등급별 F1 " + " ".join(f"{g} {m['per'][g]['F1']:.1%}" for g in G))
    gap = (f1s[720] - f1s[360]) * 100
    verdict = "학습량이 뚜렷한 제약" if gap >= 5 else ("이 범위에서는 학습량이 병목이 아님" if gap <= 2 else "판정 유보(2~5pt) — 시드 추가 필요")
    L += ["", f"F1(720) − F1(360) = {gap:+.1f}pt → 사전 규칙 판정: {verdict}  (주 시험의 시드 변동 범위: macro F1 61.9~66.3%)"]
    text = "\n".join(L)
    (WORK / "curve_result_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prepare", "fold", "tfidf", "aggregate"])
    ap.add_argument("k", nargs="?", type=int)
    ap.add_argument("pct", nargs="?", type=int)
    a = ap.parse_args()
    return {"prepare": prepare, "tfidf": tfidf, "aggregate": aggregate}.get(a.cmd, lambda: fold(a.k, a.pct))()


if __name__ == "__main__":
    sys.exit(main())
