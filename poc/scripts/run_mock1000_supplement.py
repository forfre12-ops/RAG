#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""학습 데이터 보완 시험 — 새 모의문서 1,000건 5분할의 평가 200건×5 는 그대로 두고 학습 쪽만 보태 본다.

왜: 학습량 곡선(run_mock1000_curve.py)에서 학습량이 뚜렷한 제약으로 판정됐다(180/360/720건 macro F1 42.4/53.6/65.2%).
    그렇다면 이미 있는 다른 데이터를 보태면 오르는가? 두 가지를 잰다.
  ① 배포본(v-fe4b386b, 옛 v5_clean 학습셋 2,554건으로 학습)을 새 모의문서에 그대로 태운다 — 학습 없음. v5_clean 과 본문이 같은 문서는 뺀다.
  ② 분할별 학습 720건 + 옛 데이터(labeled_p1_v5_clean_policy 2,554건 · 판결문·LLM 오라벨 117행을 S3 로 정정한 라벨)로 다시 학습.
     옛 데이터에서 (가) 모의문서 1,000건과 본문이 같은 것 (나) 그 분할의 평가 200건과 글자 TF-IDF 코사인 ≥0.7 인 것은 뺀다(누출 방지).
평가·지표는 주 시험과 같다(pipe.run τ=0.30 · macro P/R/F1 · 정확도 · 고등급 미탐). 사전 규칙(측정 전): ②의 macro F1 이 주 시험 시드 범위(61.9~66.3%) 위면 개선,
범위 안이면 효과 없음, 아래면 악화. 옛 데이터는 등급명 낱말이 든 문서가 많고 영문 문서도 섞여 있어 분포가 다르다 — 그 차이가 도움이 되는지 해가 되는지가 시험 대상이다.
⚠ 사업기간 AI 학습은 모의문서로 한다는 협의 ①(확정)과의 관계는 이 시험이 정하지 않는다 — 무엇이 오르는지만 본다.
사용:  python scripts/run_mock1000_supplement.py deployed | prepare | fold K | aggregate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import G, K, RANK, TAU, WORK, POC, load_docs, load_jsonl, prf, sha256_file  # noqa: E402

OLD = POC / "datasets" / "labeled_p1_v5_clean_policy"
OLD_ORIG = POC / "datasets" / "labeled_p1_v5_clean"
DEPLOYED = POC / "artifacts" / "classifier_p1_v5_clean" / "v-fe4b386b"
COS_MAX = 0.7


def _h(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def deployed() -> int:
    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    os.environ.setdefault("DEPLOY_PROFILE", "onprem-local")
    os.environ.setdefault("LLM_PROVIDER", "noop")
    os.environ["CLASSIFIER_MODEL_DIR"] = str(DEPLOYED)
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    from koipa.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415
    from koipa.services.review_reasons import causal_review_reason  # noqa: PLC0415

    snap = {n: str(getattr(settings, n, "?")) for n in ("deploy_profile", "classifier_escalation_tau", "classifier_temperature",
                                                        "review_confidence_threshold", "metadata_floor_enabled", "agreement_gate_enabled", "llm_provider", "classifier_model_dir")}
    print("설정 스냅샷", json.dumps(snap, ensure_ascii=False), flush=True)
    seen = {_h(r["text"]) for sp in ("train", "val", "test") for r in load_jsonl(OLD_ORIG / f"{sp}.jsonl")}
    docs = load_docs()
    svc = ClassifyService()
    rows = []
    for i, d in enumerate(docs):
        overlap = _h(d["text"]) in seen
        pr = svc.inference.run(d["text"], metadata=None)
        pipe_pred = pr.label.value if hasattr(pr.label, "value") else str(pr.label)
        res = svc.classify(ClassifyRequest(doc_id=f"mockdep-{d['doc_id']}", content=d["text"], return_evidence=False))
        pred = res.label.value if hasattr(res.label, "value") else str(res.label)
        warnings = list(getattr(res, "warnings", None) or [])
        rows.append({"doc_id": d["doc_id"], "label": d["label"], "pipe_pred": pipe_pred, "pred": pred, "status": str(res.status), "reason": causal_review_reason(warnings, str(res.status)),
                     "source": d["source_name"], "in_v5_clean": overlap})
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/1000", flush=True)
    (WORK / "deployed_model_preds.json").write_text(json.dumps({"settings": snap, "rows": rows}, ensure_ascii=False), encoding="utf-8")
    print(f"배포본 평가 완료: {len(rows)}건 · v5_clean 과 본문 동일(제외) {sum(r['in_v5_clean'] for r in rows)}건", flush=True)
    return 0


def prepare() -> int:
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer

    old, seen = [], set()
    for sp in ("train", "val", "test"):
        for r in load_jsonl(OLD / f"{sp}.jsonl"):
            if _h(r["text"]) not in seen:
                seen.add(_h(r["text"]))
                old.append({"text": r["text"], "label": r["label"], "doc_id": r.get("doc_id") or f"old-{len(old)}", "source_name": "v5_clean_policy"})
    mock_hash = {_h(d["text"]) for d in load_docs()}
    old = [r for r in old if _h(r["text"]) not in mock_hash]
    info = {"old_after_dedupe_and_mock_exact_removed": len(old), "old_by_label": {g: sum(r["label"] == g for r in old) for g in G}}
    docs = load_docs()
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000).fit([r["text"] for r in old] + [d["text"] for d in docs])
    xo = vec.transform([r["text"] for r in old])
    per = {}
    for k in range(K):
        d = WORK / f"fold{k}"
        te = load_jsonl(d / "test.jsonl")
        sim = (xo @ vec.transform([r["text"] for r in te]).T).toarray().max(axis=1)
        keep = [r for r, s in zip(old, sim) if s < COS_MAX]
        tr = load_jsonl(d / "train.jsonl")
        with (d / "train_plus_old.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for r in tr + keep:
                fh.write(json.dumps({"text": r["text"], "label": r["label"], "doc_id": r["doc_id"]}, ensure_ascii=False) + "\n")
        per[f"fold{k}"] = {"mock_train": len(tr), "old_kept": len(keep), "old_removed_similar_to_eval": len(old) - len(keep), "total": len(tr) + len(keep),
                           "by_label": {g: sum(1 for r in tr + keep if r["label"] == g) for g in G}}
        print(f"fold{k}: 모의 {len(tr)} + 옛 {len(keep)}(평가와 닮아 제외 {len(old) - len(keep)}) = {len(tr) + len(keep)}", flush=True)
    prereg = {"purpose": "학습 데이터 보완 시험 — 학습량이 제약으로 판정된 뒤 옛 데이터를 보태면 오르는가", "arms": ["배포본(v-fe4b386b) 그대로", "분할별 학습 720건 + 옛 v5_clean_policy"],
              "leak_control": f"옛 데이터에서 모의문서와 본문 동일 제거 + 분할 평가 200건과 글자 TF-IDF 코사인 ≥{COS_MAX} 제거", "eval": "주 시험과 같은 평가 200건×5, pipe.run τ=0.30",
              "training": "p1_train_classifier.py --mode full --epochs 5 --seed 42 (주 시험과 동일 레시피, 학습셋만 다름)",
              "decision_rule": "macro F1 > 66.3% 개선 · 61.9~66.3% 효과 없음 · < 61.9% 악화 (주 시험 시드 범위 기준)",
              "old_data": str(OLD), "old_manifest_sha256": sha256_file(OLD / "manifest.json"), "counts": info, "per_fold": per}
    (WORK / "prereg_supplement.json").write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("prereg_supplement sha256", sha256_file(WORK / "prereg_supplement.json"), info)
    return 0


def fold(k: int) -> int:
    d = WORK / f"fold{k}"
    out = d / "model_plus_old"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TESTING="1")
    cmd = [sys.executable, str(POC / "scripts" / "p1_train_classifier.py"), "--mode", "full", "--epochs", "5", "--seed", "42",
           "--train-path", str(d / "train_plus_old.jsonl"), "--val-path", str(d / "val.jsonl"), "--test-path", str(d / "test.jsonl"),
           "--output-dir", str(out), "--no-mlflow"]
    if not list(out.glob("v-*/model.safetensors")):
        with (d / "train_plus_old.log").open("w", encoding="utf-8") as lg:
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
        preds.append({"doc_id": r["doc_id"], "label": r["label"], "pred": code, "source": r["source_name"]})
    (d / "preds_plus_old.json").write_text(json.dumps(preds, ensure_ascii=False), encoding="utf-8")
    print(f"fold{k} 완료: 학습 {sum(1 for _ in open(d / 'train_plus_old.jsonl', encoding='utf-8'))}건 · 평가 {len(preds)}건", flush=True)
    return 0


def _line(name: str, m: dict) -> str:
    return (f"  {name:<34} {m['macroP']:.1%}/{m['macroR']:.1%}/{m['macroF1']:.1%}  정확도 {m['acc']:.1%}  고등급 미탐 {m['hi_miss']}/{m['hi_n']} = {m['hi_miss'] / m['hi_n']:.1%}"
            f"  | 등급별 F1 " + " ".join(f"{g} {m['per'][g]['F1']:.1%}" for g in G))


def aggregate() -> int:
    L = ["학습 데이터 보완 시험 — 평가 문서는 주 시험과 같음 · τ=0.30 · 지표 = macro P/R/F1"]
    base = [r for k in range(K) for r in json.loads((WORK / f"fold{k}" / "preds.json").read_text(encoding="utf-8"))]
    L.append(_line("모의 학습 720건만(주 시험·시드 42)", prf(base, lambda r: r["pred"])))
    plus = [r for k in range(K) for r in json.loads((WORK / f"fold{k}" / "preds_plus_old.json").read_text(encoding="utf-8"))]
    mp = prf(plus, lambda r: r["pred"])
    L.append(_line("모의 720 + 옛 데이터(분할별 재학습)", mp))
    dep = json.loads((WORK / "deployed_model_preds.json").read_text(encoding="utf-8"))
    rows = [r for r in dep["rows"] if not r["in_v5_clean"]]
    L.append(_line(f"배포본 그대로(모의 미학습 {len(rows)}건)", prf(rows, lambda r: r["pipe_pred"])))
    hi = [r for r in rows if r["label"] in ("TS", "S1")]
    under = [r for r in hi if RANK[r["pred"]] < RANK[r["label"]]]
    silent = [r for r in under if r["status"] != "needs_review"]
    ms = prf(rows, lambda r: r["pred"])
    L.append(_line("배포본 서빙 경로 전체(게이트 후)", ms))
    L.append(f"    배포본 서빙: 무음 미탐 {len(silent)}/{len(hi)} = {len(silent) / len(hi):.1%} · 검수 전환 {sum(r['status'] == 'needs_review' for r in rows)}/{len(rows)}")
    lo, hi_ = 0.619, 0.663
    f1 = mp["macroF1"]
    L += ["", f"사전 규칙 판정(모의 720 + 옛 데이터 macro F1 {f1:.1%}; 주 시험 시드 범위 61.9~66.3%): " + ("개선(범위 위)" if f1 > hi_ else ("악화(범위 아래)" if f1 < lo else "효과 없음(범위 안)"))]
    L.append("출처별(모의 720 + 옛 데이터): " + " · ".join(
        f"{s} {sum(r['pred'] == r['label'] for r in plus if r['source'] == s) / max(1, sum(r['source'] == s for r in plus)):.1%}"
        for s in ("selfconsistent_v3", "v8_factor_balance_fill")) + "   (모의 720만: " + " · ".join(
        f"{s} {sum(r['pred'] == r['label'] for r in base if r['source'] == s) / max(1, sum(r['source'] == s for r in base)):.1%}" for s in ("selfconsistent_v3", "v8_factor_balance_fill")) + ")")
    text = "\n".join(L)
    (WORK / "supplement_result_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["deployed", "prepare", "fold", "aggregate"])
    ap.add_argument("k", nargs="?", type=int)
    a = ap.parse_args()
    return {"deployed": deployed, "prepare": prepare, "aggregate": aggregate}.get(a.cmd, lambda: fold(a.k))()


if __name__ == "__main__":
    sys.exit(main())
