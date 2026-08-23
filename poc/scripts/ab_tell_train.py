"""등급 표식 마스킹 A/B — 최소 4등급 학습기.

왜 생산 trainer 를 안 쓰는가. `m4_training.trainer` 는 sklearn·datasets·pyarrow 등 전체
의존성을 끌어오는데 GPU venv 에 그 스택이 없고, 오프라인이라 설치가 막힌다(SSL 검증 실패).
의존성을 하나씩 복사하다 pyarrow DLL 에서 막혔다.

A/B 대조에 필요한 것은 **두 팔이 같은 코드를 쓰는 것**이지 생산 코드일 필요는 없다.
그래서 torch+transformers 만으로 도는 최소 학습기를 쓴다. 하이퍼파라미터는 생산 기본값을
그대로 맞춘다(max_seq_len 512 · batch 8 · lr 2e-5 · class_weighted · seed 42).

⚠ 한정. 이 수치는 배포본 v5 의 내부 지표(val F1 0.951)와 **직접 비교하면 안 된다** —
   학습기가 다르다. 비교 대상은 오직 **이 스크립트로 낸 clean 팔 vs masked 팔** 이다.
   그 차이가 등급 표식이 떠받치던 몫이다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

GRADES = ("TS", "S1", "S2", "S3")
IDX = {g: i for i, g in enumerate(GRADES)}
ORDER = {g: i for i, g in enumerate(GRADES)}   # 작을수록 높은 등급


def load(path: Path) -> tuple[list[str], list[int]]:
    xs, ys = [], []
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        t = r.get("text") or r.get("content") or ""
        g = r.get("label") or r.get("grade")
        if t and g in IDX:
            xs.append(t)
            ys.append(IDX[g])
    return xs, ys


def f1_macro(truth: list[int], pred: list[int]) -> float:
    tot = 0.0
    for c in range(4):
        tp = sum(1 for t, p in zip(truth, pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(truth, pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(truth, pred) if t == c and p != c)
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        tot += 2 * pr * rc / (pr + rc) if pr + rc else 0.0
    return tot / 4


def evaluate(model, tok, xs, ys, device, max_len, batch) -> dict:
    import torch

    model.eval()
    pred: list[int] = []
    with torch.no_grad():
        for i in range(0, len(xs), batch):
            enc = tok(xs[i:i + batch], truncation=True, max_length=max_len,
                      padding=True, return_tensors="pt")
            out = model(input_ids=enc["input_ids"].to(device),
                        attention_mask=enc["attention_mask"].to(device))
            pred.extend(out.logits.argmax(-1).tolist())
    hi = [i for i, y in enumerate(ys) if y in (0, 1)]           # TS·S1
    under = [i for i in hi if pred[i] > ys[i]]                  # 더 낮은 등급으로
    to_s3 = [i for i in hi if pred[i] == 3]
    return {
        "n": len(ys),
        "accuracy": round(sum(1 for a, b in zip(ys, pred) if a == b) / len(ys), 4),
        "f1_macro": round(f1_macro(ys, pred), 4),
        "high_n": len(hi),
        "fnr_high": round(len(under) / len(hi), 4) if hi else None,
        "high_to_s3": len(to_s3),
        "pred_dist": {GRADES[i]: sum(1 for p in pred if p == i) for i in range(4)},
    }


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="등급 표식 마스킹 A/B 학습")
    ap.add_argument("--data", required=True, help="train/val/test.jsonl 이 있는 디렉터리")
    ap.add_argument("--tag", required=True, help="clean 또는 masked")
    ap.add_argument("--base", default="kakaobank/kf-deberta-base")
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cross-eval", default=None,
                    help="학습 후 이 디렉터리의 val/test 로도 평가한다 — 교차 평가")
    ap.add_argument("--report", default="reports/AB_TELL_MASKING.json")
    args = ap.parse_args(argv)

    import torch
    from torch.utils.data import DataLoader, TensorDataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, set_seed

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    d = Path(args.data)
    tr_x, tr_y = load(d / "train.jsonl")
    va_x, va_y = load(d / "val.jsonl")
    te_x, te_y = load(d / "test.jsonl")
    print(f"[{args.tag}] {d} · train {len(tr_x)} · val {len(va_x)} · test {len(te_x)} · {device}")
    print(f"  등급 분포 {dict(Counter(GRADES[y] for y in tr_y))}")

    tok = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base, num_labels=4).to(device)

    enc = tok(tr_x, truncation=True, max_length=args.max_len, padding="max_length",
              return_tensors="pt")
    ds = TensorDataset(enc["input_ids"], enc["attention_mask"], torch.tensor(tr_y))
    dl = DataLoader(ds, batch_size=args.batch, shuffle=True)

    # 생산 기본값이 class_weighted=True 라 맞춘다. 등급 불균형이 커 이게 없으면 팔마다
    # 다른 편향이 생겨 대조가 흐려진다.
    cnt = Counter(tr_y)
    w = torch.tensor([len(tr_y) / (4 * max(1, cnt[i])) for i in range(4)],
                     dtype=torch.float32, device=device)
    lossf = torch.nn.CrossEntropyLoss(weight=w)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = args.epochs * len(dl)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps,
                                                pct_start=0.1)

    best = None
    for ep in range(args.epochs):
        model.train()
        tot = 0.0
        for i, (ids, mask, y) in enumerate(dl):
            ids, mask, y = ids.to(device), mask.to(device), y.to(device)
            out = model(input_ids=ids, attention_mask=mask)
            loss = lossf(out.logits, y)
            loss.backward()
            opt.step()
            sched.step()
            opt.zero_grad()
            tot += float(loss)
            if i % 50 == 0:
                print(f"  ep{ep+1} step {i}/{len(dl)} loss {float(loss):.4f}")
        va = evaluate(model, tok, va_x, va_y, device, args.max_len, args.batch)
        print(f"[epoch {ep+1}] train_loss {tot/len(dl):.4f} · val {va}")
        if best is None or va["f1_macro"] > best["val"]["f1_macro"]:
            best = {"epoch": ep + 1, "val": va,
                    "state": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}}

    model.load_state_dict(best["state"])
    te = evaluate(model, tok, te_x, te_y, device, args.max_len, args.batch)
    print(f"\n[{args.tag}] 채택 epoch {best['epoch']} · test {te}")

    cross = None
    if args.cross_eval:
        cd = Path(args.cross_eval)
        cv_x, cv_y = load(cd / "val.jsonl")
        ct_x, ct_y = load(cd / "test.jsonl")
        cross = {
            "data": str(cd),
            "val": evaluate(model, tok, cv_x, cv_y, device, args.max_len, args.batch),
            "test": evaluate(model, tok, ct_x, ct_y, device, args.max_len, args.batch),
        }
        print(f"[{args.tag}] 교차 평가 ({cd})")
        print(f"   val  {cross['val']}")
        print(f"   test {cross['test']}")

    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    out = json.loads(rp.read_text("utf-8")) if rp.exists() else {}
    out[args.tag] = {"data": str(d), "epoch": best["epoch"],
                     "val": best["val"], "test": te,
                     "cross": cross,
                     "hparams": {"base": args.base, "max_len": args.max_len,
                                 "batch": args.batch, "epochs": args.epochs,
                                 "lr": args.lr, "seed": args.seed}}
    rp.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"[saved] {rp}")

    if "clean" in out and "masked" in out:
        print(f"\n{'=' * 58}\n표식이 떠받치던 몫\n{'=' * 58}")
        for split in ("val", "test"):
            for k in ("f1_macro", "accuracy", "fnr_high"):
                a = out["clean"][split].get(k)
                b = out["masked"][split].get(k)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    print(f"  {split:5s} {k:11s} 원본 {a:.4f} -> 마스킹 {b:.4f}  ({b - a:+.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
