#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사전 품질시험 — 새 모의문서 1,000건(expert_review_mock_1000_v2_structured_20260921) 5분할 교차검증.

왜: 보호원 요청 2("현재 확보된 데이터를 임시로 학습용/평가용으로 분리해 성능을 확인")에 답하려면 **새로 추린 모의문서 1,000건** 기준의
    전체 건수·등급별 건수·학습/평가 구성·Precision/Recall/F1 이 필요하다. 옛 1차 통합테스트(v5_clean 2,554건)는 다른 묶음이다.
    5분할 교차검증은 1,000건 전부를 한 번씩 학습에서 뺀 채 예측하므로 (학습 720 · 검증 80 · 평가 200) × 5 = 평가 1,000건이 나온다.

라벨: internal_manifest.jsonl 의 source_label = **전문가 검수 이전의 잠정 등급**(같은 AI 3회 다수결·요소 상태 파생 규칙). 사람 확정 정답이 아니다.
설계는 scripts/run_phase1_cv.py 와 같다 — 분할 단위=근접중복(글자 TF-IDF 코사인 ≥0.95) 묶음, 학습=p1_train_classifier.py --mode full --epochs 5 --seed 42,
평가=서빙 경로 pipe.run + escalation τ=0.30(배포 프로필, 측정 전 고정).  비교용으로 (1) 글자 n-gram TF-IDF+로지스틱 회귀(지름길 기준선)
(2) 라벨 섞기 기준선을 같은 분할에서 함께 잰다.  합격 기준은 두지 않는다 — 목표값은 이 결과를 보고 협의하는 대상이다.

사용:  python scripts/run_mock1000_cv.py prepare
       python scripts/run_mock1000_cv.py fold 0        (분할마다 학습+보류 예측)
       python scripts/run_mock1000_cv.py tfidf
       python scripts/run_mock1000_cv.py aggregate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from collections import Counter
from math import sqrt
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # cp949 콘솔에서 '—' 출력이 UnicodeEncodeError 로 죽던 것 방지(결과에는 영향 없었음)
except Exception:  # noqa: BLE001
    pass

POC = Path(__file__).resolve().parents[1]
SRC = POC / "datasets" / "expert_review_mock_1000_v2_structured_20260921"
WORK = POC / "reports" / "mock1000_cv_20260921"
G = ["TS", "S1", "S2", "S3"]
RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
K = 5
SEED = 20260921
TAU = 0.30


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_docs() -> list[dict]:
    rev = [json.loads(x) for x in (SRC / "reviewer_documents.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    man = [json.loads(x) for x in next(p for p in (SRC / "internal_manifest.jsonl", SRC / "manifest.jsonl") if p.exists()).read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [r["review_id"] for r in rev] == [m["review_id"] for m in man], "검수번호 순서가 두 파일에서 다르다"
    return [{"doc_id": r["review_id"], "text": r["text"], "label": m["source_label"], "label_source": "mock_provisional",
             "source_name": m["source_name"], "restructured": m["enrichment_status"] != "unchanged"} for r, m in zip(rev, man)]


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def prepare() -> int:
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.model_selection import StratifiedGroupKFold

    sys.path.insert(0, str(POC / "src"))
    from koipa.services.synth_quality import _grade_term_pattern  # noqa: PLC0415

    docs = load_docs()
    texts = [d["text"] for d in docs]
    y = [d["label"] for d in docs]
    x = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000).fit_transform(texts)
    rows, cols, nn = [], [], []
    for i in range(0, x.shape[0], 400):
        sim = (x[i:i + 400] @ x.T).toarray()
        for a in range(sim.shape[0]):
            sim[a, i + a] = 0.0
        nn.extend(sim.max(axis=1).tolist())
        for a, b in zip(*np.where(sim >= 0.95)):
            rows.append(i + a)
            cols.append(b)
    n_comp, group = connected_components(csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(docs),) * 2), directed=False)
    pat = _grade_term_pattern()
    with_term = sum(bool(pat.search(unicodedata.normalize("NFKC", t))) for t in texts)
    folds = list(StratifiedGroupKFold(n_splits=K, shuffle=True, random_state=SEED).split(texts, y, group))
    WORK.mkdir(parents=True, exist_ok=True)
    sizes = []
    for k, (tr_idx, te_idx) in enumerate(folds):
        sub = list(StratifiedGroupKFold(n_splits=10, shuffle=True, random_state=SEED + k).split(
            [texts[i] for i in tr_idx], [y[i] for i in tr_idx], [group[i] for i in tr_idx]))
        va_idx = [tr_idx[i] for i in sub[0][1]]
        va_set = set(va_idx)
        tr_only = [i for i in tr_idx if i not in va_set]
        d = WORK / f"fold{k}"
        d.mkdir(exist_ok=True)
        for name, idx in (("train", tr_only), ("val", va_idx), ("test", list(te_idx))):
            with (d / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
                for i in idx:
                    fh.write(json.dumps(docs[i], ensure_ascii=False) + "\n")
        sizes.append({"fold": k, "train": len(tr_only), "val": len(va_idx), "test": len(te_idx),
                      "train_by_label": dict(Counter(y[i] for i in tr_only)), "val_by_label": dict(Counter(y[i] for i in va_idx)),
                      "test_by_label": dict(Counter(y[i] for i in te_idx))})
        print(f"fold{k}: 학습 {len(tr_only)} · 검증 {len(va_idx)} · 평가 {len(te_idx)} · 평가 등급별 " + str(dict(Counter(y[i] for i in te_idx))), flush=True)
    nnq = np.quantile(nn, [0.5, 0.9, 0.99, 1.0])
    prereg = {
        "purpose": "보호원 요청 2 — 새 모의문서 1,000건 사전 품질시험(5분할 교차검증). 측정 전에 사전 등록. 합격 기준 없음(목표값은 협의 대상).",
        "data": {"dir": "datasets/expert_review_mock_1000_v2_structured_20260921 (git 미추적)",
                 "reviewer_documents_sha256": sha256_file(SRC / "reviewer_documents.jsonl"),
                 "internal_manifest_sha256": sha256_file(next(q for q in (SRC / "internal_manifest.jsonl", SRC / "manifest.jsonl") if q.exists())),
                 "n": len(docs), "by_label": dict(Counter(y)), "by_source": dict(Counter(d["source_name"] for d in docs)),
                 "label_meaning": "internal_manifest.source_label = 전문가 검수 이전 잠정 등급(사람 확정 아님)"},
        "split": {"k": K, "seed": SEED, "group": "글자 TF-IDF 코사인 ≥0.95 근접중복 묶음", "n_groups": int(n_comp),
                  "val_within_train": "학습 분할의 10%(조기 종료용)", "folds": sizes},
        "training": "scripts/p1_train_classifier.py --mode full --epochs 5 --seed 42 (분할마다, 기반 kakaobank/kf-deberta-base, 학습 라벨=잠정 등급)",
        "operating_point": {"classifier_escalation_tau": TAU, "source": "배포 프로필 onprem-local", "note": "측정 후 바꾸지 않는다. τ 미적용(argmax)은 참고값"},
        "metrics": "등급별·macro Precision/Recall/F1, 정확도, 혼동행렬, 고등급(TS·S1) 미탐(정답보다 낮게 판정), 출처별 분해",
        "baselines": ["글자 n-gram TF-IDF + 로지스틱 회귀(같은 분할 — 지름길 기준선)", "라벨 섞기(1,000회)", "최빈 등급"],
        "review": "없음",
        "checks": {"grade_name_words_in_text": with_term,
                   "nearest_neighbor_cosine_quantiles_p50_p90_p99_max": [round(float(v), 3) for v in nnq]},
    }
    (WORK / "prereg.json").write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("등급명 낱말이 든 문서", with_term, "· 근접중복 묶음", int(n_comp), "· 최근접 코사인 p50/p90/p99/max", [round(float(v), 3) for v in nnq])
    print("prereg sha256", sha256_file(WORK / "prereg.json"), flush=True)
    return 0


def fold(k: int, seed: int = 42) -> int:
    """seed 42 = 사전 등록 주 실행. 그 밖의 시드는 같은 분할에서 학습만 다시 한 보조 실행(파일명에 _s{seed})."""
    d = WORK / f"fold{k}"
    sfx = "" if seed == 42 else f"_s{seed}"
    out = d / f"model{sfx}"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TESTING="1")
    cmd = [sys.executable, str(POC / "scripts" / "p1_train_classifier.py"), "--mode", "full", "--epochs", "5", "--seed", str(seed),
           "--train-path", str(d / "train.jsonl"), "--val-path", str(d / "val.jsonl"), "--test-path", str(d / "test.jsonl"),
           "--output-dir", str(out), "--no-mlflow"]
    if not list(out.glob("v-*/model.safetensors")):       # 재개: 학습이 끝난 분할은 건너뛴다
        with (d / f"train{sfx}.log").open("w", encoding="utf-8") as lg:
            rc = subprocess.run(cmd, cwd=str(POC), env=env, stdout=lg, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            print(f"fold{k} 학습 실패 exit={rc}")
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
        preds.append({"doc_id": r["doc_id"], "label": r["label"], "pred": code, "conf": float(res.confidence),
                      "scores": {a: float(b) for a, b in res.scores.items()}, "source": r["source_name"], "restructured": r["restructured"]})
    (d / f"preds{sfx}.json").write_text(json.dumps(preds, ensure_ascii=False), encoding="utf-8")
    print(f"fold{k} 시드 {seed} 완료: 평가 {len(preds)}건", flush=True)
    return 0


def tfidf() -> int:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    for k in range(K):
        d = WORK / f"fold{k}"
        tr = load_jsonl(d / "train.jsonl") + load_jsonl(d / "val.jsonl")
        te = load_jsonl(d / "test.jsonl")
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
        xt = vec.fit_transform([r["text"] for r in tr])
        clf = LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(xt, [r["label"] for r in tr])
        pred = clf.predict(vec.transform([r["text"] for r in te]))
        (d / "preds_tfidf.json").write_text(json.dumps(
            [{"doc_id": r["doc_id"], "label": r["label"], "pred": p} for r, p in zip(te, pred)], ensure_ascii=False), encoding="utf-8")
    print("TF-IDF 기준선 완료")
    return 0


def prf(recs: list[dict], key) -> dict:
    """등급별 Precision/Recall/F1 + macro + 정확도 + 혼동행렬 + 고등급 미탐."""
    conf = {a: {b: 0 for b in G} for a in G}          # conf[정답][예측]
    for r in recs:
        conf[r["label"]][key(r)] += 1
    per = {}
    for g in G:
        tp = conf[g][g]
        fn = sum(conf[g].values()) - tp
        fp = sum(conf[a][g] for a in G) - tp
        p = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * rc / (p + rc) if p + rc else 0.0
        per[g] = {"n": tp + fn, "pred_n": tp + fp, "P": p, "R": rc, "F1": f}
    n = len(recs)
    hi_n = sum(per[g]["n"] for g in ("TS", "S1"))
    hi_miss = sum(1 for r in recs if r["label"] in ("TS", "S1") and RANK[key(r)] < RANK[r["label"]])
    weights = [per[g]["n"] for g in G]
    return {"per": per, "n": n, "acc": sum(conf[g][g] for g in G) / n,
            "macroP": sum(per[g]["P"] for g in G) / 4, "macroR": sum(per[g]["R"] for g in G) / 4, "macroF1": sum(per[g]["F1"] for g in G) / 4,
            "weightedF1": sum(per[g]["F1"] * w for g, w in zip(G, weights)) / n,
            "conf": conf, "hi_n": hi_n, "hi_miss": hi_miss, "hi_ci": wilson(hi_miss, hi_n)}


def fmt(m: dict) -> list[str]:
    out = ["  등급     건수  예측건수  Precision  Recall   F1"]
    for g in G:
        p = m["per"][g]
        out.append(f"  {g:<4} {p['n']:>7} {p['pred_n']:>8}   {p['P']:>7.1%}  {p['R']:>7.1%} {p['F1']:>7.1%}")
    out.append(f"  macro(4등급 단순평균)          {m['macroP']:>7.1%}  {m['macroR']:>7.1%} {m['macroF1']:>7.1%}   | 가중 F1 {m['weightedF1']:.1%} · 정확도 {m['acc']:.1%}")
    lo, hi = m["hi_ci"]
    out.append(f"  고등급(TS·S1) 미탐 {m['hi_miss']}/{m['hi_n']} = {m['hi_miss'] / m['hi_n']:.1%} (Wilson 95% {lo:.1%}~{hi:.1%})")
    out.append("  혼동행렬(행=정답, 열=예측)      " + "  ".join(f"{g:>4}" for g in G))
    for a in G:
        out.append(f"    {a:<4}                         " + "  ".join(f"{m['conf'][a][b]:>4}" for b in G))
    return out


def aggregate() -> int:
    import random

    allp, tf = [], []
    for k in range(K):
        for name, bucket in (("preds.json", allp), ("preds_tfidf.json", tf)):
            f = WORK / f"fold{k}" / name
            if not f.exists():
                print(f"fold{k}/{name} 없음 — 끝나지 않았다")
                return 1
            bucket += json.loads(f.read_text(encoding="utf-8"))
    pre = json.loads((WORK / "prereg.json").read_text(encoding="utf-8"))
    L: list[str] = []
    L.append(f"평가 예측 {len(allp)}건 (사전 등록 sha256 {sha256_file(WORK / 'prereg.json')[:16]} · τ={TAU} 고정 · 잠정 등급 기준)")
    L.append(f"데이터 {pre['data']['n']}건 · 등급별 {pre['data']['by_label']} · 출처별 {pre['data']['by_source']}")
    for f_ in pre["split"]["folds"]:
        L.append(f"  분할{f_['fold']}: 학습 {f_['train']} · 검증 {f_['val']} · 평가 {f_['test']} · 평가 등급별 {f_['test_by_label']}")
    main = prf(allp, lambda r: r["pred"])
    L += ["", f"[모델 · 서빙 경로 τ={TAU}(배포 프로필) — 주 지표]"] + fmt(main)
    per_fold = []
    for k in range(K):
        sub = json.loads((WORK / f"fold{k}" / "preds.json").read_text(encoding="utf-8"))
        mm = prf(sub, lambda r: r["pred"])
        per_fold.append(mm["macroF1"])
    L.append("  분할별 macro F1: " + " · ".join(f"{v:.1%}" for v in per_fold) + f"  (최소 {min(per_fold):.1%} ~ 최대 {max(per_fold):.1%})")
    arg = prf(allp, lambda r: max(r["scores"], key=r["scores"].get))
    L += ["", "[참고 · τ 미적용(argmax)]", f"  macro P/R/F1 {arg['macroP']:.1%}/{arg['macroR']:.1%}/{arg['macroF1']:.1%} · 정확도 {arg['acc']:.1%} · 고등급 미탐 {arg['hi_miss']}/{arg['hi_n']}"]
    tfm = prf(tf, lambda r: r["pred"])
    L += ["", "[지름길 기준선 · 글자 n-gram TF-IDF + 로지스틱 회귀(같은 분할)]"] + fmt(tfm)
    labels = [r["label"] for r in allp]
    preds = [r["pred"] for r in allp]
    rng = random.Random(SEED)
    shuf = []
    for _ in range(1000):
        sl = labels[:]
        rng.shuffle(sl)
        shuf.append(prf([{"label": a, "pred": b} for a, b in zip(sl, preds)], lambda r: r["pred"])["macroF1"])
    shuf.sort()
    maj = max(Counter(labels).values()) / len(labels)
    L += ["", f"[라벨 섞기 기준선(1,000회)] macro F1 평균 {sum(shuf) / len(shuf):.1%} · 95번째 백분위 {shuf[949]:.1%} · 최빈 등급 정확도 {maj:.1%}"]
    L += ["", "[출처별 분해 · τ=0.30] (라벨이 붙은 방식이 다르다)"]
    groups = {"selfconsistent_v3(같은 AI 3회 다수결)": lambda r: r["source"] == "selfconsistent_v3",
              "v8_factor_balance_fill(요소 상태 파생 규칙)": lambda r: r["source"] == "v8_factor_balance_fill",
              "그 밖(재활용 30건)": lambda r: r["source"] not in ("selfconsistent_v3", "v8_factor_balance_fill")}
    for name, fn in groups.items():
        sub = [r for r in allp if fn(r)]
        sm = prf(sub, lambda r: r["pred"])
        L.append(f"  {name}: {len(sub)}건 · 등급별 " + str({g: sm['per'][g]['n'] for g in G}) + f" · 정확도 {sm['acc']:.1%} · 등급별 재현율 " + " ".join(f"{g} {sm['per'][g]['R']:.1%}" for g in G) + f" · 고등급 미탐 {sm['hi_miss']}/{sm['hi_n']}")
    for flag, name in ((True, "구조화 재편집 216건"), (False, "원문 그대로 784건")):
        sub = [r for r in allp if r["restructured"] == flag]
        sm = prf(sub, lambda r: r["pred"])
        L.append(f"  {name}: {len(sub)}건 · 정확도 {sm['acc']:.1%} · macro F1 {sm['macroF1']:.1%}")
    # 확인 절 — 글자 수 구간별 · 가장 닮은 학습 문서와의 코사인 구간별(같은 틀의 형제 문서가 학습에 있어 부풀려졌는지)
    from sklearn.feature_extraction.text import TfidfVectorizer

    chars = {d["doc_id"]: len(d["text"]) for d in load_docs()}
    sim_to_train: dict[str, float] = {}
    for k in range(K):
        d = WORK / f"fold{k}"
        tr = load_jsonl(d / "train.jsonl") + load_jsonl(d / "val.jsonl")
        te = load_jsonl(d / "test.jsonl")
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
        xt = vec.fit_transform([r["text"] for r in tr])
        sims = (vec.transform([r["text"] for r in te]) @ xt.T).toarray().max(axis=1)
        for r, s in zip(te, sims):
            sim_to_train[r["doc_id"]] = float(s)
    L += ["", "[글자 수 구간별 · τ=0.30]"]
    for lo, hi in ((0, 300), (300, 600), (600, 10**9)):
        sub = [r for r in allp if lo <= chars[r["doc_id"]] < hi]
        sm = prf(sub, lambda r: r["pred"])
        L.append(f"  {lo}~{hi if hi < 10**9 else ''}자: {len(sub)}건 · 정확도 {sm['acc']:.1%} · macro F1 {sm['macroF1']:.1%} · 고등급 미탐 {sm['hi_miss']}/{sm['hi_n']}")
    L += ["", "[가장 닮은 학습 문서와의 코사인 구간별 · τ=0.30]"]
    for lo, hi in ((0, 0.5), (0.5, 0.7), (0.7, 1.01)):
        sub = [r for r in allp if lo <= sim_to_train[r["doc_id"]] < hi]
        sm = prf(sub, lambda r: r["pred"])
        L.append(f"  코사인 {lo:.1f}~{min(hi, 1.0):.1f}: {len(sub)}건 · 정확도 {sm['acc']:.1%} · macro F1 {sm['macroF1']:.1%}")
    seed_rows = {42: main}
    for sd in (43, 44):
        fs = [WORK / f"fold{k}" / f"preds_s{sd}.json" for k in range(K)]
        if all(f.exists() for f in fs):
            seed_rows[sd] = prf([r for f in fs for r in json.loads(f.read_text(encoding="utf-8"))], lambda r: r["pred"])
    if len(seed_rows) > 1:
        L += ["", "[시드 변동 · 같은 분할·같은 학습 조건에서 시드만 바꿔 다시 학습(τ=0.30)]"]
        for sd, mm in seed_rows.items():
            L.append(f"  시드 {sd}{'(주 실행)' if sd == 42 else '(보조)'}: macro P/R/F1 {mm['macroP']:.1%}/{mm['macroR']:.1%}/{mm['macroF1']:.1%} · 정확도 {mm['acc']:.1%} · 고등급 미탐 {mm['hi_miss']}/{mm['hi_n']} = {mm['hi_miss'] / mm['hi_n']:.1%} · 등급별 F1 "
                     + " ".join(f"{g} {mm['per'][g]['F1']:.1%}" for g in G))
        for name, fn in (("macro F1", lambda m: m["macroF1"]), ("정확도", lambda m: m["acc"]), ("고등급 미탐률", lambda m: m["hi_miss"] / m["hi_n"])):
            vals = [fn(m) for m in seed_rows.values()]
            L.append(f"  범위 {name}: {min(vals):.1%} ~ {max(vals):.1%}")
        for g in G:
            vals = [m["per"][g]["F1"] for m in seed_rows.values()]
            L.append(f"  범위 {g} F1: {min(vals):.1%} ~ {max(vals):.1%}")
    text = "\n".join(L)
    (WORK / "result_20260921.txt").write_text(text, encoding="utf-8")
    (WORK / "result_20260921.json").write_text(json.dumps({"main": main, "argmax": arg, "tfidf": tfm, "per_fold_macroF1": per_fold,
                                                            "shuffle_macroF1_mean": sum(shuf) / len(shuf), "shuffle_macroF1_p95": shuf[949]},
                                                           ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prepare", "fold", "tfidf", "aggregate"])
    ap.add_argument("k", nargs="?", type=int)
    ap.add_argument("--seed", type=int, default=42, help="42=사전 등록 주 실행, 그 밖은 보조(시드 변동 범위 확인)")
    a = ap.parse_args()
    return {"prepare": prepare, "tfidf": tfidf, "aggregate": aggregate}.get(a.cmd, lambda: fold(a.k, a.seed))()


if __name__ == "__main__":
    sys.exit(main())
