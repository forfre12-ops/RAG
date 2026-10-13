"""원본 v5_clean 대 S3 정정본(v5_clean_s3fix) 재학습 — 시드별 짝 비교 평가.

사용: python scripts/eval_s3fix_retrain_pairs.py <결과.json>   (reports/s3fix_retrain/{control,s3fix}/model_seed*/v-* 를 찾아 배포본과 함께 평가)
학습: p1_train_classifier.py --mode full --epochs 5 --seed S --train-path/--val-path/--test-path <분할> --output-dir reports/s3fix_retrain/<arm>/model_seedS --no-mlflow


지표 (모두 서빙 경로 pipe.run, TESTING=1 · METADATA_FLOOR_ENABLED=true):
  ① holdout109(정정 후 라벨) 중 source=금융보고서 49건(전부 S3)의 과대분류율 = S3 보다 높게 판정한 비율  ← 이번 정정의 표적
  ② holdout109 고등급(TS·S1) 방향성 미탐 건수 · 정확 일치 재현율
  ③ golden100 v3.0(지름길 셋 — 상대 비교용) 등급별 재현율
  ④ hardened42 라벨 기준 고등급 미탐 (holdout109 안의 같은 42문서)
"""
import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("TESTING", "1")
POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: E402
from koipa.modules.m6_evaluation.serving_eval import predict_via_serving  # noqa: E402

RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
load = lambda p: [json.loads(l) for l in open(POC / p, encoding="utf-8") if l.strip()]
hold = load("datasets/gold_real/holdout_eval.jsonl")
hard = load("datasets/gold_real/holdout_eval.hardened.jsonl")
gold = load("datasets/gold/golden100_labeled_v3.jsonl")
lab = lambda r: r.get("label") or r.get("target")
txt = lambda r: (r.get("text") or r.get("body") or "").strip()
fin_idx = [i for i, r in enumerate(hold) if r.get("source") == "금융보고서" and lab(r) == "S3"]
hard_labels = {txt(r): lab(r) for r in hard}

models = {"배포본": POC / "artifacts/classifier_p1_v5_clean/v-fe4b386b"}
for p in sorted(glob.glob(str(POC / "reports/s3fix_retrain/*/model_seed*/v-*"))):
    parts = Path(p).parts
    models[f"{parts[-3]}/{parts[-2].replace('model_', '')}"] = Path(p)
only = sys.argv[2:] if len(sys.argv) > 2 else None
out_path = sys.argv[1]
res = json.load(open(out_path, encoding="utf-8")) if os.path.exists(out_path) else {}
for name, mdir in models.items():
    if name in res or (only and name not in only):
        continue
    if not (mdir / "model.safetensors").exists():
        print("건너뜀(모델 없음)", name, flush=True)
        continue
    pipe = InferencePipeline(model_dir=str(mdir))
    hp = predict_via_serving([{"text": txt(r), "label": lab(r)} for r in hold], pipeline=pipe)
    hpred = [p["pred"] for p in hp]
    gp = predict_via_serving([{"text": txt(r), "label": lab(r)} for r in gold], pipeline=pipe)
    gpred = [p["pred"] for p in gp]
    r = {}
    fin = [hpred[i] for i in fin_idx]
    r["fin_S3_n"] = len(fin)
    r["fin_S3_overclass"] = sum(RANK[p] > 0 for p in fin)
    r["fin_S3_dist"] = dict(Counter(fin))
    def block(pairs, key):
        b = {}
        for g in ("TS", "S1", "S2", "S3"):
            sub = [(t, p) for t, p in pairs if t == g]
            b[f"{key}_{g}_n"] = len(sub)
            b[f"{key}_{g}_ok"] = sum(p == t for t, p in sub)
            b[f"{key}_{g}_down"] = sum(RANK[p] < RANK[t] for t, p in sub)
        b[f"{key}_acc"] = sum(t == p for t, p in pairs) / len(pairs)
        return b
    r.update(block([(lab(x), p) for x, p in zip(hold, hpred)], "h109"))
    r.update(block([(lab(x), p) for x, p in zip(gold, gpred)], "g100"))
    hp_by_text = {txt(x): p for x, p in zip(hold, hpred)}
    r.update(block([(hard_labels[t], hp_by_text[t]) for t in hard_labels], "h42"))
    res[name] = r
    json.dump(res, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{name}: 금융보고서 과대분류 {r['fin_S3_overclass']}/{r['fin_S3_n']} · h109 acc {r['h109_acc']:.3f} · "
          f"h109 고등급 미탐 TS {r['h109_TS_down']}/{r['h109_TS_n']} S1 {r['h109_S1_down']}/{r['h109_S1_n']} · "
          f"g100 TS {r['g100_TS_ok']}/50 S1 {r['g100_S1_ok']}/50 S2 {r['g100_S2_ok']}/50 S3 {r['g100_S3_ok']}/50", flush=True)
print("EVAL_DONE", flush=True)
