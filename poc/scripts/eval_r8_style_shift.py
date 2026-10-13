#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""8차(문체·양식 변형 독립 시험 문서)에 학습된 FS·FS7 모델을 그대로 적용한다. 사전 등록: reports/CLAUDE_DOCGEN_R8_20260921/PREREG_R8.md — 8차 문서는 학습에 쓰지 않는다.
입력: R8 폴더 pilot_docs_checked.jsonl · pilot_judge_rows.json(판정=명세 ok) · specs_pilot.json · WRITER_MODELS_PRIVATE.json, 모델 reports/mock_final_train_20260921/model_{FS,FS7,FSO}_s*
출력: reports/mock_final_train_20260921/r8_style_shift_result.txt · r8_preds_{arm}_s{seed}.json
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

R8 = POC / "reports" / "CLAUDE_DOCGEN_R8_20260921"
OUT = POC / "reports" / "mock_final_train_20260921"
ARMS = [("FS", 42), ("FS", 43), ("FS", 44), ("FS7", 42), ("FS7", 43), ("FS7", 44), ("FSO", 42), ("FSO", 43), ("FS9", 42), ("FS9", 43), ("FS9", 44)]


def verified() -> list[dict]:
    docs = load_jsonl(R8 / "pilot_docs_checked.jsonl")
    ok = {r["doc_key"] for r in json.loads((R8 / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    specs = {s["doc_key"]: s for s in json.loads((R8 / "specs_pilot.json").read_text(encoding="utf-8"))}
    fm = json.loads((R8 / "WRITER_MODELS_PRIVATE.json").read_text(encoding="utf-8"))["family_model"]
    return [{"doc_id": d["doc_key"], "text": d["text"], "label": d["grade"], "family": d["family_id"], "writer": fm[d["family_id"]], "form": d["form"],
             "n_implicit": len(specs[d["doc_key"]]["implicit_axes"]), "cell": (d["S"], d["V"], d["M"])} for d in docs if d["doc_key"] in ok]


def fmt(name: str, m: dict) -> str:
    lo, hi = wilson(round(m["acc"] * m["n"]), m["n"])
    return (f"  {name:<30} n={m['n']:<4} 정확도 {m['acc']:.1%}({lo:.0%}~{hi:.0%}) · macro F1 {m['macroF1']:.1%} · 고등급 미탐 {m['hi_miss']}/{m['hi_n']} = "
            f"{m['hi_miss'] / max(1, m['hi_n']):.1%} · 재현율 TS/S1/S2/S3 " + "/".join(f"{m['per'][g]['R']:.0%}" for g in G))


def main() -> int:
    rows = verified()
    L = [f"8차 문체·양식 변형 시험 · 검증 통과 {len(rows)}건 · 작성 모델 {dict(Counter(r['writer'] for r in rows))} · 등급 {dict(Counter(r['label'] for r in rows))} · τ=0.30 · 학습에 안 씀"]
    res = defaultdict(dict)
    wmap = {r["doc_id"]: r for r in rows}
    for arm, seed in ARMS:
        f = OUT / f"r8_preds_{arm}_s{seed}.json"
        if f.exists():
            preds = json.loads(f.read_text(encoding="utf-8"))
        else:
            root = OUT / f"model_{arm}_s{seed}"
            if not list(root.glob("v-*/model.safetensors")):
                continue
            preds = _predict(_pipe(root), rows)
            for p in preds:
                p.update({k: wmap[p["doc_id"]][k] for k in ("writer", "form", "n_implicit", "cell")})
            f.write_text(json.dumps(preds, ensure_ascii=False), encoding="utf-8")
        res[arm][seed] = preds
    L += ["", "[전체]"]
    accs = {}
    for arm in ("FS", "FS7", "FS9", "FSO"):
        for seed, preds in sorted(res[arm].items()):
            m = prf(preds, lambda r: r["pred"])
            L.append(fmt(f"{arm} 시드 {seed}", m))
            accs.setdefault(arm, []).append(m["acc"])
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    tr = load_jsonl(OUT / "fs_train.jsonl")
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
    X = vec.fit_transform([d["text"] for d in tr])
    Xt = vec.transform([r["text"] for r in rows])
    y = np.array([d["label"] for d in tr])
    yt = np.array([r["label"] for r in rows])
    tf_acc = float(np.mean(LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(X, y).predict(Xt) == yt))
    tr7 = load_jsonl(OUT / "fs7_train.jsonl") if (OUT / "fs7_train.jsonl").exists() else tr
    vec7 = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
    X7 = vec7.fit_transform([d["text"] for d in tr7])
    tf7 = float(np.mean(LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(X7, np.array([d["label"] for d in tr7])).predict(vec7.transform([r["text"] for r in rows])) == yt))
    rng = np.random.default_rng(0)
    sh = [float(np.mean(LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(X, rng.permutation(y)).predict(Xt) == yt)) for _ in range(20)]
    L += ["", "[기준선]", f"  글자 n-gram TF-IDF: FS 학습분(579건) {tf_acc:.1%} · FS7 학습분(963건) {tf7:.1%} · 학습 라벨 섞기 20회 평균 {np.mean(sh):.1%} (범위 {min(sh):.1%}~{max(sh):.1%})"]
    L += ["", "[작성 모델별·시드 평균 정확도 / 양식군 / 암시 축 수]"]
    for arm in ("FS", "FS7"):
        for w in sorted({r["writer"] for r in rows}):
            v = [prf([p for p in pr if p["writer"] == w], lambda r: r["pred"])["acc"] for pr in res[arm].values()]
            if v:
                L.append(f"  {arm} · 작성 {w}: {sum(v) / len(v):.1%} (시드 {len(v)}개, 범위 {100 * (max(v) - min(v)):.1f}pt)")
        for k in (1, 2):
            v = [np.mean([p["pred"] == p["label"] for p in pr if p["n_implicit"] == k]) for pr in res[arm].values() if any(p["n_implicit"] == k for p in pr)]
            if v:
                L.append(f"  {arm} · 암시 축 {k}개 문서: {sum(v) / len(v):.1%}")
    L += ["", "[사전 등록 판정]"]
    if accs.get("FS7"):
        a7 = sum(accs["FS7"]) / len(accs["FS7"])
        d1 = "문체·양식이 바뀌어도 유지(≥85%)" if a7 >= 0.85 else "부분 하락(70~85%)" if a7 >= 0.70 else "문체 의존 큼(<70%)"
        if min(abs(a7 - 0.85), abs(a7 - 0.70)) <= 0.03:
            d1 += " — 단 경계 ±3pt 안이라 판정 보류"
        L.append(f"  D1 FS7 {len(accs['FS7'])}시드 평균 정확도 {a7:.1%} (개발·표현 94.4%·6차 93.0% 대비 {100 * (a7 - 0.944):+.1f}pt/{100 * (a7 - 0.930):+.1f}pt) → {d1}")
        L.append(f"  D3 FS7 − TF-IDF(FS7 학습분) = {100 * (a7 - tf7):+.1f}pt → " + ("표면 n-gram 을 넘어 읽는다(≥ +10pt)" if a7 - tf7 >= 0.10 else "n-gram 수준(< +10pt)"))
    if accs.get("FS7") and accs.get("FS"):
        a7, a0 = sum(accs["FS7"]) / len(accs["FS7"]), sum(accs["FS"]) / len(accs["FS"])
        w = max(max(accs["FS7"]) - min(accs["FS7"]), max(accs["FS"]) - min(accs["FS"]))
        L.append(f"  D2 FS7 − FS = {100 * (a7 - a0):+.1f}pt (시드 범위 폭 max {100 * w:.1f}pt) → " + ("7차의 이득이 문체 변형에서도 유지" if a7 - a0 >= 0.02 and a7 - a0 > w else "이득이 작성 틀에 묶여 있었다/불명확(< +2pt 이거나 범위 안)"))
    if accs.get("FS9"):
        a9 = sum(accs["FS9"]) / len(accs["FS9"])
        L.append(f"  [9차 판정 · PREREG_R9.md] FS9 {len(accs['FS9'])}시드 평균 정확도(8차, 겨냥한 면) {a9:.1%}")
        if accs.get("FS7"):
            a7 = sum(accs["FS7"]) / len(accs["FS7"])
            w = max(max(accs["FS9"]) - min(accs["FS9"]), max(accs["FS7"]) - min(accs["FS7"]))
            d = a9 - a7
            L.append(f"  FS9 − FS7 = {100 * d:+.1f}pt (시드 범위 폭 max {100 * w:.1f}pt) → " + ("도움(≥+3pt, 범위 초과)" if d >= 0.03 and d > w else "효과 불확실"))
    for arm in ("FS7", "FS9"):
        if res[arm]:
            conf = Counter()
            for pr in res[arm].values():
                for q in pr:
                    conf[(q["label"], q["pred"])] += 1
            L += ["", f"[{arm} 시드 합산 혼동(정답→예측 TS/S1/S2/S3)]"] + [f"  {a} " + str([conf[(a, b)] for b in G]) for a in G]
    text = "\n".join(L)
    (OUT / "r8_style_shift_result.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
