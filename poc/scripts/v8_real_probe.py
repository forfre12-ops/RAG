"""요소 모델을 **우리 생성기 밖의 실데이터**로 잰다.

왜. 1차·2차 판정면을 모두 통과했지만 둘 다 내가 만든 문서다. "형태도 표현도 새롭다" 고 해도
같은 생성기 · 같은 프레임 어휘 · 같은 문장 구조에서 나온다. 판정면을 하나 더 만들어도
그 한계는 안 바뀐다.

프로젝트에 이미 실데이터가 있다:

    patent_proxy/holdout_eval_clean   1,190건  S1 890 · TS 300   AI Hub 특허 기반
    gold_real/holdout_eval.hardened      42건  4등급             실문서 성격 업무문서
    mundane_s3/holdout                   50건  S3 50            짧은 일상 문서

요소 라벨은 없다. 그래서 **요소를 예측한 뒤 grade_from_svm 으로 등급을 만들어** 기존 등급
라벨과 댄다. 요소 정확도는 못 재지만 "요소 -> 등급" 경로가 실문서에서 서는지는 재진다.

⚠ 이 셋들은 요소 기반이 아니라 등급 기반으로 만들어졌다. 정본 규칙이 S x V x M 곱셈이므로
   등급이 맞아도 요소 조합은 다를 수 있다. 그래서 **등급 일치와 미탐 방향만** 본다.

⚠ 한 번 쓰면 이 셋도 소비된다. 결과를 보고 프레임을 고치면 그 순간 판정면이 아니게 된다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GRADES = ("TS", "S1", "S2", "S3")
ORDER = {g: i for i, g in enumerate(GRADES)}
FACTORS = ("secrecy", "value", "management")
NM = {0: "absent", 1: "lv1", 2: "lv2", 3: "unk"}


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (c + r) / d)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="요소 모델 실데이터 탐침")
    ap.add_argument("--model", default="artifacts/factor_model/v8_bal")
    ap.add_argument("--base", default="kakaobank/kf-deberta-base")
    ap.add_argument("--classes", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="0 이면 전체")
    ap.add_argument("--report", default="reports/V8_REAL_PROBE.json")
    args = ap.parse_args(argv)

    import torch
    from transformers import AutoTokenizer

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm

    from train_factor_model import _build_model
    from v8_judge import cls_to_score, cls_to_worst, predict

    tok = AutoTokenizer.from_pretrained(args.base)
    model = _build_model(args.base, torch, args.classes).cuda()
    model.load_state_dict(torch.load(Path(args.model) / "model.pt"))

    # 출처별로 갈라 만든 실문서 판정면(v8_real_surface.py). 섞으면 수치가 어디서 왔는지
    # 알 수 없다 — 판례는 우리 루브릭으로 S3 가 정답이라 모델이 S3 라 해도 맞다.
    sets = [
        ("business(주판정면)", "datasets/v8_real/business.jsonl"),
        ("finance", "datasets/v8_real/finance.jsonl"),
        ("court", "datasets/v8_real/court.jsonl"),
        ("경화42(이전기준)", "datasets/gold_real/holdout_eval.hardened.jsonl"),
    ]
    out: dict = {"model": args.model}
    for name, path in sets:
        p = Path(path)
        if not p.exists():
            print(f"[skip] {path}")
            continue
        rows = [json.loads(l) for l in p.read_text("utf-8").splitlines() if l.strip()]
        rows = [r for r in rows if (r.get("text") or r.get("content")) and r.get("label") in ORDER]
        if args.limit:
            rows = rows[:args.limit]
        texts = [r.get("text") or r.get("content") for r in rows]
        truth = [r["label"] for r in rows]

        preds, probs = predict(model, tok, texts, "cuda", args.max_len, args.batch,
                               want_probs=True)
        # [규칙 선택] grade() 는 unknown 을 0 으로 본다 — 데이터 만들 때 쓰는 값이다.
        # 서빙에서 사람에게 보이는 등급은 serving_grade() 이고 unknown 을 최악으로 채운다.
        # 실측 2026-08-14: 무언급 unknown 학습으로 unknown 이 18->28 로 늘었는데 grade()
        # 로 재니 등급이 그대로였다. 규칙을 잘못 쓰면 개선이 안 보인다.
        pred = [grade_from_svm(*[cls_to_score(c) for c in c3]) for c3 in preds]
        pred_serving = [grade_from_svm(*[cls_to_worst(c) for c in c3]) for c3 in preds]

        hi = [i for i, g in enumerate(truth) if g in ("TS", "S1")]
        under = [i for i in hi if ORDER[pred[i]] > ORDER[truth[i]]]
        to_s3 = [i for i in hi if pred[i] == "S3"]
        acc = sum(1 for a, b in zip(truth, pred) if a == b) / len(rows)
        # 서빙 규칙 기준 — 이것이 실제 운영에서 사람이 보는 등급이다
        under_s = [i for i in hi if ORDER[pred_serving[i]] > ORDER[truth[i]]]
        acc_s = sum(1 for a, b in zip(truth, pred_serving) if a == b) / len(rows)

        # 자동확정 게이트를 태웠을 때 — 실문서에서 무음 미탐이 나오는가
        auto = []
        for i, r in enumerate(rows):
            conf = min(max(d) for d in probs[i])
            if conf < 0.99:
                continue
            if pred[i] == "S3" and grade_from_svm(*[cls_to_worst(c) for c in preds[i]]) != "S3":
                continue
            auto.append(i)
        a_miss = [i for i in auto if ORDER[pred[i]] > ORDER[truth[i]]]

        blk = {
            "n": len(rows),
            "grade_acc": round(acc, 4),
            "truth_dist": dict(Counter(truth)),
            "pred_dist": dict(Counter(pred)),
            "high_n": len(hi),
            "under_rate": round(len(under) / len(hi), 4) if hi else None,
            "high_to_s3": len(to_s3),
            "serving": {
                "grade_acc": round(acc_s, 4),
                "under_rate": round(len(under_s) / len(hi), 4) if hi else None,
                "high_to_s3": len([i for i in hi if pred_serving[i] == "S3"]),
                "pred_dist": dict(Counter(pred_serving)),
            },
            "factor_pred_dist": {
                f: dict(Counter(NM[c[k]] for c in preds)) for k, f in enumerate(FACTORS)
            },
            "gate_tau099": {
                "auto_n": len(auto),
                "coverage": round(len(auto) / len(rows), 4),
                "silent_miss_n": len(a_miss),
                "silent_miss_95_upper": round(wilson_upper(len(a_miss), len(auto)), 4) if auto else None,
            },
        }
        out[name] = blk
        print(f"\n=== {name} (n={blk['n']})")
        sv = blk["serving"]
        print(f"  [저술규칙] 등급일치 {blk['grade_acc']:.4f} · 과소분류 {blk['under_rate']} · S3추락 {blk['high_to_s3']}")
        print(f"  [서빙규칙] 등급일치 {sv['grade_acc']:.4f} · 과소분류 {sv['under_rate']} · S3추락 {sv['high_to_s3']}")
        print(f"             예측 {sv['pred_dist']}")
        print(f"  정답 {blk['truth_dist']}")
        print(f"  예측 {blk['pred_dist']}")
        for f in FACTORS:
            print(f"    {f:11s} {blk['factor_pred_dist'][f]}")
        g = blk["gate_tau099"]
        print(f"  게이트 tau0.99 · 자동 {g['auto_n']}({g['coverage']:.1%}) · "
              f"무음 미탐 {g['silent_miss_n']} · 상한 {g['silent_miss_95_upper']}")

    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
