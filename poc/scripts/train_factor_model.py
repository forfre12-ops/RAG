"""S/V/M 3요소를 직접 예측하는 다중헤드 분류기 — 등급->요소 순환을 끊기 위한 실험.

왜(`docs/RULE_EXTRACTOR_DIAGNOSIS_2026-08-12.md`). 현재 룰 추출기의 s_lv/v_lv/m_lv 은
요소 근거가 아니라 content_grade(키워드 argmax)에서 **역산**된다. 그 S/V/M 이 다시
grade_from_svm() 에 들어가 등급이 되니 순환이다. 그래서 분포 밖 데이터에서 룰은 800건
전부에 S3 라는 상수를 낼 뿐이고, 합의 게이트가 믿는 "독립 근거"가 실제로는 없다.

시드 사전으로는 못 고친다는 것을 실측으로 확인했다(누산 점수 분산 0 · semantic 코사인이
음성 대조군과 겹침 · 임계 탐색이 상수 예측기로 수렴). 남은 길은 요소를 학습으로 뽑는
것이고, 다행히 라벨이 이미 있다 — v6 계열은 `expected_factor_scores` 를 전건 보유한다.

구조는 단순하다. 공유 백본 위에 헤드 3개(각 0/1/2 3-way)를 얹고 손실은 합이다. 등급은
예측한 요소를 grade_from_svm() 에 넣어 얻는다. 요소가 먼저 나오고 등급이 따라온다.

⚠ 이 스크립트는 **생산 trainer 를 건드리지 않는다.** m4_training.trainer 는 4등급
단일헤드에 특화돼 있고(MLflow·base model attestation·chunk 확장·sample weight) 거기에
헤드를 늘리면 배포 학습 경로가 흔들린다. 제안이 검증되기 전에는 분리해 둔다.

⚠ 과적합 방지:
    학습   labeled_v6_factor_grounded/train.jsonl  (1,833)
    검증   labeled_v6_factor_grounded/val.jsonl    (  455)
    확인   proxy_eval/.../development_200.jsonl    (  200)  중간 점검용
    판정   proxy_eval/.../final_800.locked.jsonl   (  800)  마지막에 한 번만
final_800 은 계보 독립이 확인된 셋이다(report_holdout_independence.py verdict True).
이 셋을 보고 하이퍼파라미터를 고치면 잠금이 풀린 것이니 하지 말 것.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

FACTORS = ("secrecy", "value", "management")
DEFAULT_BASE = "kakaobank/kf-deberta-base"


def _load(path: Path) -> tuple[list[str], list[tuple[int, int, int]], list[str]]:
    texts: list[str] = []
    levels: list[tuple[int, int, int]] = []
    grades: list[str] = []
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        text = row.get("text") or row.get("content") or ""
        efs = row.get("expected_factor_scores")
        if not text or not isinstance(efs, dict):
            continue
        texts.append(text)
        levels.append(tuple(int(efs[f]) for f in FACTORS))  # type: ignore[arg-type]
        grades.append(row.get("label") or row.get("grade") or "")
    return texts, levels, grades


def _build_model(base: str, torch):
    from transformers import AutoModel

    class FactorModel(torch.nn.Module):
        """공유 백본 + 요소별 3-way 헤드 3개."""

        def __init__(self) -> None:
            super().__init__()
            self.backbone = AutoModel.from_pretrained(base)
            hidden = self.backbone.config.hidden_size
            self.dropout = torch.nn.Dropout(0.1)
            # 헤드를 ModuleList 가 아니라 이름으로 두면 state_dict 가 읽기 쉬워진다.
            self.head_secrecy = torch.nn.Linear(hidden, 3)
            self.head_value = torch.nn.Linear(hidden, 3)
            self.head_management = torch.nn.Linear(hidden, 3)

        def forward(self, input_ids, attention_mask):
            out = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
            # DeBERTa 는 pooler 가 없을 수 있어 [CLS] 토큰을 직접 쓴다.
            cls = out.last_hidden_state[:, 0]
            cls = self.dropout(cls)
            return (
                self.head_secrecy(cls),
                self.head_value(cls),
                self.head_management(cls),
            )

    return FactorModel()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S/V/M 3헤드 요소 분류기 학습")
    parser.add_argument("--train", default="datasets/labeled_v6_factor_grounded/train.jsonl")
    parser.add_argument("--val", default="datasets/labeled_v6_factor_grounded/val.jsonl")
    # v6 val 은 학습셋과 같은 코퍼스라 1 epoch 만에 정확도 1.000 이 나온다 — 조기종료 신호를
    # 주지 못한다(v6 분류기 학습 때도 F1 1.000 이 나와 아무것도 못 알려줬다). 계보가 다른
    # development_200 을 별도 검증면으로 붙여 epoch 마다 재고, 그 기준으로 최적 체크포인트를
    # 고른다. final_800 은 여전히 판정 전용이라 여기에 넣지 않는다.
    parser.add_argument("--dev", default=None,
                        help="계보가 다른 검증셋 — 조기종료·체크포인트 선택 기준")
    # [절단] 실측 2026-08-13: v3 문서 중앙값이 1,085 토큰인데 창은 512 다. 세 요소의 신호가
    # 문서 곳곳에 흩어져 있어 한 창에 다 안 들어온다 — 앞부분만 먹이면 secrecy 0.160 /
    # management 0.040 은 좋은데 value 가 1.045 로 무너지고, §4 부터 먹이면 value 가 0.190
    # 으로 살아나는 대신 management 가 0.970 으로 무너진다. 학습을 문서 앞부분으로만 하면
    # 중간 청크가 분포 밖이라 추론 때 청크 집계를 해도 오히려 나빠진다(실측 평균 MAE
    # 0.478 -> 0.810). 생산 분류기(m5_inference)가 이미 청크 학습·집계를 하는 이유다.
    parser.add_argument("--chunk-chars", type=int, default=0,
                        help="0 이면 문서 앞부분만. >0 이면 그 길이로 슬라이딩 청크 확장")
    parser.add_argument("--chunk-overlap", type=int, default=300)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--max-len", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="artifacts/factor_model/v1")
    args = parser.parse_args(argv)

    # Windows 콘솔 기본 cp949 라 한글 지표 출력이 UnicodeEncodeError 로 죽는다.
    # 이 세션에서 같은 이유로 네 번 죽었다. 문자를 골라 쓰는 대신 스트림을 고친다.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    import torch
    from torch.utils.data import DataLoader, TensorDataset
    from transformers import AutoTokenizer, set_seed

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[device] {device}")

    tok = AutoTokenizer.from_pretrained(args.base)

    def encode(texts, levels):
        enc = tok(texts, truncation=True, max_length=args.max_len,
                  padding="max_length", return_tensors="pt")
        y = torch.tensor(levels, dtype=torch.long)
        return TensorDataset(enc["input_ids"], enc["attention_mask"], y)

    tr_x, tr_y, _ = _load(Path(args.train))
    va_x, va_y, va_g = _load(Path(args.val))
    print(f"[data] train {len(tr_x)} · val {len(va_x)}")

    if args.chunk_chars > 0:
        # 학습만 청크 확장한다. val/dev 는 문서 단위로 두어 평가 축을 바꾸지 않는다.
        # 각 청크는 문서의 요소 라벨을 그대로 물려받는다 — value 근거가 없는 청크에도
        # value=2 가 붙는 라벨 잡음이 생기지만, 생산 분류기가 등급에 대해 쓰는 것과
        # 같은 절충이고 추론 때 집계로 상쇄한다.
        cx, cy = [], []
        for text, lv in zip(tr_x, tr_y):
            i = 0
            while i < len(text):
                cx.append(text[i:i + args.chunk_chars])
                cy.append(lv)
                if i + args.chunk_chars >= len(text):
                    break
                i += args.chunk_chars - args.chunk_overlap
        print(f"[chunk] train {len(tr_x)} 문서 -> {len(cx)} 청크 "
              f"({args.chunk_chars}자 · 겹침 {args.chunk_overlap})")
        tr_x, tr_y = cx, cy

    dl_train = DataLoader(encode(tr_x, tr_y), batch_size=args.batch, shuffle=True)
    dl_val = DataLoader(encode(va_x, va_y), batch_size=args.batch)
    dl_dev = None
    dev_g: list[str] = []
    if args.dev:
        de_x, de_y, dev_g = _load(Path(args.dev))
        dl_dev = DataLoader(encode(de_x, de_y), batch_size=args.batch)
        # print 에 em dash 금지 (Windows cp949 콘솔에서 UnicodeEncodeError)
        print(f"[data] dev {len(de_x)} (계보 다름, 조기종료 기준)")

    model = _build_model(args.base, torch).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    lossf = torch.nn.CrossEntropyLoss()
    steps = args.epochs * len(dl_train)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps,
                                                pct_start=0.1)

    def evaluate(loader, grades=None):
        model.eval()
        correct = [0, 0, 0]
        abs_err = [0, 0, 0]
        n = 0
        grade_hit = 0
        with torch.no_grad():
            for i, (ids, mask, y) in enumerate(loader):
                ids, mask, y = ids.to(device), mask.to(device), y.to(device)
                logits = model(ids, mask)
                preds = [lg.argmax(-1) for lg in logits]
                for k in range(3):
                    correct[k] += int((preds[k] == y[:, k]).sum())
                    abs_err[k] += int((preds[k] - y[:, k]).abs().sum())
                if grades is not None:
                    for b in range(y.size(0)):
                        g = grade_from_svm(int(preds[0][b]), int(preds[1][b]), int(preds[2][b]))
                        idx = i * loader.batch_size + b
                        if idx < len(grades) and g == grades[idx]:
                            grade_hit += 1
                n += y.size(0)
        return {
            "n": n,
            "acc": {FACTORS[k]: round(correct[k] / n, 4) for k in range(3)},
            "mae": {FACTORS[k]: round(abs_err[k] / n, 4) for k in range(3)},
            "grade_acc": round(grade_hit / n, 4) if grades is not None else None,
        }

    best_dev: tuple[float, int, dict | None] = (float("inf"), -1, None)
    for ep in range(args.epochs):
        model.train()
        total = 0.0
        for step, (ids, mask, y) in enumerate(dl_train):
            ids, mask, y = ids.to(device), mask.to(device), y.to(device)
            logits = model(ids, mask)
            loss = sum(lossf(logits[k], y[:, k]) for k in range(3))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad()
            total += float(loss)
            if step % 40 == 0:
                print(f"  ep{ep + 1} step {step}/{len(dl_train)} loss {float(loss):.4f}")
        m = evaluate(dl_val, va_g)
        print(f"[epoch {ep + 1}] train_loss {total / len(dl_train):.4f} · "
              f"val acc {m['acc']} · MAE {m['mae']} · 등급 {m['grade_acc']}")
        if dl_dev is not None:
            d = evaluate(dl_dev, dev_g)
            mean_mae = sum(d["mae"].values()) / 3
            print(f"   [dev] MAE {d['mae']} · 평균 {mean_mae:.3f} · 등급 {d['grade_acc']}")
            # 요소 평균 MAE 가 최소인 시점을 남긴다 — v6 val 은 항상 1.000 이라 쓸 수 없다.
            if mean_mae < best_dev[0]:
                best_dev = (mean_mae, ep + 1, {k: v.detach().cpu().clone()
                                               for k, v in model.state_dict().items()})
                print(f"   [dev] 최적 갱신 (epoch {ep + 1})")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if best_dev[2] is not None and best_dev[1] != args.epochs:
        print(f"[select] epoch {best_dev[1]} 체크포인트 채택 (dev 평균 MAE {best_dev[0]:.3f}) "
              f"— 마지막 epoch 이 아니다")
        model.load_state_dict(best_dev[2])
    torch.save(model.state_dict(), out / "model.pt")
    meta = {
        "base_model": args.base,
        "factors": list(FACTORS),
        "levels": [0, 1, 2],
        "train": args.train,
        "val": args.val,
        "epochs": args.epochs,
        "batch": args.batch,
        "lr": args.lr,
        "max_len": args.max_len,
        "seed": args.seed,
        "val_metrics": evaluate(dl_val, va_g),
        "note": "등급은 grade_from_svm(예측 S,V,M) 으로 얻는다. final_800 은 판정용이므로 "
                "이 스크립트의 하이퍼파라미터를 그 셋을 보고 고치지 말 것.",
    }
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tok.save_pretrained(out)
    print(f"[saved] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
