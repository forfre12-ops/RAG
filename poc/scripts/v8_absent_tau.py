"""`absent` 단언에만 별도 문턱을 건다 — 비대칭 비용에는 비대칭 결정 규칙.

왜. 서빙 규칙에서 무음 미탐을 만드는 클래스는 **absent 하나뿐**이다.

    정답 lv2 · 예측 unknown  ->  보수적 완성이 2 로 채움  ->  고등급  ->  검수로 감
    정답 lv2 · 예측 absent   ->  곱이 0                ->  S3      ->  무음 통과

그런데 argmax 는 네 클래스를 대등하게 본다. p(absent)=0.34 로 1등이면 absent 를 낸다.
비용이 비대칭인데 결정 규칙이 대칭이다.

그래서 absent 는 p(absent) >= kappa 일 때만 채택하고, 아니면 unknown 으로 내린다.
lv1/lv2 는 건드리지 않는다 — 그쪽으로 틀리는 것은 과분류라 안전하다.

    kappa 0.0   현재와 같음(argmax)
    kappa 1.0   absent 를 절대 내지 않음 -> 미탐 0 이지만 S3 자동확정도 0

⚠ 이것은 **결정 규칙**이지 학습이 아니다. 판정면을 소비하지 않는 대신, kappa 를 판정면
   에서 고르면 그 순간 소비된다. kappa 는 **보정면(calib)에서 고르고** 판정면에서는
   고른 값 하나만 쓴다.
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
CLS_ABSENT, CLS_UNKNOWN = 0, 3


def apply_absent_tau(cls3: list[int], prob3, kappa: float) -> list[int]:
    """absent 예측 중 확신이 kappa 에 못 미치는 것을 unknown 으로 내린다."""
    out = list(cls3)
    for k in range(3):
        if out[k] == CLS_ABSENT and prob3[k][CLS_ABSENT] < kappa:
            out[k] = CLS_UNKNOWN
    return out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="absent 문턱 스윕")
    ap.add_argument("--model", default="artifacts/factor_model/v8_sec")
    ap.add_argument("--base", default="kakaobank/kf-deberta-base")
    ap.add_argument("--data", action="append", default=None,
                    help="이름=경로. 여러 번 줄 수 있다")
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--source-prior", action="store_true",
                    help="공개 출처 문서를 S3 로 캡한다 — kappa 의 대가를 갚는 짝")
    ap.add_argument("--report", default="reports/V8_ABSENT_TAU.json")
    args = ap.parse_args(argv)

    sets = args.data or [
        "business_work=datasets/v8_real/business_work.jsonl",
        "finance=datasets/v8_real/finance.jsonl",
        "court=datasets/v8_real/court.jsonl",
    ]

    import torch
    from transformers import AutoTokenizer

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm
    from train_factor_model import _build_model
    from v8_judge import cls_to_worst, predict

    # [출처 prior] kappa 가 무는 대가는 공개문서의 S3 정탐이다. 그런데 공개문서는 본문을
    # 읽어서가 아니라 **출처로** S3 인 것이 맞다 — 비공지성은 문서 밖의 사실이고, 공개
    # 여부의 신뢰할 만한 증거는 본문 표현이 아니라 출처다. 그래서 kappa 와 출처 prior 는
    # 서로의 대가를 갚는다. 같이 재야 판단할 수 있다.
    def is_public(r: dict) -> bool:
        src = str(r.get("source") or r.get("label_source") or "")
        md = r.get("metadata") or {}
        src = str(md.get("source_type") or md.get("source") or src)
        return any(t in src for t in ("판례", "공시", "금융보고서", "보도자료", "공개"))

    tok = AutoTokenizer.from_pretrained(args.base)
    model = _build_model(args.base, torch, 4).cuda()
    model.load_state_dict(torch.load(Path(args.model) / "model.pt"))

    KAPPAS = (0.0, 0.50, 0.70, 0.80, 0.90, 0.95, 0.99, 1.01)
    out: dict = {"model": args.model, "kappas": list(KAPPAS)}
    for spec in sets:
        name, path = spec.split("=", 1)
        rows = [json.loads(l) for l in Path(path).read_text("utf-8").splitlines() if l.strip()]
        rows = [r for r in rows if (r.get("text") or r.get("content")) and r.get("label") in ORDER]
        texts = [r.get("text") or r.get("content") for r in rows]
        truth = [r["label"] for r in rows]
        preds, probs = predict(model, tok, texts, "cuda", args.max_len, args.batch,
                               want_probs=True)
        hi = [i for i, g in enumerate(truth) if g in ("TS", "S1")]
        lo = [i for i, g in enumerate(truth) if g == "S3"]

        print(f"\n=== {name} (n={len(rows)} · 고등급 {len(hi)} · 정답S3 {len(lo)})")
        print(f"{'kappa':>7s}{'등급일치':>9s}{'과소분류':>9s}{'S3추락':>8s}"
              f"{'S3정탐':>8s}{'absent수':>9s}")
        blk = []
        for kp in KAPPAS:
            cs = [apply_absent_tau(list(preds[i]), probs[i], kp) for i in range(len(rows))]
            g = [grade_from_svm(*[cls_to_worst(c) for c in c3]) for c3 in cs]
            if args.source_prior:
                g = ["S3" if is_public(rows[i]) else g[i] for i in range(len(rows))]
            under = [i for i in hi if ORDER[g[i]] > ORDER[truth[i]]]
            acc = sum(1 for a, b in zip(truth, g) if a == b) / len(rows)
            # S3 정탐 — 정답이 S3 인 것을 S3 로 맞힌 비율. absent 를 죽이면 이게 떨어진다.
            s3_rec = (sum(1 for i in lo if g[i] == "S3") / len(lo)) if lo else None
            n_abs = sum(1 for c3 in cs for c in c3 if c == CLS_ABSENT)
            rec = {"kappa": kp, "grade_acc": round(acc, 4),
                   "under_rate": round(len(under) / len(hi), 4) if hi else None,
                   "high_to_s3": len([i for i in hi if g[i] == "S3"]),
                   "s3_recall": round(s3_rec, 4) if s3_rec is not None else None,
                   "absent_n": n_abs, "pred_dist": dict(Counter(g))}
            blk.append(rec)
            print(f"{kp:>7.2f}{acc:>9.4f}{rec['under_rate'] if rec['under_rate'] is not None else 0:>9.4f}"
                  f"{rec['high_to_s3']:>8d}{(s3_rec if s3_rec is not None else 0):>8.3f}{n_abs:>9d}")
        out[name] = blk

    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\nkappa 를 올리면 business 과소분류는 내려가고 court/finance 의 S3 정탐이 내려간다.")
    print("둘의 교환이 얼마나 가파른지가 이 장치를 쓸지 말지를 정한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
