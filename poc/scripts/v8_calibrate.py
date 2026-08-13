"""요소별 온도 보정 — 게이트가 쓰는 신뢰도를 믿을 수 있게 만든다.

왜 필요한가(실측). 3차 모델은 임계 0.99 에서도 미탐이 19건 나온다. 그중 18건이 value 오류다.
**확신을 갖고 틀린다**는 뜻이고, 그러면 임계를 올려도 안 잡힌다 — 실제로 임계를 0.99 까지
올려도 미탐이 남았다(0.9995 에서 겨우 0 이 되는데 자동확정이 22건뿐이라 무의미하다).

게이트는 `min(head_conf) >= tau` 로 동작한다. 그 conf 가 실제 정확도를 반영하지 않으면
게이트는 아무것도 거르지 못한다. 온도 보정은 헤드별 스칼라 하나로 그 어긋남을 줄인다.

⚠ 보정면을 어디서 얻는가가 이 스크립트의 핵심 설계다.

    dev 는 쓸 수 없다. 미관측 형태지만 프레임을 다 봤기 때문에 MAE 0.000 이다. 오차가
    없으면 온도를 추정할 신호가 없다 — v6 가 val 정확도 1.000 때문에 온도가 0.05 로
    퇴화한 것과 같은 함정이다.

    판정면(holdout)도 쓸 수 없다. 거기서 온도를 맞추면 판정면이 튜닝면이 되어 잠금이 풀린다.

    그래서 **미관측 프레임 중 일부를 보정 전용으로 떼어낸다.** 판정면과 프레임이 겹치지
    않아야 한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FACTORS = ("secrecy", "value", "management")


def fit_temperature(logits, labels, torch, *, lo: float = 0.25, hi: float = 10.0,
                    steps: int = 200) -> float:
    """NLL 을 최소화하는 온도를 격자로 찾는다.

    경사하강 대신 격자 탐색을 쓰는 이유는 헤드가 3개뿐이고 1차원 문제라 안정성이 낫기
    때문이다. v6 에서 온도가 0.05 로 퇴화한 전례가 있어 하한을 둔다 — 1 미만은 신뢰도를
    **올리는** 방향이라 게이트에 위험하다.
    """
    best_t, best_nll = 1.0, float("inf")
    for i in range(steps + 1):
        t = lo + (hi - lo) * i / steps
        nll = torch.nn.functional.cross_entropy(logits / t, labels).item()
        if nll < best_nll:
            best_nll, best_t = nll, t
    return best_t


def ece(probs, correct, bins: int = 10) -> float:
    """Expected Calibration Error — 신뢰도와 실제 정확도의 평균 괴리."""
    tot = 0.0
    n = len(probs)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if (lo < p <= hi) or (b == 0 and p <= hi)]
        if not idx:
            continue
        conf = sum(probs[i] for i in idx) / len(idx)
        acc = sum(correct[i] for i in idx) / len(idx)
        tot += len(idx) / n * abs(conf - acc)
    return tot


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="요소별 온도 보정")
    ap.add_argument("--model", default="artifacts/factor_model/v8_wide")
    ap.add_argument("--calib", required=True, help="보정면 — 판정면과 프레임이 겹치면 안 된다")
    ap.add_argument("--base", default="kakaobank/kf-deberta-base")
    ap.add_argument("--classes", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--out", default=None, help="온도를 저장할 json. 미지정 시 model/temperature.json")
    args = ap.parse_args(argv)

    import torch
    from transformers import AutoTokenizer

    from train_factor_model import _build_model
    from v8_judge import row_codes

    rows = [json.loads(l) for l in Path(args.calib).read_text("utf-8").splitlines() if l.strip()]
    print(f"[calib] {args.calib} - {len(rows)}건")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(args.base)
    model = _build_model(args.base, torch, args.classes).to(device)
    model.load_state_dict(torch.load(Path(args.model) / "model.pt", map_location=device))
    model.eval()

    heads = [[] for _ in FACTORS]
    truth = [[] for _ in FACTORS]
    with torch.no_grad():
        for i in range(0, len(rows), args.batch):
            chunk = rows[i:i + args.batch]
            enc = tok([r["text"] for r in chunk], truncation=True, max_length=args.max_len,
                      padding=True, return_tensors="pt")
            out = model(enc["input_ids"].to(device), enc["attention_mask"].to(device))
            for k in range(3):
                heads[k].append(out[k].cpu())
            for r in chunk:
                c = row_codes(r)
                for k in range(3):
                    truth[k].append(c[k])

    temps: dict[str, float] = {}
    print(f"\n{'요소':11s}{'정확도':>8s}{'ECE 전':>9s}{'온도':>8s}{'ECE 후':>9s}   해석")
    for k, f in enumerate(FACTORS):
        lg = torch.cat(heads[k])
        lb = torch.tensor(truth[k])
        acc = (lg.argmax(-1) == lb).float().mean().item()
        p0 = torch.softmax(lg, -1).max(-1).values.tolist()
        corr = (lg.argmax(-1) == lb).int().tolist()
        e0 = ece(p0, corr)
        t = fit_temperature(lg, lb, torch)
        p1 = torch.softmax(lg / t, -1).max(-1).values.tolist()
        e1 = ece(p1, corr)
        temps[f] = round(t, 4)
        note = "과신 완화" if t > 1.05 else ("과소신뢰 보정" if t < 0.95 else "보정 불필요")
        print(f"{f:11s}{acc:>8.4f}{e0:>9.4f}{t:>8.4f}{e1:>9.4f}   {note}")

    dest = Path(args.out) if args.out else Path(args.model) / "temperature.json"
    dest.write_text(json.dumps({"per_factor": temps,
                                "calib_set": args.calib, "n": len(rows)},
                               ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[saved] {dest}")
    print("게이트에 반영하려면 v8_gate.py 가 이 온도로 나눈 확률을 쓰게 해야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
