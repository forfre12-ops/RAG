"""등급 표식 마스킹 A/B — 배포본 내부 지표가 무엇 위에 서 있었는지 가른다.

배경(실측 2026-08-14). 운영 학습셋 `labeled_p1_v5_clean` 2,554건 중 **1,273건(49.8%)** 이
본문에 등급 표식을 달고 있고, 표식만 보면 등급이 거의 결정된다:

    1급 비밀 380건 -> 100% S1 · 대외비 382건 -> 99% S2
    기밀 432건 -> 82% TS · 영업비밀 446건 -> 85% S1

배포본 v5 의 내부 지표(val F1 0.951)가 이 위에서 나온 값이다. 모델이 내용을 읽어 맞힌
것인지 표식을 읽어 맞힌 것인지 구분되지 않는다.

**같은 설정으로 원본과 마스킹본을 각각 학습해** 그 차이를 숫자로 만든다. 떨어지는 폭이
곧 표식이 떠받치던 몫이고, 남는 값이 사실상 첫 정직한 측정치다.

⚠ 두 학습은 base_model · seq_len · batch · epochs · lr · seed 를 전부 같게 둔다.
   하나라도 다르면 차이가 표식 때문인지 설정 때문인지 갈리지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="등급 표식 마스킹 A/B")
    ap.add_argument("--clean", default="datasets/labeled_p1_v5_clean")
    ap.add_argument("--masked", default="datasets/labeled_p1_v5_masked")
    ap.add_argument("--out", default="artifacts/ab_tell")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--max-seq-len", type=int, default=512)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--only", default=None, choices=("clean", "masked"),
                    help="한쪽만 돌린다 — 두 번에 나눠 실행할 때")
    ap.add_argument("--report", default="reports/AB_TELL_MASKING.json")
    args = ap.parse_args(argv)

    from koipa.modules.m4_training.trainer import TrainSpec, train_classifier

    arms = [("clean", args.clean), ("masked", args.masked)]
    if args.only:
        arms = [a for a in arms if a[0] == args.only]

    out: dict = {}
    rp = Path(args.report)
    if rp.exists():
        out = json.loads(rp.read_text("utf-8"))

    for name, ds in arms:
        print(f"\n{'=' * 60}\n[{name}] {ds}\n{'=' * 60}")
        spec = TrainSpec(
            train_path=f"{ds}/train.jsonl",
            val_path=f"{ds}/val.jsonl",
            test_path=f"{ds}/test.jsonl",
            output_dir=f"{args.out}/{name}",
            max_seq_len=args.max_seq_len,
            batch_size=args.batch,
            epochs=args.epochs,
            seed=args.seed,
        )
        rep = train_classifier(spec)
        d = rep if isinstance(rep, dict) else getattr(rep, "__dict__", {})
        # TrainReport 형태가 바뀌어도 지표만 뽑아 둔다
        keep = {k: v for k, v in d.items()
                if isinstance(v, (int, float, str, bool, dict, list)) and k != "confusion_matrix"}
        out[name] = keep
        print(json.dumps(keep, ensure_ascii=False, indent=2)[:1200])
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
        print(f"[saved] {rp}")

    if "clean" in out and "masked" in out:
        print(f"\n{'=' * 60}\nA/B 대조 — 표식이 떠받치던 몫\n{'=' * 60}")
        for k in ("f1_macro", "accuracy", "fnr_high", "fnr_overall"):
            a, b = out["clean"].get(k), out["masked"].get(k)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                print(f"  {k:14s} 원본 {a:.4f} -> 마스킹 {b:.4f}  ({b - a:+.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
