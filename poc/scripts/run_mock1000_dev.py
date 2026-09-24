#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""개발 800건(봉인 200건 제외) 5분할 학습 실험 — 에폭 통제 · 계열 분할 vs 무작위 분할.

사전 등록·판정 규칙: reports/mock1000_dev800/prereg_dev800.json (build_mock1000_devseal.py 가 만든다). 봉인 200건은 이 스크립트가 절대 읽지 않는다(분할 파일에 없다).
조건(이름 = 분할 R무작위/F계열 · 학습건수 · 에폭):
  R576E5  무작위 분할 · 학습 ~576건 · 5에폭        (기준 — 주 시험 레시피)
  R288E10 무작위 분할 · 학습 ~288건 · 10에폭       (업데이트 수를 R576E5 와 맞춤)
  R288E5  무작위 분할 · 학습 ~288건 · 5에폭        (미수렴 확인)
  F576E5  계열 분할   · 학습 ~520~545건 · 5에폭    (계열 누출 확인; 학습이 무작위 분할보다 5~10% 적다 — 계열이 크게 묶여 균형이 깨진다)
  R576E10 무작위 분할 · 학습 ~576건 · 10에폭       (576건 자체가 미수렴인지)
평가: pipe.run τ=0.30, 분할별 평가 문서를 모아 800건 전체에서 macro P/R/F1·정확도·고등급 미탐.
사용:  python scripts/run_mock1000_dev.py run CFG SEED | tfidf | aggregate
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import G, K, TAU, POC, load_jsonl, prf  # noqa: E402

OUT = POC / "reports" / "mock1000_dev800"
CFGS = {"R576E5": ("rand", "train.jsonl", 5), "R288E10": ("rand", "train_half.jsonl", 10), "R288E5": ("rand", "train_half.jsonl", 5),
        "F576E5": ("fam", "train.jsonl", 5), "R576E10": ("rand", "train.jsonl", 10),
        "R576E15": ("rand", "train.jsonl", 15), "R576E20": ("rand", "train.jsonl", 20),
        "R576E10NC": ("rand", "train.jsonl", 10, ["--no-class-weight"]),
        "R576E10F2": ("rand", "train.jsonl", 10, ["--fnr-cost-multiplier", "2.0"]),
        "R576E10F3": ("rand", "train.jsonl", 10, ["--fnr-cost-multiplier", "3.0"])}


def run(cfg: str, seed: int) -> int:
    split, train_file, epochs = CFGS[cfg][:3]
    extra = list(CFGS[cfg][3]) if len(CFGS[cfg]) > 3 else []
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(POC / "src"))
    for k in range(K):
        d = OUT / split / f"fold{k}"
        pf = d / f"preds_{cfg}_s{seed}.json"
        if pf.exists():
            continue
        mdir_root = d / f"model_{cfg}_s{seed}"
        for attempt in range(3):                                   # GPU 를 다른 작업과 나눠 쓰므로 메모리 부족 등 일시 실패는 재시도한다
            if list(mdir_root.glob("v-*/model.safetensors")):
                break
            cmd = [sys.executable, str(POC / "scripts" / "p1_train_classifier.py"), "--mode", "full", "--epochs", str(epochs), "--seed", str(seed),
                   "--train-path", str(d / train_file), "--val-path", str(d / "val.jsonl"), "--test-path", str(d / "test.jsonl"),
                   "--output-dir", str(mdir_root), "--no-mlflow"] + extra
            with (d / f"train_{cfg}_s{seed}.log").open("w", encoding="utf-8") as lg:
                rc = subprocess.run(cmd, cwd=str(POC), env=dict(os.environ, PYTHONIOENCODING="utf-8", TESTING="1"), stdout=lg, stderr=subprocess.STDOUT).returncode
            if rc != 0:
                print(f"{cfg} s{seed} fold{k} 학습 실패(exit {rc}) 시도 {attempt + 1}/3", flush=True)
        if not list(mdir_root.glob("v-*/model.safetensors")):
            print(f"{cfg} s{seed} fold{k} 포기")
            return 1
        from koipa.config import settings  # noqa: PLC0415
        from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415

        settings.classifier_escalation_tau = TAU
        pipe = InferencePipeline(model_dir=str(sorted(mdir_root.glob("v-*"))[-1]))
        preds = []
        for r in load_jsonl(d / "test.jsonl"):
            res = pipe.run(r["text"], metadata=None)
            code = res.label.value if hasattr(res.label, "value") else str(res.label)
            preds.append({"doc_id": r["doc_id"], "label": r["label"], "pred": code, "source": r["source_name"]})
        pf.write_text(json.dumps(preds, ensure_ascii=False), encoding="utf-8")
        print(f"{cfg} s{seed} fold{k} 완료: 학습 {sum(1 for _ in open(d / train_file, encoding='utf-8'))}건 · 에폭 {epochs} · 평가 {len(preds)}건", flush=True)
    return 0


def tfidf() -> int:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    for split in ("rand", "fam"):
        for k in range(K):
            d = OUT / split / f"fold{k}"
            tr = load_jsonl(d / "train.jsonl") + load_jsonl(d / "val.jsonl")
            te = load_jsonl(d / "test.jsonl")
            vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
            clf = LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(vec.fit_transform([r["text"] for r in tr]), [r["label"] for r in tr])
            pred = clf.predict(vec.transform([r["text"] for r in te]))
            (d / "preds_tfidf.json").write_text(json.dumps([{"doc_id": r["doc_id"], "label": r["label"], "pred": p} for r, p in zip(te, pred)], ensure_ascii=False), encoding="utf-8")
    print("TF-IDF 완료")
    return 0


def _pool(split: str, name: str) -> list[dict] | None:
    out = []
    for k in range(K):
        f = OUT / split / f"fold{k}" / name
        if not f.exists():
            return None
        out += json.loads(f.read_text(encoding="utf-8"))
    return out


def aggregate() -> int:
    L = ["개발 800건 5분할 · τ=0.30 · 조건별 시드별 값(분할 평가 문서를 모아 800건)  — 봉인 200건은 쓰지 않음"]
    res: dict[str, dict[int, dict]] = {}
    for cfg, spec in CFGS.items():
        split = spec[0]
        for seed in (42, 43):
            p = _pool(split, f"preds_{cfg}_s{seed}.json")
            if p is None:
                continue
            res.setdefault(cfg, {})[seed] = prf(p, lambda r: r["pred"])
    L.append("  조건       시드  macro P/R/F1          정확도   고등급 미탐         등급별 재현율 TS/S1/S2/S3")
    for cfg, by in res.items():
        for seed, m in sorted(by.items()):
            L.append(f"  {cfg:<9} {seed:>4}  {m['macroP']:.1%}/{m['macroR']:.1%}/{m['macroF1']:.1%}   {m['acc']:.1%}   {m['hi_miss']}/{m['hi_n']} = {m['hi_miss'] / m['hi_n']:.1%}   "
                     + "/".join(f"{m['per'][g]['R']:.0%}" for g in G))
    for split in ("rand", "fam"):
        p = _pool(split, "preds_tfidf.json")
        if p:
            m = prf(p, lambda r: r["pred"])
            L.append(f"  TF-IDF({'무작위' if split == 'rand' else '계열'} 분할) macro P/R/F1 {m['macroP']:.1%}/{m['macroR']:.1%}/{m['macroF1']:.1%} · 정확도 {m['acc']:.1%} · 고등급 미탐 {m['hi_miss']}/{m['hi_n']}")

    def rng(cfg: str) -> tuple[float, float, float] | None:
        v = [m["macroF1"] for m in res.get(cfg, {}).values()]
        return (min(v), max(v), sum(v) / len(v)) if v else None

    L += ["", "[조건별 macro F1 · 시드 최소~최대(평균)]"]
    for cfg in CFGS:
        r = rng(cfg)
        if r:
            L.append(f"  {cfg:<9} {r[0]:.1%} ~ {r[1]:.1%}  (평균 {r[2]:.1%}, 시드 {len(res[cfg])}개, 시드 범위 폭 {100 * (r[1] - r[0]):.1f}pt)")
    L += ["", "[사전 등록 규칙(prereg_dev800.json) 적용]"]

    def diff(a: str, b: str) -> tuple[float, float] | None:
        ra, rb = rng(a), rng(b)
        if not ra or not rb:
            return None
        return 100 * (ra[2] - rb[2]), 100 * max(ra[1] - ra[0], rb[1] - rb[0])

    d = diff("R576E5", "R288E10")
    if d:
        v = "문서 수·다양성 부족(차이 유지)" if d[0] >= 5 and d[0] > d[1] else ("학습 횟수 문제(차이 대부분 사라짐)" if d[0] <= 2 else "판정 보류(시드 범위≥차이 또는 2~5pt)")
        L.append(f"  에폭 통제 R576E5 − R288E10 = {d[0]:+.1f}pt (시드 범위 폭 {d[1]:.1f}pt) → {v}")
    d = diff("R288E10", "R288E5")
    if d:
        v = "5에폭은 작은 학습셋에서 미수렴" if d[0] >= 3 and d[0] > d[1] else "미수렴 증거 없음/판정 보류"
        L.append(f"  미수렴 R288E10 − R288E5 = {d[0]:+.1f}pt (시드 범위 폭 {d[1]:.1f}pt) → {v}")
    d = diff("R576E10", "R576E5")
    if d:
        L.append(f"  576건 에폭 R576E10 − R576E5 = {d[0]:+.1f}pt (시드 범위 폭 {d[1]:.1f}pt; R576E10 은 시드 1개일 수 있음)")
    d = diff("F576E5", "R576E5")
    if d:
        v = "계열(템플릿) 누출이 점수를 부풀렸다" if d[0] <= -3 and -d[0] > d[1] else ("영향 미미" if d[0] > -3 else "판정 보류(시드 범위≥차이)")
        L.append(f"  계열 분할 F576E5 − R576E5 = {d[0]:+.1f}pt (시드 범위 폭 {d[1]:.1f}pt; 계열 분할은 학습이 5~10% 적음) → {v}")
    # 개선 1번 스윕 판정(prereg_sweep.json — 측정 전에 고정한 규칙)
    L += ["", "[사전 등록 규칙(prereg_sweep.json) 적용]"]
    d = diff("R576E15", "R576E10")
    if d:
        v = "에폭을 더 늘리면 오른다(에폭 10 상한 미도달 → 에폭 20 실행)" if d[0] >= 2 and d[0] > d[1] else ("평평 — 에폭 10 으로 충분" if d[0] < 1.5 else "판정 보류(1.5~2pt 또는 시드 범위≥차이)")
        L.append(f"  에폭 15 R576E15 − R576E10 = {d[0]:+.1f}pt (시드 범위 폭 {d[1]:.1f}pt) → {v}")
    d = diff("R576E20", "R576E15")
    if d:
        v = "계속 오른다" if d[0] >= 2 and d[0] > d[1] else ("평평" if d[0] < 1.5 else "판정 보류")
        L.append(f"  에폭 20 R576E20 − R576E15 = {d[0]:+.1f}pt (시드 범위 폭 {d[1]:.1f}pt) → {v}")
    d = diff("R576E10NC", "R576E10")
    if d:
        v = "클래스 가중 끄기가 낫다(채택 후보)" if d[0] >= 2 and d[0] > d[1] else ("클래스 가중 유지" if d[0] <= -2 and -d[0] > d[1] else "영향 없음/판정 보류")
        L.append(f"  클래스 가중 끄기 R576E10NC − R576E10 = {d[0]:+.1f}pt (시드 범위 폭 {d[1]:.1f}pt) → {v}")
        for c in ("R576E10", "R576E10NC"):
            if c in res:
                L.append("     " + c + " 시드별 고등급 미탐 " + " · ".join(f"{m['hi_miss']}/{m['hi_n']}" for m in res[c].values())
                         + " · 등급별 재현율(TS/S1/S2/S3) " + " | ".join("/".join(f"{m['per'][g]['R']:.0%}" for g in G) for m in res[c].values()))
    # 고등급 손실 가중(fnr_cost_multiplier) 판정(prereg_lossweight.json — 측정 전 고정): 미탐↓ 가 F1 손실을 정당화하는가, 아니면 τ·정책 설정과 같은 교환 곡선인가
    if "R576E10" in res:
        base_miss = [m["hi_miss"] / m["hi_n"] for m in res["R576E10"].values()]
        base_mean = sum(base_miss) / len(base_miss)
        base_w = 100 * (max(base_miss) - min(base_miss))
        for c, mult in (("R576E10F2", "2.0"), ("R576E10F3", "3.0")):
            d = diff(c, "R576E10")
            if not d or c not in res:
                continue
            cm = [m["hi_miss"] / m["hi_n"] for m in res[c].values()]
            gain = 100 * (base_mean - sum(cm) / len(cm))                    # 고등급 미탐률 감소(pt, 양수=좋아짐)
            spread = max(base_w, 100 * (max(cm) - min(cm)))
            if gain >= 3 and gain > spread and d[0] >= -2:
                v = "채택 후보(F1 손실 2pt 이내에서 고등급 미탐 감소, 시드 범위 밖)"
            elif gain >= 3 and gain > spread:
                v = "같은 교환 곡선(미탐은 줄지만 F1 손실 2pt 초과 — τ·자동확정 제외 설정과 다르지 않음)"
            else:
                v = "효과 없음/판정 보류(미탐 감소 3pt 미만이거나 시드 범위 안)"
            L.append(f"  고등급 손실 가중 ×{mult} {c} − R576E10: F1 {d[0]:+.1f}pt · 고등급 미탐률 {100 * sum(cm) / len(cm):.1f}% vs {100 * base_mean:.1f}% (감소 {gain:+.1f}pt, 시드 범위 폭 {spread:.1f}pt) → {v}")
    text = "\n".join(L)
    (OUT / "dev800_result_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "tfidf", "aggregate"])
    ap.add_argument("cfg", nargs="?")
    ap.add_argument("seed", nargs="?", type=int)
    a = ap.parse_args()
    return run(a.cfg, a.seed) if a.cmd == "run" else (tfidf() if a.cmd == "tfidf" else aggregate())


if __name__ == "__main__":
    sys.exit(main())
