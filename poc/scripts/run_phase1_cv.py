#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""1차 통합테스트(검수 없음) — v5_clean 5분할 교차검증으로 학습 분포 내 보류 예측 2,554건을 만든다.

왜: 학습 분포 내 테스트(val+test 512건)는 고등급이 190건뿐이라 미탐 5% 를 표본 오차 없이 판정할 수 없다.
    5분할 교차검증은 모든 문서를 한 번씩 학습에서 뺀 채 예측하므로 고등급 약 950건의 보류 예측이 나온다.
    (labeled_oss_v1 미사용분 3,137건은 전부 판결문이라 테스트로 쓸 수 없다 — memory target-feasibility-review-curve)

설계
  · 분할 단위 = 근접중복(글자 TF-IDF 코사인 ≥ 0.95) 묶음 — 같은 묶음은 같은 분할에 들어간다(누출 방지).
  · 학습 라벨 = v5_clean 원본 라벨(배포본과 같은 조건). 평가 라벨 = 정정본(s3fix) — LLM 이 규칙 S3 를 덮어쓴 65행을 바로잡은 값.
  · 학습 = p1_train_classifier.py --mode full --epochs 5 --seed 42 (배포본 재현 조건과 같은 스크립트).
  · 평가 = 서빙 경로 pipe.run, escalation τ = 0.30(배포 프로필 값)을 **측정 전에** 고정. τ 미적용은 참고값.
  · 사전 등록: prepare 단계가 prereg.json 을 만든다. 결과를 보고 τ·기준·분할을 바꾸지 않는다.

지표(합격 기준은 점추정 + Wilson 95% 구간을 함께 보고)
  · 재현율 = 4등급 평균(정확 일치) ≥ 90%
  · 미탐 = 고등급(TS·S1) 정답을 정답보다 낮게 판정한 비율 ≤ 5%   (프로젝트 게이트 fnr_high 와 같은 정의)

사용:  python scripts/run_phase1_cv.py prepare
       python scripts/run_phase1_cv.py fold 0        (학습 + 보류 예측, 분할마다 약 10분)
       python scripts/run_phase1_cv.py aggregate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from math import sqrt
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
WORK = POC / "reports" / os.environ.get("PHASE1_DIR", "phase1_cv")
POLICY_LABELS = os.environ.get("PHASE1_LABELS", "orig") == "policy"   # policy: 판결문 검출기(현재) 적중 문서를 S3 로
G = ["TS", "S1", "S2", "S3"]
R = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
K = 5
SEED = 20260920
TAU = 0.30


def load(rel: str) -> list[dict]:
    return [json.loads(x) for x in (POC / rel).read_text(encoding="utf-8").splitlines() if x.strip()]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def prepare() -> int:
    import numpy as np
    from scipy.sparse.csgraph import connected_components
    from scipy.sparse import csr_matrix
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.model_selection import StratifiedGroupKFold

    docs = []
    for sp in ("train", "val", "test"):
        orig = load(f"datasets/labeled_p1_v5_clean/{sp}.jsonl")
        fix = load(f"datasets/labeled_p1_v5_clean_s3fix/{sp}.jsonl")
        assert [r["doc_id"] for r in orig] == [r["doc_id"] for r in fix]
        for o, f in zip(orig, fix):
            o = dict(o)
            o["label_train"] = o["label"]          # 배포본이 학습한 라벨
            o["label_eval"] = f["label"]           # 정정본(평가 진실)
            docs.append(o)
    texts = [d["text"] for d in docs]
    x = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000).fit_transform(texts)
    rows, cols = [], []
    for i in range(0, x.shape[0], 400):
        sim = (x[i:i + 400] @ x.T).toarray()
        for a, b in zip(*np.where(sim >= 0.95)):
            rows.append(i + a)
            cols.append(b)
    n_comp, group = connected_components(csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(docs),) * 2), directed=False)
    y = [d["label_eval"] for d in docs]
    folds = list(StratifiedGroupKFold(n_splits=K, shuffle=True, random_state=SEED).split(texts, y, group))
    WORK.mkdir(parents=True, exist_ok=True)
    for k, (tr_idx, te_idx) in enumerate(folds):
        # 학습 분할에서 다시 10% 를 검증(조기종료용)으로 뗀다 — 같은 묶음 단위
        sub = list(StratifiedGroupKFold(n_splits=10, shuffle=True, random_state=SEED + k).split(
            [texts[i] for i in tr_idx], [y[i] for i in tr_idx], [group[i] for i in tr_idx]))
        va_local = sub[0][1]
        va_idx = [tr_idx[i] for i in va_local]
        tr_only = [i for i in tr_idx if i not in set(va_idx)]
        d = WORK / f"fold{k}"
        d.mkdir(exist_ok=True)
        for name, idx, lab_key in (("train", tr_only, "label_train"), ("val", va_idx, "label_train"), ("test", list(te_idx), "label_train")):
            with (d / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
                for i in idx:
                    r = {kk: vv for kk, vv in docs[i].items() if kk not in ("label_train", "label_eval")}
                    r["label"] = docs[i][lab_key]
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        (d / "heldout_eval_labels.json").write_text(
            json.dumps({docs[i]["doc_id"]: docs[i]["label_eval"] for i in te_idx}, ensure_ascii=False), encoding="utf-8")
        print(f"fold{k}: train {len(tr_only)} · val {len(va_idx)} · 보류 {len(te_idx)} · 보류 고등급 {sum(y[i] in ('TS', 'S1') for i in te_idx)}", flush=True)
    prereg = {
        "purpose": "1차 통합테스트(검수 없음) — v5_clean 5분할 교차검증 보류 예측. 측정 전에 사전 등록.",
        "data": "datasets/labeled_p1_v5_clean(학습 라벨) + labeled_p1_v5_clean_s3fix(평가 라벨), 2,554건",
        "split": {"k": K, "seed": SEED, "group": "글자 TF-IDF 코사인 ≥0.95 근접중복 묶음", "n_groups": int(n_comp)},
        "training": "scripts/p1_train_classifier.py --mode full --epochs 5 --seed 42 (분할마다)",
        "operating_point": {"classifier_escalation_tau": TAU, "source": "배포 프로필 onprem-local (config.py:81)",
                            "note": "측정 후 바꾸지 않는다. τ 미적용(argmax)은 참고값"},
        "metrics": {"recall": "4등급 평균 재현율(정확 일치)", "miss": "고등급(TS·S1) 정답을 정답보다 낮게 판정한 비율"},
        "pass_criteria": {"recall_macro_min": 0.90, "high_grade_miss_max": 0.05, "report": "점추정 + Wilson 95% 구간"},
        "review": "없음(검수 라우팅 미포함)",
    }
    (WORK / "prereg.json").write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding="utf-8")
    prereg["sha256"] = hashlib.sha256((WORK / "prereg.json").read_bytes()).hexdigest()
    print("prereg sha256", prereg["sha256"], flush=True)
    return 0


def truth_labels() -> dict:
    """평가 진실 라벨(본문→라벨). 기본 = 정정본 s3fix. PHASE1_LABELS=policy 이면 프로젝트 판결문 검출기 적중 문서를 S3 로 덮는다."""
    sys.path.insert(0, str(POC / "scripts"))
    truth = {}
    for sp in ("train", "val", "test"):
        for r in load(f"datasets/labeled_p1_v5_clean_s3fix/{sp}.jsonl"):
            lab = r["label"]
            if POLICY_LABELS:
                from build_p1_v5_clean import is_public_ruling  # noqa: PLC0415
                if is_public_ruling(r):
                    lab = "S3"
            truth[r["text"]] = lab
    return truth


def prepare_policy() -> int:
    """기준 분할(reports/phase1_cv/fold*)을 그대로 두고 학습 라벨만 정책 라벨로 바꾼 사본을 만든다."""
    base = POC / "reports" / "phase1_cv"
    truth = truth_labels()
    WORK.mkdir(parents=True, exist_ok=True)
    changed = 0
    for k in range(K):
        d = WORK / f"fold{k}"
        d.mkdir(exist_ok=True)
        for name in ("train", "val", "test"):
            rows = [json.loads(x) for x in (base / f"fold{k}" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            with (d / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
                for r in rows:
                    new = truth[r["text"]]
                    changed += new != r["label"]
                    r["label"] = new
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"정책 라벨로 바뀐 행 {changed}건(분할 사본 합계 — 문서마다 학습·검증·보류 중 한 곳에 한 번씩)")
    return 0


def external(k: int) -> int:
    """분할 k 의 모델을 독립 셋(golden100 v3.0 · holdout109)에 τ=0.30 으로 평가한다 — 다른 문서에서 미탐이 늘었는지 본다."""
    d = WORK / f"fold{k}"
    mdir = sorted((d / "model").glob("v-*"))[-1]
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415

    settings.classifier_escalation_tau = TAU
    pipe = InferencePipeline(model_dir=str(mdir))
    out = {}
    for name, rel, lk in (("golden100_v3", "datasets/gold/golden100_labeled_v3.jsonl", "target"),
                          ("holdout109", "datasets/gold_real/holdout_eval.jsonl", "label")):
        recs = []
        for r in load(rel):
            res = pipe.run(r.get("text") or r.get("body"), metadata=None)
            code = res.label.value if hasattr(res.label, "value") else str(res.label)
            recs.append({"label": r[lk], "pred": code, "scores": {a: float(b) for a, b in res.scores.items()}})
        out[name] = recs
    (d / "preds_external.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"fold{k} 외부 셋 평가 완료", flush=True)
    return 0


def fold(k: int) -> int:
    d = WORK / f"fold{k}"
    out = d / "model"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TESTING="1")
    cmd = [sys.executable, str(POC / "scripts" / "p1_train_classifier.py"), "--mode", "full", "--epochs", "5", "--seed", "42",
           "--train-path", str(d / "train.jsonl"), "--val-path", str(d / "val.jsonl"), "--test-path", str(d / "test.jsonl"),
           "--output-dir", str(out), "--no-mlflow"]
    if not list(out.glob("v-*/model.safetensors")):       # 재개: 학습이 끝난 분할은 건너뛴다
        with (d / "train.log").open("w", encoding="utf-8") as lg:
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
    truth = truth_labels()
    preds = []
    for r in load(f"reports/phase1_cv/fold{k}/test.jsonl"):
        res = pipe.run(r["text"], metadata=None)
        code = res.label.value if hasattr(res.label, "value") else str(res.label)
        preds.append({"doc_id": r.get("doc_id"), "label": truth[r["text"]], "pred": code, "conf": float(res.confidence),
                      "scores": {a: float(b) for a, b in res.scores.items()}, "source": r.get("label_source")})
    (d / "preds.json").write_text(json.dumps(preds, ensure_ascii=False), encoding="utf-8")
    print(f"fold{k} 완료: 보류 {len(preds)}건", flush=True)
    return 0


def esc_argmax(r: dict) -> str:
    return max(r["scores"], key=r["scores"].get)


def metrics(recs: list[dict], key) -> dict:
    n = {g: 0 for g in G}
    ok = {g: 0 for g in G}
    down = {g: 0 for g in G}
    for r in recs:
        p, lab = key(r), r["label"]
        n[lab] += 1
        ok[lab] += p == lab
        down[lab] += R[p] < R[lab]
    rec = {g: ok[g] / n[g] for g in G}
    hi_n, hi_m = n["TS"] + n["S1"], down["TS"] + down["S1"]
    return {"macro": sum(rec.values()) / 4, "rec": rec, "n": n, "hi_n": hi_n, "hi_miss": hi_m,
            "hi_miss_rate": hi_m / hi_n, "hi_ci": wilson(hi_m, hi_n), "acc": sum(ok.values()) / len(recs)}


def aggregate() -> int:
    allp = []
    for k in range(K):
        f = WORK / f"fold{k}" / "preds.json"
        if not f.exists():
            print(f"fold{k} 결과 없음 — 끝나지 않았다")
            return 1
        allp += json.loads(f.read_text(encoding="utf-8"))
    pre = json.loads((WORK / "prereg.json").read_text(encoding="utf-8"))
    print(f"보류 예측 {len(allp)}건 (사전 등록: τ={pre['operating_point']['classifier_escalation_tau']}, 기준 재현율≥90%·미탐≤5%)")
    for name, key in (("τ=0.30 (배포 프로필 · 사전 등록 주 지표)", lambda r: r["pred"]), ("τ 미적용 argmax (참고)", esc_argmax)):
        m = metrics(allp, key)
        lo, hi = m["hi_ci"]
        print(f"\n[{name}]")
        print(f"  4등급 평균 재현율 {m['macro']:.1%} | " + " ".join(f"{g} {m['rec'][g]:.1%}({m['n'][g]})" for g in G) + f" | 정확도 {m['acc']:.1%}")
        print(f"  고등급 미탐 {m['hi_miss']}/{m['hi_n']} = {m['hi_miss_rate']:.1%}  (Wilson 95% {lo:.1%}~{hi:.1%})")
        print(f"  판정: 재현율 {'통과' if m['macro'] >= 0.90 else '미달'} · 미탐 {'통과' if m['hi_miss_rate'] <= 0.05 else '미달'}(점추정)"
              f" · 미탐 상한(95% 구간)이 5% 이하인가: {'예' if hi <= 0.05 else '아니오'}")
        per = []
        for k in range(K):
            sub = json.loads((WORK / f"fold{k}" / "preds.json").read_text(encoding="utf-8"))
            mm = metrics(sub, key)
            per.append(f"fold{k} {mm['macro']:.1%}/{mm['hi_miss']}/{mm['hi_n']}")
        print("  분할별(재현율/고등급미탐/고등급): " + " · ".join(per))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prepare", "prepare_policy", "fold", "external", "aggregate"])
    ap.add_argument("k", nargs="?", type=int)
    a = ap.parse_args()
    if a.cmd == "prepare":
        return prepare()
    if a.cmd == "prepare_policy":
        return prepare_policy()
    if a.cmd == "external":
        return external(a.k)
    if a.cmd == "fold":
        return fold(a.k)
    return aggregate()


if __name__ == "__main__":
    sys.exit(main())
