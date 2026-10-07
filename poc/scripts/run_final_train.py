#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""최종 모의문서(사실 근거 문서 1,000건)로 학습·평가 — 봉인 200건은 열지 않는다. 사전 등록: reports/mock_final_train_20260921/PREREG_FINAL_TRAIN.md

팔(arm): FS = 사실 문서 학습분만 · FSO = 옛 개발 800건 + 사실 문서 학습분. 평가 = 개발·표현 시험 154건(+ FS 는 옛 800건 판독).
사용:  python scripts/run_final_train.py prepare
       python scripts/run_final_train.py run FS|FSO SEED
       python scripts/run_final_train.py aggregate
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import G, POC, load_jsonl, prf, wilson  # noqa: E402
from run_mock1000_pilotmix import _pipe, _predict, _w  # noqa: E402

FS_DIR = POC / "datasets" / "mock_final_factfirst_20260921"
OLD_DIR = POC / "reports" / "mock1000_dev800" / "rand" / "fold0"      # 옛 개발 800건 = fold0 의 train+val+test (같은 800건의 분할만 다름)
OUT = POC / "reports" / "mock_final_train_20260921"
R7 = POC / "reports" / "CLAUDE_DOCGEN_R7_20260921"
VAL_MIN = 64
RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
ARM_CFG = {"FS": ("fs_train.jsonl", 10), "FSO": ("fso_train.jsonl", 10), "FS50": ("fs50_train.jsonl", 10), "FS25": ("fs25_train.jsonl", 10),
           "FS50E20": ("fs50_train.jsonl", 20), "FS25E40": ("fs25_train.jsonl", 40), "FS7": ("fs7_train.jsonl", 10), "FS9": ("fs9_train.jsonl", 10)}      # 팔 → (학습 파일, 에폭). E20·E40 은 FS(에폭 10)와 스텝 수를 맞춘 통제 팔. FS9=FS7+9차(8차 오류 표적)
MIX2_R3_ACC = (0.706 + 0.668) / 2       # pool_result_20260921.txt · r3_phrase_shift_*: MIX2 시드 42·43 의 R3 115건 정확도


def _final_rows() -> list[dict]:
    man = {m["review_id"]: m for m in load_jsonl(FS_DIR / "internal_manifest.jsonl")}
    rows = []
    for r in load_jsonl(FS_DIR / "reviewer_documents_all.jsonl"):
        m = man[r["review_id"]]
        if m["split"] == "sealed":
            continue                                     # 봉인 본문은 어떤 산출 파일에도 쓰지 않는다
        rows.append({"doc_id": r["review_id"], "text": r["text"], "label": m["grade"], "family": m["family_id"], "round": m["round"], "split": m["split"],
                     "source_name": "factfirst_final", "typed_facts": m["typed_facts"], "r4_test": m["round"] == "R4" and m["split"] == "dev_expression"})
    return rows


def prepare() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = _final_rows()
    tr = [r for r in rows if r["split"] == "train"]
    dev = [r for r in rows if r["split"] == "dev_expression"]
    fams = sorted({r["family"] for r in tr})
    random.Random(20261003).shuffle(fams)
    val_f, n = set(), 0
    for f in fams:
        if n >= VAL_MIN:
            break
        val_f.add(f)
        n += sum(1 for r in tr if r["family"] == f)
    val = [r for r in tr if r["family"] in val_f]
    trn = [r for r in tr if r["family"] not in val_f]
    old = load_jsonl(OLD_DIR / "train.jsonl") + load_jsonl(OLD_DIR / "val.jsonl") + load_jsonl(OLD_DIR / "test.jsonl")
    sealed_old = set(json.loads((POC / "reports" / "mock1000_dev800" / "SEALED_ids.json").read_text(encoding="utf-8")))
    assert len(old) == 800 and not (sealed_old & {r["doc_id"] for r in old}), "옛 봉인 200 이 섞였다"
    strip = lambda rs: [{k: r[k] for k in ("doc_id", "text", "label", "family", "round", "source_name") if k in r} for r in rs]  # noqa: E731
    _w(OUT / "fs_train.jsonl", strip(trn))
    _w(OUT / "fs_val.jsonl", strip(val))
    fam_order = sorted({r["family"] for r in trn})
    random.Random(20261004).shuffle(fam_order)
    for tag, frac in (("fs25", 0.25), ("fs50", 0.50)):        # 가족 단위 부분집합(fs25 ⊂ fs50)
        chosen, n = set(), 0
        for f in fam_order:
            if n >= frac * len(trn):
                break
            chosen.add(f)
            n += sum(1 for r in trn if r["family"] == f)
        _w(OUT / f"{tag}_train.jsonl", strip([r for r in trn if r["family"] in chosen]))
    _w(OUT / "fs_dev_test.jsonl", strip(dev))
    _w(OUT / "fs_dev_test_v2.jsonl", [{"doc_id": r["doc_id"], "text": r["text"], "label": r["label"], "typed_facts": r["typed_facts"]} for r in dev])   # 서빙 하니스 v2 입력
    _w(OUT / "fso_train.jsonl", old + strip(trn))
    _w(OUT / "old_dev800.jsonl", old)
    info = {"fs_train": len(trn), "fs_val": len(val), "fs_dev_test": len(dev), "fso_train": len(old) + len(trn), "old_dev800": len(old),
            "by_grade": {k: dict(Counter(r["label"] for r in v)) for k, v in (("train", trn), ("val", val), ("dev", dev))},
            "dev_by_round": dict(Counter("R4test" if r["r4_test"] else r["round"] for r in dev)),
            "family_disjoint": not ({r["family"] for r in trn} & ({r["family"] for r in val} | {r["family"] for r in dev})),
            "fs25_train": len(load_jsonl(OUT / "fs25_train.jsonl")), "fs50_train": len(load_jsonl(OUT / "fs50_train.jsonl")),
            "sealed_text_written": False}
    (OUT / "prepare_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False))
    return 0


def prepare7() -> int:
    """FS7 학습 파일 = FS 학습분 + 7차 검증 통과 문서(판정=명세). 가족은 학습에만 쓴다(검증·개발·시험·6차·봉인과 가족 겹침 없음)."""
    docs = load_jsonl(R7 / "pilot_docs_checked.jsonl")
    ok = {r["doc_key"] for r in json.loads((R7 / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    fam_model = json.loads((R7 / "WRITER_MODELS_PRIVATE.json").read_text(encoding="utf-8"))["family_model"]
    rows = [{"doc_id": d["doc_key"], "text": d["text"], "label": d["grade"], "family": d["family_id"], "round": "R7", "source_name": "factfirst_r7"} for d in docs if d["doc_key"] in ok]
    base = load_jsonl(OUT / "fs_train.jsonl")
    _w(OUT / "fs7_train.jsonl", base + rows)
    info = {"fs_train": len(base), "r7_verified": len(rows), "fs7_train": len(base) + len(rows), "r7_by_grade": dict(Counter(r["label"] for r in rows)),
            "r7_by_writer": dict(Counter(fam_model[r["family"]] for r in rows)), "r7_by_type": dict(Counter(r["family"][0] for r in rows)),
            "family_overlap_with_val_dev": len({r["family"] for r in rows} & {r["family"] for r in load_jsonl(OUT / "fs_val.jsonl") + load_jsonl(OUT / "fs_dev_test.jsonl")})}
    (OUT / "prepare7_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False))
    return 0


def prepare9() -> int:
    """FS9 학습 파일 = FS7 학습분(963) + 9차 검증 통과 문서(8차 오류 TS→S1·S1→S2 를 겨냥한 경계 대조, 판정=명세). 가족은 학습에만 쓴다."""
    R9 = POC / "reports" / "CLAUDE_DOCGEN_R9_20260921"
    docs = load_jsonl(R9 / "pilot_docs_checked.jsonl")
    ok = {r["doc_key"] for r in json.loads((R9 / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    fam_model = json.loads((R9 / "WRITER_MODELS_PRIVATE.json").read_text(encoding="utf-8"))["family_model"]
    rows = [{"doc_id": d["doc_key"], "text": d["text"], "label": d["grade"], "family": d["family_id"], "round": "R9", "source_name": "factfirst_r9"} for d in docs if d["doc_key"] in ok]
    base = load_jsonl(OUT / "fs7_train.jsonl")
    _w(OUT / "fs9_train.jsonl", base + rows)
    info = {"fs7_train": len(base), "r9_verified": len(rows), "fs9_train": len(base) + len(rows), "r9_by_grade": dict(Counter(r["label"] for r in rows)),
            "r9_by_writer": dict(Counter(fam_model[r["family"]] for r in rows)),
            "family_overlap_with_val_dev": len({r["family"] for r in rows} & {r["family"] for r in load_jsonl(OUT / "fs_val.jsonl") + load_jsonl(OUT / "fs_dev_test.jsonl")})}
    (OUT / "prepare9_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False))
    return 0


def run(arm: str, seed: int) -> int:
    root = OUT / f"model_{arm}_s{seed}"
    fname, epochs = ARM_CFG[arm]
    train = OUT / fname
    for attempt in range(3):
        if list(root.glob("v-*/model.safetensors")):
            break
        cmd = [sys.executable, str(POC / "scripts" / "p1_train_classifier.py"), "--mode", "full", "--epochs", str(epochs), "--seed", str(seed), "--train-path", str(train),
               "--val-path", str(OUT / "fs_val.jsonl"), "--test-path", str(OUT / "fs_dev_test.jsonl"), "--output-dir", str(root), "--no-mlflow"]
        with (OUT / f"train_{arm}_s{seed}.log").open("w", encoding="utf-8") as lg:
            rc = subprocess.run(cmd, cwd=str(POC), env=dict(os.environ, PYTHONIOENCODING="utf-8", TESTING="1"), stdout=lg, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            print(f"{arm} s{seed} 학습 실패(exit {rc}) 시도 {attempt + 1}/3", flush=True)
    if not list(root.glob("v-*/model.safetensors")):
        print(f"{arm} s{seed} 포기")
        return 1
    pipe = _pipe(root)
    meta = {r["doc_id"]: r for r in load_jsonl(OUT / "fs_dev_test.jsonl")}
    preds = _predict(pipe, list(meta.values()))
    for p in preds:
        p["round"] = "R4test" if (meta[p["doc_id"]]["round"] == "R4") else meta[p["doc_id"]]["round"]
    (OUT / f"preds_{arm}_s{seed}_dev.json").write_text(json.dumps(preds, ensure_ascii=False), encoding="utf-8")
    (OUT / f"preds_{arm}_s{seed}_old.json").write_text(json.dumps(_predict(pipe, load_jsonl(OUT / "old_dev800.jsonl")), ensure_ascii=False), encoding="utf-8")
    print(f"{arm} s{seed} 완료", flush=True)
    return 0


def _under_over(recs: list[dict]) -> dict:
    out = {}
    for g in G:
        sub = [r for r in recs if r["label"] == g]
        out[g] = {"n": len(sub), "under": sum(RANK[r["pred"]] < RANK[g] for r in sub), "over": sum(RANK[r["pred"]] > RANK[g] for r in sub), "hit": sum(r["pred"] == g for r in sub)}
    return out


def _line(name: str, m: dict) -> str:
    lo, hi = wilson(round(m["acc"] * m["n"]), m["n"])
    return (f"  {name:<30} n={m['n']:<4} 정확도 {m['acc']:.1%}({lo:.0%}~{hi:.0%}) · macro P/R/F1 {m['macroP']:.1%}/{m['macroR']:.1%}/{m['macroF1']:.1%} · 고등급 미탐 {m['hi_miss']}/{m['hi_n']} = "
            f"{m['hi_miss'] / max(1, m['hi_n']):.1%} · 재현율 TS/S1/S2/S3 " + "/".join(f"{m['per'][g]['R']:.0%}" for g in G))


def aggregate() -> int:
    L = ["최종 모의문서(사실 근거 문서)로 학습 · τ=0.30 · 평가면 = 개발·표현 시험 154건(학습·검증에 안 씀) · 사전 등록 PREREG_FINAL_TRAIN.md · 봉인 200 미개봉"]
    res: dict[str, dict[int, dict]] = defaultdict(dict)
    for arm in ("FS", "FSO", "FS50", "FS25", "FS50E20", "FS25E40", "FS7", "FS9"):
        for s in (42, 43, 44):
            f = OUT / f"preds_{arm}_s{s}_dev.json"
            if not f.exists():
                continue
            preds = json.loads(f.read_text(encoding="utf-8"))
            res[arm][s] = {"all": prf(preds, lambda r: r["pred"]), "R3": prf([p for p in preds if p["round"] == "R3"], lambda r: r["pred"]),
                           "R4test": prf([p for p in preds if p["round"] == "R4test"], lambda r: r["pred"]), "uo": _under_over(preds), "preds": preds}
    L += ["", "[개발·표현 시험 154건 = R3 115 + R4 시험 가족 39]"]
    for arm in ("FS", "FSO", "FS50", "FS25", "FS50E20", "FS25E40", "FS7", "FS9"):
        for s, d in sorted(res[arm].items()):
            L.append(_line(f"{arm} 시드 {s} 전체", d["all"]))
            L.append(_line(f"{arm} 시드 {s} R3(표현 새 문서)", d["R3"]))
            L.append(_line(f"{arm} 시드 {s} R4 시험 가족(경계 대조)", d["R4test"]))
    L += ["", f"[판정] MIX2 R3 정확도 기준값 = {MIX2_R3_ACC:.1%} (pool_result_20260921.txt: 시드 42·43 70.6·66.8%)"]

    def mean_rng(arm, sub, key):
        v = [res[arm][s][sub][key] for s in res[arm]]
        return (sum(v) / len(v), max(v) - min(v)) if v else None
    fs_r3, fso_all, fs_all = mean_rng("FS", "R3", "acc"), mean_rng("FSO", "all", "acc"), mean_rng("FS", "all", "acc")
    if fs_r3:
        d = fs_r3[0] - MIX2_R3_ACC
        L.append(f"  Q1 FS R3 정확도 평균 {fs_r3[0]:.1%}(시드 범위 폭 {fs_r3[1] * 100:.1f}pt, 시드 {len(res['FS'])}개) − MIX2 {MIX2_R3_ACC:.1%} = {100 * d:+.1f}pt → "
                 + ("사실 문서만으로 충분(≥ −2pt)" if d >= -0.02 else "옛 문서 섞기가 필요(< −2pt)"))
    if fso_all and fs_all:
        d, w = fso_all[0] - fs_all[0], max(fso_all[1], fs_all[1])
        L.append(f"  Q2 FSO − FS 개발·표현 154건 정확도 = {100 * d:+.1f}pt (시드 범위 폭 max {100 * w:.1f}pt; FS 시드 {len(res['FS'])}·FSO 시드 {len(res['FSO'])}) → "
                 + ("옛 문서가 도움" if d >= 0.02 and d > w else ("옛 문서가 해로움" if d <= -0.02 and -d > w else "영향 없음")))
    if res.get("FS7") and res.get("FS"):
        a7 = [res["FS7"][s]["all"]["acc"] for s in res["FS7"]]
        a0 = [res["FS"][s]["all"]["acc"] for s in res["FS"]]
        d, w = sum(a7) / len(a7) - sum(a0) / len(a0), max(max(a7) - min(a7), max(a0) - min(a0))
        L.append(f"  [7차 판정 · 사전 등록 PREREG_R7.md] FS7 {len(a7)}시드 평균 {sum(a7) / len(a7):.1%} − FS {len(a0)}시드 평균 {sum(a0) / len(a0):.1%} = {100 * d:+.1f}pt (시드 범위 폭 max {100 * w:.1f}pt) → "
                 + ("7차가 도움" if d >= 0.02 and d > w else "7차가 해로움" if d <= -0.02 and -d > w else "영향 없음"))
    if res.get("FS9") and res.get("FS7"):
        a9 = [res["FS9"][s]["all"]["acc"] for s in res["FS9"]]
        a7b = [res["FS7"][s]["all"]["acc"] for s in res["FS7"]]
        d, w = sum(a9) / len(a9) - sum(a7b) / len(a7b), max(max(a9) - min(a9), max(a7b) - min(a7b))
        L.append(f"  [9차 회귀 확인(개발·표현 154, PREREG_R9.md) · 8차 표적] FS9 {len(a9)}시드 평균 {sum(a9) / len(a9):.1%} − FS7 {len(a7b)}시드 평균 {sum(a7b) / len(a7b):.1%} = {100 * d:+.1f}pt (범위 폭 max {100 * w:.1f}pt) → "
                 + ("이 면도 나빠지지 않음" if d >= -0.02 or -d <= w else "회귀 발생 — 이 면이 나빠짐"))
    if res.get("FS50") or res.get("FS25"):
        L += ["", "[학습량 곡선 — 개발·표현 154건 정확도 시드 평균(범위) · 고등급 미탐 평균; 사전 등록 추가 절]"]
        pts = {}
        for arm, n_tr in (("FS25", "fs25_train.jsonl"), ("FS25E40", "fs25_train.jsonl"), ("FS50", "fs50_train.jsonl"), ("FS50E20", "fs50_train.jsonl"), ("FS", "fs_train.jsonl")):
            if not res.get(arm):
                continue
            accs = [res[arm][s]["all"]["acc"] for s in res[arm]]
            hm = [res[arm][s]["all"]["hi_miss"] / res[arm][s]["all"]["hi_n"] for s in res[arm]]
            pts[arm] = (sum(accs) / len(accs), max(accs) - min(accs))
            L.append(f"  {arm:<5} 학습 {len(load_jsonl(OUT / n_tr))}건 · 시드 {len(accs)}개 · 정확도 평균 {pts[arm][0]:.1%} (범위 폭 {100 * pts[arm][1]:.1f}pt) · 고등급 미탐 평균 {sum(hm) / len(hm):.1%}")
        if "FS50E20" in pts and "FS50" in pts and "FS" in pts:
            a, b = pts["FS50E20"][0] - pts["FS50"][0], pts["FS"][0] - pts["FS50E20"][0]
            w = max(pts["FS50E20"][1], pts["FS50"][1])
            wf = max(pts["FS50E20"][1], pts["FS"][1])
            v = ("곡선의 상당 부분은 스텝 부족" if a >= 0.05 and a > w else "") + (" · " if a >= 0.05 and a > w and b >= 0.05 and b > wf else "") + ("문서 수 자체가 정확도를 올린다(스텝 통제 후에도)" if b >= 0.05 and b > wf else "")
            L.append(f"  [추가 2 통제] FS50E20 − FS50 = {100 * a:+.1f}pt (범위 폭 max {100 * w:.1f}pt) · FS − FS50E20 = {100 * b:+.1f}pt (범위 폭 max {100 * wf:.1f}pt) → {v or '혼재, 판정 보류'}")
        if "FS50" in pts and "FS" in pts:
            d, w = pts["FS"][0] - pts["FS50"][0], max(pts["FS"][1], pts["FS50"][1])
            L.append(f"  FS50 → FS 증가 {100 * d:+.1f}pt (두 점 시드 범위 폭 max {100 * w:.1f}pt) → " + ("문서를 더 늘리면 오를 것(≥ +3pt 이고 범위보다 큼)" if d >= 0.03 and d > w else "이 구간에서는 문서량이 병목이 아니다(< +3pt 이거나 범위 안)"))
    L += ["", "[등급별 — 개발·표현 154건, 정답 등급 기준(적중 / 하향 미탐 / 상향 오판)]"]
    for arm in ("FS", "FSO"):
        for s, d in sorted(res[arm].items()):
            L.append(f"  {arm} 시드 {s}: " + " · ".join(f"{g} {d['uo'][g]['hit']}/{d['uo'][g]['n']} 하향 {d['uo'][g]['under']} 상향 {d['uo'][g]['over']}" for g in G))
    L += ["", "[Q3 사실 문서만 학습한 모델(FS)의 옛 스타일 800건 판독 — 판정 없음, 참고: MIX2 옛 스타일 macro F1 70.3~71.3%]"]
    for s in (42, 43, 44):
        f = OUT / f"preds_FS_s{s}_old.json"
        if f.exists():
            L.append(_line(f"FS 시드 {s} 옛 800건", prf(json.loads(f.read_text(encoding='utf-8')), lambda r: r["pred"])))
    text = "\n".join(L)
    (OUT / "final_train_result_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prepare", "prepare7", "prepare9", "run", "aggregate"])
    ap.add_argument("arm", nargs="?", choices=list(ARM_CFG))
    ap.add_argument("seed", nargs="?", type=int)
    a = ap.parse_args()
    if a.cmd == "prepare":
        return prepare()
    if a.cmd == "prepare7":
        return prepare7()
    if a.cmd == "prepare9":
        return prepare9()
    if a.cmd == "aggregate":
        return aggregate()
    return run(a.arm, a.seed)


if __name__ == "__main__":
    sys.exit(main())
