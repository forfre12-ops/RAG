"""요소 하나를 정답으로 갈아끼워 **천장**을 잰다.

왜. business 판정면 과소분류 52건 중 45건이 secrecy=absent 에서 나왔다. 그러면 자연히
묻게 된다 — secrecy 만 완벽하면 되는가, 아니면 고쳐도 자동확정은 안 오르는가.

실측 없이는 답할 수 없다. 그래서 모델 예측에서 요소 하나만 **정답으로 대체**하고
나머지는 그대로 둔 채 등급과 자동확정을 다시 잰다. 차이가 그 요소의 몫이다.

정답 요소는 등급에서 역산한다. 정본이 곱셈이라 등급이 요소를 완전히 결정하지는 않지만
**하한은 준다**:

    TS  -> s=2 v=2 m>=1     (s==2 and v==2 and 곱>=4)
    S1  -> s=2 v=2 m==0     (s==2 and v==2 and 곱<4  -> m 이 0)
    S2  -> 곱>=1            (셋 다 1 이상. 최소 조합 1/1/1)
    S3  -> 곱==0            (하나 이상이 0)

그래서 "그 요소가 최소한 이 값이어야 한다" 를 오라클로 쓴다. 낙관적인 오라클이라
여기서 나온 수치는 **천장**이지 달성치가 아니다.

⚠ 이 스크립트는 학습에 아무 영향을 주지 않는다. 판정면을 소비하지도 않는다 — 요소
   라벨이 아니라 이미 있는 등급 라벨만 쓴다.
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
CLS = {"absent": 0, "lv1": 1, "lv2": 2, "unk": 3}


def oracle_min(grade: str, k: int) -> int | None:
    """등급이 요구하는 요소 k 의 **최소 클래스**. 결정 못 하면 None."""
    if grade == "TS":
        return 2 if k < 2 else 1          # s=2 v=2 m>=1
    if grade == "S1":
        return 2 if k < 2 else 0          # s=2 v=2 m=0
    if grade == "S2":
        return 1                          # 셋 다 1 이상
    return None                           # S3 는 어느 요소가 0 인지 모른다


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="요소별 천장 분석")
    ap.add_argument("--model", default="artifacts/factor_model/v8_sec")
    ap.add_argument("--base", default="kakaobank/kf-deberta-base")
    ap.add_argument("--data", default="datasets/v8_real/business_work.jsonl")
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--tau", type=float, default=0.99)
    ap.add_argument("--report", default="reports/V8_CEILING.json")
    args = ap.parse_args(argv)

    import torch
    from transformers import AutoTokenizer

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm
    from train_factor_model import _build_model
    from v8_judge import cls_to_worst, predict

    tok = AutoTokenizer.from_pretrained(args.base)
    model = _build_model(args.base, torch, 4).cuda()
    model.load_state_dict(torch.load(Path(args.model) / "model.pt"))

    rows = [json.loads(l) for l in Path(args.data).read_text("utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if (r.get("text") or r.get("content")) and r.get("label") in ORDER]
    texts = [r.get("text") or r.get("content") for r in rows]
    truth = [r["label"] for r in rows]
    preds, probs = predict(model, tok, texts, "cuda", args.max_len, args.batch, want_probs=True)

    hi = [i for i, g in enumerate(truth) if g in ("TS", "S1")]

    def score(cs: list[list[int]]) -> dict:
        g = [grade_from_svm(*[cls_to_worst(c) for c in c3]) for c3 in cs]
        under = [i for i in hi if ORDER[g[i]] > ORDER[truth[i]]]
        auto = [i for i in range(len(rows)) if min(max(d) for d in probs[i]) >= args.tau]
        a_miss = [i for i in auto if ORDER[g[i]] > ORDER[truth[i]]]
        return {
            "grade_acc": round(sum(1 for a, b in zip(truth, g) if a == b) / len(rows), 4),
            "under_rate": round(len(under) / len(hi), 4) if hi else None,
            "under_n": len(under),
            "auto_cov": round(len(auto) / len(rows), 4),
            "silent_miss": len(a_miss),
            "pred_dist": dict(Counter(g)),
        }

    out = {"model": args.model, "data": args.data, "n": len(rows), "tau": args.tau}
    base = score([list(c) for c in preds])
    out["as_is"] = base
    print(f"\n{args.data}  n={len(rows)} · 고등급 {len(hi)}\n")
    print(f"{'대체한 요소':16s}{'등급일치':>9s}{'과소분류':>9s}{'자동커버':>9s}{'무음미탐':>9s}")
    print(f"{'(없음·현재)':16s}{base['grade_acc']:>9.4f}{base['under_rate']:>9.4f}"
          f"{base['auto_cov']:>9.1%}{base['silent_miss']:>9d}")

    for k, f in enumerate(FACTORS):
        cs = [list(c) for c in preds]
        n_sub = 0
        for i, c3 in enumerate(cs):
            m = oracle_min(truth[i], k)
            if m is not None and cls_to_worst(c3[k]) < m:
                c3[k] = m
                n_sub += 1
        s = score(cs)
        out[f"oracle_{f}"] = {**s, "substituted": n_sub}
        print(f"{f + f'({n_sub}건)':16s}{s['grade_acc']:>9.4f}{s['under_rate']:>9.4f}"
              f"{s['auto_cov']:>9.1%}{s['silent_miss']:>9d}")

    # 세 요소 전부 오라클 — 등급 라벨이 요소를 결정하는 만큼의 완전한 천장
    cs = [list(c) for c in preds]
    for i, c3 in enumerate(cs):
        for k in range(3):
            m = oracle_min(truth[i], k)
            if m is not None and cls_to_worst(c3[k]) < m:
                c3[k] = m
    s = score(cs)
    out["oracle_all"] = s
    print(f"{'전부':16s}{s['grade_acc']:>9.4f}{s['under_rate']:>9.4f}"
          f"{s['auto_cov']:>9.1%}{s['silent_miss']:>9d}")
    print("\n자동커버는 conf 게이트만 본 값이라 요소를 갈아끼워도 거의 안 변한다 —")
    print("바뀌는 것은 그 커버 안에서 **무음 미탐이 남는지**다.")

    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
