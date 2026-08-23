"""요소 분류기를 잠금 평가셋에 돌려 룰 기준선과 같은 축으로 비교한다.

`measure_rule_extractor.py` 와 **같은 지표**를 낸다(요소별 평균차·MAE·낮게봄/높게봄,
등급 일치율). 그래야 "룰 대비 나아졌나"를 눈으로 대조할 수 있다. 등급은 예측한 S/V/M 을
grade_from_svm() 에 넣어 얻는다 — 요소가 먼저, 등급이 나중이다.

⚠ final_800 은 판정용이다. 이 스크립트 결과를 보고 학습 하이퍼파라미터를 고치면 잠금이
풀린 것이다. 중간 점검은 development_200 으로 할 것.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

FACTORS = ("secrecy", "value", "management")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="요소 분류기 평가")
    parser.add_argument("--model", required=True, help="train_factor_model.py 산출 디렉토리")
    parser.add_argument("--eval", required=True, action="append")
    parser.add_argument("--batch", type=int, default=16)
    # [집계] 문서가 창(512토큰)보다 길고 세 요소의 근거가 문서 곳곳에 흩어져 있다.
    # 앞부분만 보면 value 가 무너진다(실측 MAE 1.045 vs 창을 옮기면 0.190). 청크로 학습한
    # 모델은 각 청크가 문서 라벨을 예측하도록 배웠으므로 확률 평균이 자연스러운 집계다.
    # max 는 미탐 방향으로 안전하지만 라벨 잡음(근거 없는 청크도 높은 수준 라벨을 물려받음)을
    # 그대로 증폭하므로 dev 에서 골라야 한다. final_800 을 보고 고르면 잠금이 풀린다.
    parser.add_argument("--chunk-chars", type=int, default=0,
                        help="0 이면 앞부분만. >0 이면 그 길이로 청크 분할 후 집계")
    parser.add_argument("--chunk-overlap", type=int, default=300)
    parser.add_argument("--agg", choices=("mean", "max"), default="mean")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")

    import torch
    from transformers import AutoTokenizer

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from train_factor_model import _build_model, _load  # noqa: PLC0415

    mdir = Path(args.model)
    meta = json.loads((mdir / "meta.json").read_text("utf-8"))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(str(mdir))
    model = _build_model(meta["base_model"], torch)
    model.load_state_dict(torch.load(mdir / "model.pt", map_location=device, weights_only=True))
    model.to(device).eval()

    report: dict = {"model": str(mdir), "sets": {}}
    for spec in args.eval:
        path = Path(spec)
        texts, truth, grades = _load(path)
        preds: list[tuple[int, int, int]] = []
        with torch.no_grad():
            if args.chunk_chars > 0:
                import torch.nn.functional as F  # noqa: PLC0415

                step = max(1, args.chunk_chars - args.chunk_overlap)
                for text in texts:
                    pieces = [text[i:i + args.chunk_chars]
                              for i in range(0, max(1, len(text)), step)] or [text]
                    probs = []
                    for i in range(0, len(pieces), args.batch):
                        enc = tok(pieces[i:i + args.batch], truncation=True,
                                  max_length=meta["max_len"], padding="max_length",
                                  return_tensors="pt").to(device)
                        lg = model(enc["input_ids"], enc["attention_mask"])
                        probs.append(torch.stack([F.softmax(x, -1) for x in lg]))
                    p = torch.cat(probs, dim=1)  # [3, n_chunks, 3]
                    agg = p.mean(1) if args.agg == "mean" else p.max(1).values
                    preds.append(tuple(int(agg[k].argmax()) for k in range(3)))
            else:
                for i in range(0, len(texts), args.batch):
                    enc = tok(texts[i:i + args.batch], truncation=True,
                              max_length=meta["max_len"], padding="max_length",
                              return_tensors="pt").to(device)
                    logits = model(enc["input_ids"], enc["attention_mask"])
                    p = [lg.argmax(-1).tolist() for lg in logits]
                    preds.extend(zip(p[0], p[1], p[2]))

        entry: dict = {"n": len(preds), "factors": {}}
        for k, name in enumerate(FACTORS):
            d = [preds[i][k] - truth[i][k] for i in range(len(preds))]
            entry["factors"][name] = {
                "mean_diff": round(st.mean(d), 3),
                "mae": round(st.mean([abs(x) for x in d]), 3),
                "under_rate": round(sum(1 for x in d if x < 0) / len(d), 3),
                "over_rate": round(sum(1 for x in d if x > 0) / len(d), 3),
                "exact": round(sum(1 for x in d if x == 0) / len(d), 3),
            }
        hit = sum(1 for i, p in enumerate(preds)
                  if grades[i] and grade_from_svm(*p) == grades[i])
        entry["grade_match_rate"] = round(hit / len(preds), 4)
        report["sets"][path.name] = entry

        print(f"\n{path.name}  N={len(preds)}")
        print(f"  등급 일치율 {entry['grade_match_rate']:.1%}")
        print(f"  {'요소':<12}{'평균차':>8}{'MAE':>8}{'정확':>8}{'낮게봄':>9}{'높게봄':>9}")
        for name in FACTORS:
            e = entry["factors"][name]
            print(f"  {name:<12}{e['mean_diff']:>+8.2f}{e['mae']:>8.2f}{e['exact']:>8.1%}"
                  f"{e['under_rate']:>9.1%}{e['over_rate']:>9.1%}")

    report["note"] = (
        "같은 지표의 룰 기준선은 reports/rule_extractor_baseline.json 에 있다. "
        "final_800 결과를 보고 학습 설정을 고치면 잠금이 풀린 것이다."
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"\n[report] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
