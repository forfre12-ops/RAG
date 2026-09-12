"""v8 판정 실패 진단 — 어느 요소가 어떻게 무너졌는가.

판정 결과(reports/V8_JUDGE_holdout.json)가 (A) 문자열 암기였다. 요소별로 갈렸다:

    secrecy    정확 0.490 · MAE 0.767
    value      정확 0.492 · MAE 0.795
    management 정확 0.820 · MAE 0.245

management 만 살아 있다. 원인을 좁히려면 혼동행렬과 예측 분포를 봐야 한다.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FACTORS = ("secrecy", "value", "management")
NM = {0: "absent", 1: "lv1", 2: "lv2", 3: "unk"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="artifacts/factor_model/v8")
    ap.add_argument("--out", default="reports/V8_DIAGNOSE.json")
    args = ap.parse_args()
    import torch
    from transformers import AutoTokenizer

    from v8_judge import predict, row_codes

    from train_factor_model import _build_model

    tok = AutoTokenizer.from_pretrained("kakaobank/kf-deberta-base")
    model = _build_model("kakaobank/kf-deberta-base", torch, 4).cuda()
    model.load_state_dict(torch.load(f"{args.model}/model.pt", weights_only=True))
    print(f"[model] {args.model}")

    out: dict = {}
    for name in ("dev", "holdout_forms"):
        rows = [json.loads(l) for l in Path(f"datasets/v8/{name}.jsonl").read_text("utf-8").splitlines() if l.strip()]
        preds = predict(model, tok, [r["text"] for r in rows], "cuda", 768, 8)
        print(f"===== {name} (n={len(rows)})")
        blk: dict = {}
        for k, f in enumerate(FACTORS):
            cm = Counter()
            for i, p in enumerate(preds):
                cm[(row_codes(rows[i])[k], p[k])] += 1
            print(f"  [{f}]  행=정답 열=예측")
            print("            " + "".join(f"{NM[c]:>8s}" for c in range(4)))
            for t in range(4):
                tot = sum(cm[(t, c)] for c in range(4))
                if not tot:
                    continue
                line = "".join(f"{cm[(t, c)]:>8d}" for c in range(4))
                print(f"    {NM[t]:>8s}{line}   (n={tot})")
            blk[f] = {f"{NM[t]}->{NM[c]}": cm[(t, c)] for t in range(4) for c in range(4) if cm[(t, c)]}
        out[name] = blk

        # 쌍 예측이 동일한가 — "한 문장을 못 읽는다"의 직접 증거
        from collections import defaultdict
        P = defaultdict(list)
        for i, r in enumerate(rows):
            if r.get("pair_id"):
                P[r["pair_id"]].append(i)
        same = diff = 0
        same_factor = 0
        for idx in P.values():
            if len(idx) != 2:
                continue
            pa, pb = preds[idx[0]], preds[idx[1]]
            if pa == pb:
                same += 1
            else:
                diff += 1
            # 정답에서 달라야 하는 그 요소만 봤을 때 같은가
            ta, tb = row_codes(rows[idx[0]]), row_codes(rows[idx[1]])
            vk = [k for k in range(3) if ta[k] != tb[k]]
            if vk and pa[vk[0]] == pb[vk[0]]:
                same_factor += 1
        print(f"  쌍 {len(P)}개 — 요소예측 전부 동일 {same} · 다름 {diff} · "
              f"변경된 요소를 같게 본 쌍 {same_factor}")
        out[name]["pairs"] = {"n": len(P), "identical_pred": same,
                              "varied_factor_same": same_factor}
        print()

    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"[report] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
