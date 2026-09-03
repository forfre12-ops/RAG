"""경화 홀드아웃 42건의 등급별 Precision·Recall·F1 과 혼동행렬을 낸다.

회신서 §14 는 대표값(F1 macro)만 실었고 질의 나-3 은 등급별 지표를 함께 물었다.
같은 셋·같은 배포 모델로 등급별 수치를 뽑아 표를 채우기 위한 스크립트다.

  cd poc && TESTING=1 python scripts/report_hardened42_per_grade.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("scripts").resolve()))

GRADES = ["TS", "S1", "S2", "S3"]
SET = Path("datasets/gold_real/holdout_eval.hardened.jsonl")
MODEL = Path("artifacts/classifier_p1_v5_clean/v-fe4b386b")


def _prf(gold: list[str], pred: list[str]) -> dict:
    out = {}
    for g in GRADES:
        tp = sum(1 for a, b in zip(gold, pred) if a == g and b == g)
        fp = sum(1 for a, b in zip(gold, pred) if a != g and b == g)
        fn = sum(1 for a, b in zip(gold, pred) if a == g and b != g)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        out[g] = {"n": tp + fn, "precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3)}
    out["macro_f1"] = round(sum(out[g]["f1"] for g in GRADES) / len(GRADES), 3)
    out["accuracy"] = round(sum(1 for a, b in zip(gold, pred) if a == b) / len(gold), 3)
    return out


def _confusion(gold: list[str], pred: list[str]) -> dict:
    return {g: {p: sum(1 for a, b in zip(gold, pred) if a == g and b == p) for p in GRADES} for g in GRADES}


def main() -> int:
    rows = [json.loads(l) for l in SET.open(encoding="utf-8") if l.strip()]
    gold = [r["label"] for r in rows]

    from eval_p1_model_gold import predict_api_like
    serving = [p["label"] for p in predict_api_like(MODEL, rows)]

    # 원시 argmax — 서빙 보정(온도·τ·FNR-safe·출처 상한) 없이 모델 확률만.
    from koipa.modules.m5_inference.pipeline import InferencePipeline
    pipe = InferencePipeline(model_dir=MODEL)
    raw = []
    for r in rows:
        out = pipe.run(r["text"], return_evidence=False, metadata=None)
        sc = out.scores or {}
        raw.append(max(sc, key=sc.get) if sc else str(out.label))

    print(json.dumps({
        "set": str(SET), "n": len(rows), "model": MODEL.name,
        "serving": {"metrics": _prf(gold, serving), "confusion": _confusion(gold, serving)},
        "raw_argmax": {"metrics": _prf(gold, raw), "confusion": _confusion(gold, raw)},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
