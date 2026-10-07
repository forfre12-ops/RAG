#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""모의문서 1,000건 → 봉인 200건 + 개발 800건으로 고정하고, 개발 800건의 5분할(무작위 · 계열 단위)을 만든다.

왜: 봉인 평가셋은 모델·에폭·임계값·정책을 정할 때 한 번도 열어보지 않아야 한다. 이번 이후의 모든 분석·학습 실험은 개발 800건에서만 한다.
    (이전 시험 — 주 시험·학습량 곡선·보완 시험 — 은 1,000건 전체 교차검증이었다. 그것은 서술용 기록이고 무엇도 그 결과로 결정하지 않았다.)
계열(family) 대리값: v8 = pair_id · selfconsistent = (domain, format) · 그 밖 = 문서 하나가 계열 하나(ts_fin_hr 16건은 한 계열).
    본격 document_family_id 필드는 새 데이터부터 의무화한다. 이 대리값은 기존 1,000건에서 지금 쓸 수 있는 가장 나은 값이다.
봉인 선정: 계열 무작위 순서로 훑어 등급별 50건 상한 안에서 계열 통째로 편입 = 약 200건. 봉인 문서 ID 목록은 파일에만 두고 화면에 찍지 않는다.
사용:  python scripts/build_mock1000_devseal.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import G, POC, SRC, load_docs, sha256_file  # noqa: E402

OUT = POC / "reports" / "mock1000_dev800"
SEAL_SEED = 20260922
SPLIT_SEED = 20260923


def family_proxy(docs: list[dict]) -> dict[str, str]:
    man = [json.loads(x) for x in next(p for p in (SRC / "internal_manifest.jsonl", SRC / "manifest.jsonl") if p.exists()).read_text(encoding="utf-8").splitlines() if x.strip()]
    v8 = {}
    for f in ("train", "calib", "dev"):
        for x in (POC / "datasets" / "new_build_candidates_2026-09-17" / "v8" / f"{f}.jsonl").read_text(encoding="utf-8").splitlines():
            if x.strip():
                r = json.loads(x)
                v8[r["doc_id"]] = r
    sc = {}
    for x in (POC / "datasets" / "labeled_synth_v3_selfconsistent" / "all_1000_before_filter.jsonl").read_text(encoding="utf-8").splitlines():
        if x.strip():
            r = json.loads(x)
            sc[r["doc_id"]] = r
    fam = {}
    for m in man:
        rid, src, sid = m["review_id"], m["source_name"], m["source_doc_id"]
        if src == "v8_factor_balance_fill":
            fam[rid] = f"v8:{v8[sid]['pair_id']}"
        elif src == "selfconsistent_v3":
            fam[rid] = f"sc:{sc[sid]['domain']}|{sc[sid]['format']}"
        elif src == "ts_fin_hr_20260911":
            fam[rid] = "tsfinhr"
        else:
            fam[rid] = f"single:{rid}"
    return fam


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> int:
    from sklearn.model_selection import StratifiedGroupKFold
    import random

    docs = load_docs()
    fam = family_proxy(docs)
    for d in docs:
        d["family"] = fam[d["doc_id"]]
    y = [d["label"] for d in docs]
    grp = [d["family"] for d in docs]
    # 봉인 선정: 계열을 무작위 순서로 훑으며 등급별 50건 상한을 넘지 않는 계열만 통째로 넣는다(계열 무결성 + 등급 균형).
    import random as _r

    quota = 50
    by_fam: dict[str, list[int]] = {}
    for i, d in enumerate(docs):
        by_fam.setdefault(d["family"], []).append(i)
    order = sorted(by_fam)
    _r.Random(SEAL_SEED).shuffle(order)
    cnt = {g: 0 for g in G}
    seal_idx = []
    for f in order:
        add = Counter(docs[i]["label"] for i in by_fam[f])
        if all(cnt[g] + add.get(g, 0) <= quota for g in G):
            seal_idx += by_fam[f]
            for g in G:
                cnt[g] += add.get(g, 0)
    seal_set = {docs[i]["doc_id"] for i in seal_idx}
    dev = [d for d in docs if d["doc_id"] not in seal_set]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "SEALED_ids.json").write_text(json.dumps(sorted(seal_set)), encoding="utf-8")
    seal_fams = {d["family"] for d in docs if d["doc_id"] in seal_set}
    leak = sum(1 for d in dev if d["family"] in seal_fams and not d["family"].startswith("single:"))
    print(f"봉인 {len(seal_set)}건(등급별 {dict(Counter(docs[i]['label'] for i in seal_idx))}) · 개발 {len(dev)}건(등급별 {dict(Counter(d['label'] for d in dev))})")
    print(f"봉인 출처 {dict(Counter(docs[i]['source_name'] for i in seal_idx))} · 봉인과 계열을 공유하는 개발 문서 {leak}건(0 이어야 함)")

    info = {}
    for kind in ("rand", "fam"):
        dy = [d["label"] for d in dev]
        dg = [d["family"] if kind == "fam" else f"u{i}" for i, d in enumerate(dev)]
        folds = list(StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SPLIT_SEED).split(dev, dy, dg))
        for k, (tr_idx, te_idx) in enumerate(folds):
            sub = list(StratifiedGroupKFold(n_splits=10, shuffle=True, random_state=SPLIT_SEED + k).split(
                [dev[i] for i in tr_idx], [dy[i] for i in tr_idx], [dg[i] for i in tr_idx]))
            va = [tr_idx[i] for i in sub[0][1]]
            vs = set(va)
            tr = [i for i in tr_idx if i not in vs]
            rng = random.Random(SPLIT_SEED + 100 + k)
            by = {g: [dev[i] for i in tr if dev[i]["label"] == g] for g in G}
            for g in G:
                rng.shuffle(by[g])
            half = [r for g in G for r in by[g][: round(len(by[g]) / 2)]]
            d = OUT / kind / f"fold{k}"
            write(d / "train.jsonl", [dev[i] for i in tr])
            write(d / "train_half.jsonl", half)
            write(d / "val.jsonl", [dev[i] for i in va])
            write(d / "test.jsonl", [dev[i] for i in te_idx])
            te_f = {dev[i]["family"] for i in te_idx if not dev[i]["family"].startswith("single:")}
            share = sum(1 for i in tr + va if dev[i]["family"] in te_f)
            info[f"{kind}_fold{k}"] = {"train": len(tr), "train_half": len(half), "val": len(va), "test": len(te_idx), "test_by_label": dict(Counter(dev[i]["label"] for i in te_idx)),
                                       "train_val_docs_sharing_family_with_test": share}
            print(f"{kind} fold{k}: 학습 {len(tr)}(절반 {len(half)}) · 검증 {len(va)} · 평가 {len(te_idx)} · 평가 문서와 계열을 공유하는 학습·검증 문서 {share}건", flush=True)
    prereg = {
        "purpose": "봉인 200건 + 개발 800건 고정, 개발 800건 5분할(무작위/계열). 이후 분석·학습·튜닝은 개발 800건에서만.",
        "source": {"dir": "datasets/expert_review_mock_1000_v2_structured_20260921", "reviewer_documents_sha256": sha256_file(SRC / "reviewer_documents.jsonl")},
        "seal": {"n": len(seal_set), "method": "계열을 무작위 순서로 훑어 등급별 50건 상한을 넘지 않는 계열만 통째로 편입", "seed": SEAL_SEED,
                 "ids_file_sha256": sha256_file(OUT / "SEALED_ids.json"),
                 "rule": "봉인 문서는 학습·검증·튜닝·미탐 분석·원인 판정에 쓰지 않는다. 정답은 전문가 검수 확정 라벨이어야 최종 평가에 쓴다. 최종 평가 때 한 번만 연다."},
        "family_proxy": "v8=pair_id · selfconsistent=(domain,format) · ts_fin_hr=한 계열 · 그 밖=문서 하나",
        "splits": {"seed": SPLIT_SEED, "detail": info},
        "decision_rules": {
            "epochs_control": "같은 개발 800건·같은 분할에서 (가) 학습 288건·10에폭 vs 학습 576건·5에폭(업데이트 수 동일) 비교. 차이 ≥5pt 이고 시드 범위보다 크면 문서 수·다양성 부족 · 차이 ≤2pt 면 학습 횟수 문제 · 시드 범위가 차이보다 크면 판정 보류.",
            "undertraining": "288건·5에폭 vs 288건·10에폭 차이가 ≥3pt 이고 시드 범위보다 크면 5에폭은 작은 학습셋에서 미수렴.",
            "family_split": "계열 분할 F1 이 무작위 분할보다 ≥3pt 낮고 시드 범위보다 크면 계열(템플릿) 누출이 점수를 부풀렸다 · 그 아래면 영향 미미로 본다.",
            "seeds": "42·43. 표시는 시드별 값과 최소·최대(시드 2개라 평균의 신뢰구간은 내지 않는다).",
        },
        "training_recipe": "p1_train_classifier.py --mode full --seed S --no-mlflow, 평가 pipe.run τ=0.30 (주 시험과 동일), 에폭은 조건별",
    }
    (OUT / "prereg_dev800.json").write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("prereg_dev800 sha256", sha256_file(OUT / "prereg_dev800.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
