#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 파일럿 문서를 섞어 학습하는 시험 — 개발 800건 5분할(무작위) 위에서.

질문 두 가지:
  (Q1) 현재 모델(개발 800건만 학습, 에폭 10)은 등급 근거가 본문에 적힌 파일럿 문서를 읽는가?            → baseline
  (Q2) 파일럿 문서를 섞어 학습하면 (a) 옛 스타일 문서(개발 800건 평가) 성능이 오르는가·해치는가 (b) 파일럿 문서 판독이 오르는가?  → run
설계(측정 전 고정 — reports/mock1000_dev800/prereg_pilotmix.json)
  · 파일럿은 블라인드 판정이 명세와 일치한 233건만 쓴다(검증 통과 문서). 가족 60개를 5분할에 배정해 **가족 단위**로 학습/평가에 나눈다.
  · 학습 = 개발 분할 k 의 학습 576건 + 파일럿 학습 가족(약 186건), 검증 = 개발 분할 k 의 검증 64건(옛 스타일 그대로), 에폭 10, 시드 42·43. 평가 = pipe.run τ=0.30.
  · (a) 옛 스타일 평가 = 개발 분할 k 의 평가 160건 → 800건 모아 R576E10(에폭 10, 같은 분할)과 비교.
    규칙: F1 차이 ≥ +2pt 이고 시드 범위 폭보다 크면 '옛 문서 성능도 오른다' · ≤ −2pt 이고 시드 범위보다 크면 '옛 문서 성능을 해친다' · 그 밖은 '영향 없음'.
  · (b) 파일럿 평가 = 학습에서 뺀 가족의 문서(합쳐서 233건). 비교 대상 = 같은 문서를 읽은 R576E10 모델(기준선), 글자 n-gram TF-IDF(같은 가족 분할).
    ⚠ 파일럿은 사실 표현이 정형화돼 있다(TF-IDF 가족 분할 72%) — 파일럿 판독 정확도가 높아도 '표현이 바뀐 문서를 읽는다'는 증거가 아니다. 그 검증은 표현을 바꾼 2차 생성 시험셋이 필요하다.
사용:  python scripts/run_mock1000_pilotmix.py prepare | baseline SEED | run SEED | aggregate
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import G, K, POC, TAU, load_jsonl, prf, sha256_file  # noqa: E402

OUT = POC / "reports" / "mock1000_dev800"
D = POC / "reports" / "CLAUDE_DOCGEN_20260921"


def _w(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def prepare() -> int:
    docs = load_jsonl(D / "pilot_docs_checked.jsonl")
    ok = {r["doc_key"] for r in json.loads((D / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    ver = [x for x in docs if x["doc_key"] in ok]
    fams = sorted({x["family_id"] for x in ver})
    random.Random(20260927).shuffle(fams)
    fam_fold = {f: i % K for i, f in enumerate(fams)}
    info = {}
    for k in range(K):
        d = OUT / "rand" / f"fold{k}"
        base = load_jsonl(d / "train.jsonl")
        ptr = [x for x in ver if fam_fold[x["family_id"]] != k]
        pte = [x for x in ver if fam_fold[x["family_id"]] == k]
        conv = lambda x: {"doc_id": x["doc_key"], "text": x["text"], "label": x["grade"], "family": x["family_id"], "source_name": "factfirst_pilot"}  # noqa: E731
        _w(d / "train_mix.jsonl", base + [conv(x) for x in ptr])
        _w(d / "pilot_test.jsonl", [conv(x) for x in pte])
        info[f"fold{k}"] = {"train_mix": len(base) + len(ptr), "pilot_train": len(ptr), "pilot_test": len(pte), "pilot_test_by_grade": {g: sum(1 for x in pte if x["grade"] == g) for g in G}}
        print(f"fold{k}: 학습 {len(base)}+파일럿 {len(ptr)} = {len(base) + len(ptr)} · 파일럿 평가(가족 분리) {len(pte)}건", flush=True)
    prereg = {"date": "2026-09-21", "purpose": "파일럿 문서를 섞어 학습하는 시험 — 옛 스타일 성능 영향(a)과 파일럿 판독(b), 기준선=R576E10",
              "pilot": {"verified_docs": len(ver), "verified_by_judge": "판정=명세 일치 233건", "family_folds": 5, "families": len(fams)},
              "rules": {"old_style": "F1 차이 ≥+2pt 이고 시드 범위 폭보다 크면 오른다 · ≤−2pt 이고 시드 범위보다 크면 해친다 · 그 밖은 영향 없음",
                        "pilot_read": "R576E10 기준선 대비 정확도 차이와 TF-IDF 를 함께 본다. 정형 표현 때문에 높아도 일반화 증거가 아니다."},
              "seeds": [42, 43], "training": "p1_train_classifier.py --mode full --epochs 10 --no-mlflow (train_mix, val=개발 분할 val)", "detail": info,
              "pilot_docs_sha256": sha256_file(D / "pilot_docs_checked.jsonl")}
    (OUT / "prereg_pilotmix.json").write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _pipe(model_dir: Path):
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415

    settings.classifier_escalation_tau = TAU
    return InferencePipeline(model_dir=str(sorted(model_dir.glob("v-*"))[-1]))


def _predict(pipe, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        res = pipe.run(r["text"], metadata=None)
        code = res.label.value if hasattr(res.label, "value") else str(res.label)
        out.append({"doc_id": r["doc_id"], "label": r["label"], "pred": code, "family": r.get("family"), "source": r.get("source_name")})
    return out


def baseline(seed: int) -> int:
    for k in range(K):
        d = OUT / "rand" / f"fold{k}"
        f = d / f"preds_BASEP_s{seed}.json"
        if f.exists():
            continue
        pipe = _pipe(d / f"model_R576E10_s{seed}")
        f.write_text(json.dumps(_predict(pipe, load_jsonl(d / "pilot_test.jsonl")), ensure_ascii=False), encoding="utf-8")
        print(f"baseline s{seed} fold{k} 완료", flush=True)
    return 0


def run(seed: int) -> int:
    for k in range(K):
        d = OUT / "rand" / f"fold{k}"
        f_old, f_pil = d / f"preds_MIX_s{seed}.json", d / f"preds_MIXP_s{seed}.json"
        if f_old.exists() and f_pil.exists():
            continue
        root = d / f"model_MIX_s{seed}"
        for attempt in range(3):
            if list(root.glob("v-*/model.safetensors")):
                break
            cmd = [sys.executable, str(POC / "scripts" / "p1_train_classifier.py"), "--mode", "full", "--epochs", "10", "--seed", str(seed),
                   "--train-path", str(d / "train_mix.jsonl"), "--val-path", str(d / "val.jsonl"), "--test-path", str(d / "test.jsonl"), "--output-dir", str(root), "--no-mlflow"]
            with (d / f"train_MIX_s{seed}.log").open("w", encoding="utf-8") as lg:
                rc = subprocess.run(cmd, cwd=str(POC), env=dict(os.environ, PYTHONIOENCODING="utf-8", TESTING="1"), stdout=lg, stderr=subprocess.STDOUT).returncode
            if rc != 0:
                print(f"MIX s{seed} fold{k} 학습 실패(exit {rc}) 시도 {attempt + 1}/3", flush=True)
        if not list(root.glob("v-*/model.safetensors")):
            print(f"MIX s{seed} fold{k} 포기")
            return 1
        pipe = _pipe(root)
        f_old.write_text(json.dumps(_predict(pipe, load_jsonl(d / "test.jsonl")), ensure_ascii=False), encoding="utf-8")
        f_pil.write_text(json.dumps(_predict(pipe, load_jsonl(d / "pilot_test.jsonl")), ensure_ascii=False), encoding="utf-8")
        print(f"MIX s{seed} fold{k} 완료 (옛 스타일 평가 + 파일럿 평가)", flush=True)
    return 0


def _pool(name: str) -> list[dict] | None:
    out = []
    for k in range(K):
        f = OUT / "rand" / f"fold{k}" / name
        if not f.exists():
            return None
        out += json.loads(f.read_text(encoding="utf-8"))
    return out


def _line(name: str, m: dict) -> str:
    return (f"  {name:<34} macro P/R/F1 {m['macroP']:.1%}/{m['macroR']:.1%}/{m['macroF1']:.1%} · 정확도 {m['acc']:.1%} · 고등급 미탐 {m['hi_miss']}/{m['hi_n']} = {m['hi_miss'] / m['hi_n']:.1%}"
            f" · 재현율 TS/S1/S2/S3 " + "/".join(f"{m['per'][g]['R']:.0%}" for g in G))


def aggregate() -> int:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    L = ["파일럿 섞어 학습 시험 · τ=0.30 · 개발 800건 5분할 + 파일럿 가족 5분할"]
    L += ["", "[(a) 옛 스타일 문서 — 개발 800건 평가(분할 평가 문서를 모아 800건)]"]
    res = {}
    for tag, name in (("R576E10(기준·파일럿 없음)", "preds_R576E10_s{s}.json"), ("MIX(파일럿 섞음)", "preds_MIX_s{s}.json")):
        for s in (42, 43):
            p = _pool(name.format(s=s))
            if p is None:
                continue
            m = prf(p, lambda r: r["pred"])
            res.setdefault(tag, {})[s] = m
            L.append(_line(f"{tag} 시드 {s}", m))
    def rng(tag):
        v = [m["macroF1"] for m in res.get(tag, {}).values()]
        return (min(v), max(v), sum(v) / len(v)) if v else None
    b, x = rng("R576E10(기준·파일럿 없음)"), rng("MIX(파일럿 섞음)")
    if b and x:
        d = 100 * (x[2] - b[2])
        w = 100 * max(b[1] - b[0], x[1] - x[0])
        v = "옛 문서 성능도 오른다" if d >= 2 and d > w else ("옛 문서 성능을 해친다" if d <= -2 and -d > w else "영향 없음")
        L.append(f"  사전 규칙: MIX − 기준 F1 = {d:+.1f}pt (시드 범위 폭 {w:.1f}pt) → {v}")
    L += ["", "[(b) 파일럿 문서 판독 — 학습에서 뺀 가족의 문서(233건)]"]
    for tag, name in (("R576E10 기준선(파일럿 미학습)", "preds_BASEP_s{s}.json"), ("MIX(파일럿 섞어 학습)", "preds_MIXP_s{s}.json")):
        for s in (42, 43):
            p = _pool(name.format(s=s))
            if p is not None:
                L.append(_line(f"{tag} 시드 {s}", prf(p, lambda r: r["pred"])))
    # TF-IDF: 같은 가족 분할(파일럿 학습 가족만으로 학습)
    tf = []
    for k in range(K):
        d = OUT / "rand" / f"fold{k}"
        tr = [r for r in load_jsonl(d / "train_mix.jsonl") if r.get("source_name") == "factfirst_pilot"]
        te = load_jsonl(d / "pilot_test.jsonl")
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
        clf = LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(vec.fit_transform([r["text"] for r in tr]), [r["label"] for r in tr])
        tf += [{"label": r["label"], "pred": p} for r, p in zip(te, clf.predict(vec.transform([r["text"] for r in te])))]
    L.append(_line("글자 n-gram TF-IDF(파일럿 가족 분할)", prf(tf, lambda r: r["pred"])))
    text = "\n".join(L)
    (OUT / "pilotmix_result_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prepare", "baseline", "run", "aggregate"])
    ap.add_argument("seed", nargs="?", type=int)
    a = ap.parse_args()
    return {"prepare": prepare, "aggregate": aggregate}.get(a.cmd, lambda: baseline(a.seed) if a.cmd == "baseline" else run(a.seed))()


if __name__ == "__main__":
    sys.exit(main())
