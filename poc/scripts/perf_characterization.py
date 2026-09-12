"""성능 특성화 — "이런 문서가 오면 이 정확도로 분류된다"를 통계로 입증.

룰(B안)·BERT 둘 다, 두 평가셋(합성 test split / 실문서 홀드아웃)에서
출처×등급별 정확도·재현율·고등급 FNR + Wilson 95% 신뢰구간 산출.

실행:
  cd poc
  PYTHONPATH=src .venv/Scripts/python.exe scripts/perf_characterization.py \
    --model artifacts/classifier_bpilot_v2/v-137066a1
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}
EVALSETS = [
    ("합성 test split", "datasets/labeled_bpilot/test.jsonl"),
    ("실문서 홀드아웃", "datasets/gold_real/holdout_eval.jsonl"),
]


def wilson(k: int, n: int, z: float = 1.96):
    """이항비율 Wilson 95% 신뢰구간 (작은 n에 적합)."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def make_bert_predictor(model_dir: str):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir).eval()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(dev)
    id2label = {int(k): v for k, v in model.config.id2label.items()}

    def predict(text: str) -> str:
        enc = tok(text[:3000], truncation=True, max_length=384, return_tensors="pt").to(dev)
        with torch.no_grad():
            logits = model(**enc).logits
        return id2label[int(logits.argmax(-1))]
    return predict


def make_rule_predictor():
    from koipa.modules.m3_labeling.pipeline import LabelingPipeline
    p = LabelingPipeline()

    def predict(text: str) -> str:
        g = p.label(text).grade
        return g.value if hasattr(g, "value") else str(g)
    return predict


def characterize(name: str, rows: list[dict], predict, out: list[str]):
    preds = [(r, predict(r["text"])) for r in rows]
    n = len(preds)
    acc = sum(p == r["label"] for r, p in preds)
    pp, lo, hi = wilson(acc, n)
    out.append(f"\n### {name} — 전체 (n={n})")
    out.append(f"- **정확도 {acc}/{n} = {pp:.0%}**  [95% CI {lo:.0%}–{hi:.0%}]")

    # 출처별
    bysrc = defaultdict(lambda: [0, 0])
    for r, pr in preds:
        s = r.get("source", "?")
        bysrc[s][0] += int(pr == r["label"]); bysrc[s][1] += 1
    out.append("\n| 출처 | n | 정확도 | 95% CI |")
    out.append("|---|---:|---:|---|")
    for s, (k, nn) in sorted(bysrc.items(), key=lambda x: -x[1][1]):
        p2, l2, h2 = wilson(k, nn)
        out.append(f"| {s} | {nn} | {p2:.0%} | {l2:.0%}–{h2:.0%} |")

    # 등급별 재현율 + 고등급 FNR
    bygr = defaultdict(lambda: [0, 0]); fnr = [0, 0]
    for r, pr in preds:
        g = r["label"]; bygr[g][0] += int(pr == g); bygr[g][1] += 1
        if g in ("TS", "S1"):
            fnr[1] += 1; fnr[0] += int(ORDER.get(pr, 9) > ORDER[g])
    out.append("\n| 등급 | n | 재현율(recall) | 95% CI |")
    out.append("|---|---:|---:|---|")
    for g in ("TS", "S1", "S2", "S3"):
        k, nn = bygr[g]
        if nn:
            p2, l2, h2 = wilson(k, nn)
            out.append(f"| {g} | {nn} | {p2:.0%} | {l2:.0%}–{h2:.0%} |")
    fp, fl, fh = wilson(fnr[0], fnr[1])
    out.append(f"\n- **고등급(TS·S1) 미탐율(FNR) {fnr[0]}/{fnr[1]} = {fp:.0%}**  [95% CI {fl:.0%}–{fh:.0%}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="artifacts/classifier_bpilot_v2/v-137066a1")
    ap.add_argument("--report", default="reports/perf_characterization.md")
    args = ap.parse_args()

    rule_pred = make_rule_predictor()
    bert_pred = make_bert_predictor(args.model)

    out = ["# 성능 특성화 — 데이터셋별 분류 정확도 (입증용)", "",
           f"- 모델: `{args.model}`  | 룰: B안(S×V×M)  | CI: Wilson 95%",
           "- 해석: 표본 n이 작은 셀은 CI가 넓음(불확실성 정직 표기)."]
    for setname, path in EVALSETS:
        rows = load(path)
        out.append(f"\n---\n## {setname}  (출처: 아래 표)")
        out.append("\n#### ▶ 룰(B안)")
        characterize(setname + " · 룰", rows, rule_pred, out)
        out.append("\n#### ▶ BERT")
        characterize(setname + " · BERT", rows, bert_pred, out)

    text = "\n".join(out)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[저장] {args.report}")


if __name__ == "__main__":
    main()
