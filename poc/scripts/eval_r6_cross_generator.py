#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""6차(작성 모델이 다른 독립 시험 문서)에 학습된 FS·FSO 모델을 그대로 적용해 작성 모델별 정확도를 잰다.
사전 등록: reports/CLAUDE_DOCGEN_R6_20260921/PREREG_R6.md — 6차 문서는 학습에 쓰지 않는다.
입력: R6 폴더의 pilot_docs_checked.jsonl · pilot_judge_rows.json(블라인드 판정=명세 일치 ok) · WRITER_MODELS_PRIVATE.json,
      학습 모델 reports/mock_final_train_20260921/model_{FS_s42,FS_s43,FS_s44,FSO_s42,FSO_s43}
출력: reports/mock_final_train_20260921/r6_cross_generator_result.txt · r6_preds_{arm}_s{seed}.json
사용:  python scripts/eval_r6_cross_generator.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import G, POC, load_jsonl, prf, wilson  # noqa: E402
from run_mock1000_pilotmix import _pipe, _predict  # noqa: E402

R6 = POC / "reports" / "CLAUDE_DOCGEN_R6_20260921"
OUT = POC / "reports" / "mock_final_train_20260921"
ARMS = [("FS", 42), ("FS", 43), ("FS", 44), ("FSO", 42), ("FSO", 43), ("FS50", 42), ("FS50", 43), ("FS25", 42), ("FS25", 43), ("FS50E20", 42), ("FS50E20", 43), ("FS25E40", 42), ("FS25E40", 43), ("FS7", 42), ("FS7", 43), ("FS7", 44), ("FS9", 42), ("FS9", 43), ("FS9", 44)]
DEV_FS_ACC = 0.833          # 개발·표현 시험 154건 FS 시드 3개 평균 (final_train_result_20260921.txt)
RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}


def verified_docs() -> tuple[list[dict], dict]:
    docs = load_jsonl(R6 / "pilot_docs_checked.jsonl")
    ok = {r["doc_key"] for r in json.loads((R6 / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    fam_model = json.loads((R6 / "WRITER_MODELS_PRIVATE.json").read_text(encoding="utf-8"))["family_model"]
    rows = [{"doc_id": d["doc_key"], "text": d["text"], "label": d["grade"], "family": d["family_id"], "writer": fam_model[d["family_id"]]} for d in docs if d["doc_key"] in ok]
    return rows, fam_model


def fmt(name: str, m: dict) -> str:
    lo, hi = wilson(round(m["acc"] * m["n"]), m["n"])
    return (f"  {name:<34} n={m['n']:<4} 정확도 {m['acc']:.1%}({lo:.0%}~{hi:.0%}) · macro F1 {m['macroF1']:.1%} · 고등급 미탐 {m['hi_miss']}/{m['hi_n']} = "
            f"{m['hi_miss'] / max(1, m['hi_n']):.1%} · 재현율 TS/S1/S2/S3 " + "/".join(f"{m['per'][g]['R']:.0%}" for g in G))


def main() -> int:
    rows, _ = verified_docs()
    L = [f"6차 독립 시험(작성 모델이 다른 문서) · 검증 통과 {len(rows)}건 · 작성 모델별 {dict(Counter(r['writer'] for r in rows))} · 등급 {dict(Counter(r['label'] for r in rows))} · τ=0.30 · 학습에 안 씀"]
    res = defaultdict(dict)
    for arm, seed in ARMS:
        f = OUT / f"r6_preds_{arm}_s{seed}.json"
        if f.exists():
            preds = json.loads(f.read_text(encoding="utf-8"))
        else:
            root = OUT / f"model_{arm}_s{seed}"
            if not list(root.glob("v-*/model.safetensors")):
                L.append(f"  (모델 없음: {arm} 시드 {seed})")
                continue
            preds = _predict(_pipe(root), rows)
            wmap = {r["doc_id"]: r["writer"] for r in rows}
            for p in preds:
                p["writer"] = wmap[p["doc_id"]]
            f.write_text(json.dumps(preds, ensure_ascii=False), encoding="utf-8")
        res[arm][seed] = preds
    L += ["", "[풀 전체]"]
    for arm in ("FS", "FSO", "FS50", "FS25", "FS50E20", "FS25E40", "FS7", "FS9"):
        for seed, preds in sorted(res[arm].items()):
            L.append(fmt(f"{arm} 시드 {seed}", prf(preds, lambda r: r["pred"])))
    L += ["", "[작성 모델별 — FS 시드 평균 정확도 / 시드별]"]
    writers = sorted({r["writer"] for r in rows})
    by_writer_fs = {}
    for w in writers:
        accs = []
        for seed, preds in sorted(res["FS"].items()):
            sub = [p for p in preds if p["writer"] == w]
            if sub:
                m = prf(sub, lambda r: r["pred"])
                accs.append(m["acc"])
                L.append(fmt(f"FS 시드 {seed} · 작성 {w}", m))
        if accs:
            by_writer_fs[w] = sum(accs) / len(accs)
    for w in writers:
        for seed, preds in sorted(res["FSO"].items()):
            sub = [p for p in preds if p["writer"] == w]
            if sub:
                L.append(fmt(f"FSO 시드 {seed} · 작성 {w}", prf(sub, lambda r: r["pred"])))
    # 기준선: 글자 n-gram TF-IDF (FS 학습분으로 학습, 5차와 같은 설정) + 학습 라벨 섞기
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    tr = load_jsonl(OUT / "fs_train.jsonl")
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
    X = vec.fit_transform([d["text"] for d in tr])
    Xt = vec.transform([r["text"] for r in rows])
    y = np.array([d["label"] for d in tr])
    yt = np.array([r["label"] for r in rows])
    p = LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(X, y).predict(Xt)
    tf_acc = float(np.mean(p == yt))
    rng = np.random.default_rng(0)
    sh = [float(np.mean(LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(X, rng.permutation(y)).predict(Xt) == yt)) for _ in range(30)]
    L += ["", "[기준선]", f"  글자 n-gram TF-IDF(FS 학습분 579건) → 6차: 정확도 {tf_acc:.1%} · 작성 모델별 " + " · ".join(f"{w} {float(np.mean(p[[r['writer'] == w for r in rows]] == yt[[r['writer'] == w for r in rows]])):.0%}" for w in writers),
          f"  학습 라벨 섞기 30회 평균 {np.mean(sh):.1%} (범위 {min(sh):.1%}~{max(sh):.1%})"]
    # 판정 (사전 등록)
    fs_accs = [prf(pr, lambda r: r["pred"])["acc"] for pr in res["FS"].values()]
    L += ["", "[사전 등록 판정]"]
    if fs_accs:
        a = sum(fs_accs) / len(fs_accs)
        d1 = ("작성 습관 의존 제한적" if a >= 0.75 else "부분 의존" if a >= 0.65 else "작성 습관 의존 큼")
        if abs(a - 0.75) <= 0.03 or abs(a - 0.65) <= 0.03:
            d1 += " — 단 경계 ±3pt 안이라 판정 보류"
        L.append(f"  D1 FS {len(fs_accs)}시드 평균 정확도 {a:.1%} (개발·표현 시험 {DEV_FS_ACC:.1%} 대비 {100 * (a - DEV_FS_ACC):+.1f}pt) → {d1}")
        d2 = 100 * (a - tf_acc)
        L.append(f"  D2 FS − TF-IDF = {d2:+.1f}pt → " + ("표면 n-gram 을 넘어 읽는다(≥ +10pt)" if d2 >= 10 else "n-gram 수준(< +10pt)"))
    if by_writer_fs:
        L.append("  D3 작성 모델별 FS 평균 정확도 " + " · ".join(f"{w} {v:.1%}" for w, v in by_writer_fs.items()) + f" (최고−최저 {100 * (max(by_writer_fs.values()) - min(by_writer_fs.values())):.1f}pt)")
    # 오류 방향
    if res["FS"]:
        conf = Counter()
        for preds in res["FS"].values():
            for q in preds:
                conf[(q["label"], q["pred"])] += 1
        L += ["", "[FS 3시드 합산 혼동(정답→예측 TS/S1/S2/S3)]"] + [f"  {a} " + str([conf[(a, b)] for b in G]) for a in G]
    text = "\n".join(L)
    (OUT / "r6_cross_generator_result.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
