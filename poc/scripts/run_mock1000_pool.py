#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""1차 + 2차 파일럿 문서를 함께 섞어 학습하는 시험 — 개발 800건 5분할 위에서 (run_mock1000_pilotmix.py 의 확장).

질문: (a) 1·2차를 모두 섞으면 옛 스타일 문서 성능이 어떻게 되는가(MIX 1차만 vs MIX2 1·2차) (b) 학습에서 뺀 가족(1·2차 각각)의 판독 정확도.
설계: 검증 통과 문서(1차 233 + 2차 228 = 461건)의 가족(F··/G··)을 5분할에 배정해 가족 단위로 학습/평가에 나눈다. 학습 = 개발 분할 k 학습 576건 + 파일럿 학습 가족, 검증 = 개발 분할 k 검증 64건,
      에폭 10, 시드 42·43, 평가 = pipe.run τ=0.30. 사전 규칙은 pilotmix 와 같다(F1 ±2pt·시드 범위).
⚠ 학습에서 뺀 2차 가족은 학습에 들어간 2차 가족과 같은 표현 전략·같은 작성 방식이다 — 표현이 새로운 문서에서의 일반화는 3차 시험셋으로 잰다(eval_r2_phrase_shift.py SHIFT_TARGET=R3).
사용:  python scripts/run_mock1000_pool.py prepare | run SEED | aggregate
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
from run_mock1000_cv import G, K, POC, load_jsonl, prf  # noqa: E402
from run_mock1000_pilotmix import _pipe, _predict, _w  # noqa: E402

OUT = POC / "reports" / "mock1000_dev800"
R1 = POC / "reports" / "CLAUDE_DOCGEN_20260921"
R2 = POC / "reports" / "CLAUDE_DOCGEN_R2_20260921"


def verified(d: Path) -> list[dict]:
    docs = load_jsonl(d / "pilot_docs_checked.jsonl")
    ok = {r["doc_key"] for r in json.loads((d / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    return [x for x in docs if x["doc_key"] in ok]


def prepare() -> int:
    allv = [(x, "R1") for x in verified(R1)] + [(x, "R2") for x in verified(R2)]
    fams = sorted({x["family_id"] for x, _ in allv})
    random.Random(20260930).shuffle(fams)
    fold_of = {f: i % K for i, f in enumerate(fams)}
    for k in range(K):
        d = OUT / "rand" / f"fold{k}"
        base = load_jsonl(d / "train.jsonl")
        conv = lambda x, r: {"doc_id": x["doc_key"], "text": x["text"], "label": x["grade"], "family": x["family_id"], "round": r, "source_name": "factfirst_pilot"}  # noqa: E731
        ptr = [conv(x, r) for x, r in allv if fold_of[x["family_id"]] != k]
        pte = [conv(x, r) for x, r in allv if fold_of[x["family_id"]] == k]
        _w(d / "train_mix2.jsonl", base + ptr)
        _w(d / "pilot2_test.jsonl", pte)
        print(f"fold{k}: 학습 {len(base)}+파일럿 {len(ptr)}(1차 {sum(1 for x in ptr if x['round'] == 'R1')}·2차 {sum(1 for x in ptr if x['round'] == 'R2')}) · 파일럿 평가 {len(pte)}건", flush=True)
    return 0


def run(seed: int) -> int:
    for k in range(K):
        d = OUT / "rand" / f"fold{k}"
        f_old, f_pil = d / f"preds_MIX2_s{seed}.json", d / f"preds_MIX2P_s{seed}.json"
        if f_old.exists() and f_pil.exists():
            continue
        root = d / f"model_MIX2_s{seed}"
        for attempt in range(3):
            if list(root.glob("v-*/model.safetensors")):
                break
            cmd = [sys.executable, str(POC / "scripts" / "p1_train_classifier.py"), "--mode", "full", "--epochs", "10", "--seed", str(seed),
                   "--train-path", str(d / "train_mix2.jsonl"), "--val-path", str(d / "val.jsonl"), "--test-path", str(d / "test.jsonl"), "--output-dir", str(root), "--no-mlflow"]
            with (d / f"train_MIX2_s{seed}.log").open("w", encoding="utf-8") as lg:
                rc = subprocess.run(cmd, cwd=str(POC), env=dict(os.environ, PYTHONIOENCODING="utf-8", TESTING="1"), stdout=lg, stderr=subprocess.STDOUT).returncode
            if rc != 0:
                print(f"MIX2 s{seed} fold{k} 학습 실패(exit {rc}) 시도 {attempt + 1}/3", flush=True)
        if not list(root.glob("v-*/model.safetensors")):
            print(f"MIX2 s{seed} fold{k} 포기")
            return 1
        pipe = _pipe(root)
        f_old.write_text(json.dumps(_predict(pipe, load_jsonl(d / "test.jsonl")), ensure_ascii=False), encoding="utf-8")
        preds = _predict(pipe, load_jsonl(d / "pilot2_test.jsonl"))
        rounds = {r["doc_id"]: r["round"] for r in load_jsonl(d / "pilot2_test.jsonl")}
        for p in preds:
            p["round"] = rounds[p["doc_id"]]
        f_pil.write_text(json.dumps(preds, ensure_ascii=False), encoding="utf-8")
        print(f"MIX2 s{seed} fold{k} 완료", flush=True)
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
    L = ["1·2차 파일럿 섞어 학습 시험 · τ=0.30 · 개발 800건 5분할 + 파일럿 가족 5분할(1차 233 + 2차 228 검증 문서)"]
    L += ["", "[(a) 옛 스타일 문서 — 개발 800건 평가]"]
    res = {}
    for tag, name in (("R576E10(기준)", "preds_R576E10_s{s}.json"), ("MIX(1차 섞음)", "preds_MIX_s{s}.json"), ("MIX2(1·2차 섞음)", "preds_MIX2_s{s}.json")):
        for s in (42, 43):
            p = _pool(name.format(s=s))
            if p is not None:
                m = prf(p, lambda r: r["pred"])
                res.setdefault(tag, {})[s] = m
                L.append(_line(f"{tag} 시드 {s}", m))

    def mean(tag):
        v = [m["macroF1"] for m in res.get(tag, {}).values()]
        return (min(v), max(v), sum(v) / len(v)) if v else None
    b, a, c = mean("R576E10(기준)"), mean("MIX(1차 섞음)"), mean("MIX2(1·2차 섞음)")
    if b and c:
        w = 100 * max(b[1] - b[0], c[1] - c[0])
        d = 100 * (c[2] - b[2])
        L.append(f"  MIX2 − 기준 F1 = {d:+.1f}pt (시드 범위 폭 {w:.1f}pt) → " + ("옛 문서 성능도 오른다" if d >= 2 and d > w else ("옛 문서 성능을 해친다" if d <= -2 and -d > w else "영향 없음")))
    if a and c:
        L.append(f"  MIX2 − MIX(1차만) F1 = {100 * (c[2] - a[2]):+.1f}pt")
    L += ["", "[(b) 학습에서 뺀 파일럿 가족 판독 — 1차·2차 각각]"]
    for s in (42, 43):
        p = _pool(f"preds_MIX2P_s{s}.json")
        if p is None:
            continue
        for r in ("R1", "R2"):
            sub = [x for x in p if x["round"] == r]
            L.append(_line(f"MIX2 시드 {s} · {r} 가족", prf(sub, lambda x: x["pred"])))
    text = "\n".join(L)
    (OUT / "pool_result_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prepare", "run", "aggregate"])
    ap.add_argument("seed", nargs="?", type=int)
    a = ap.parse_args()
    return {"prepare": prepare, "aggregate": aggregate}.get(a.cmd, lambda: run(a.seed))()


if __name__ == "__main__":
    sys.exit(main())
