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
        if not text:
            continue
        code = _codes_from_row(row)
        if code is None:
            continue
        texts.append(text)
        levels.append(code)
        grades.append(row.get("label") or row.get("grade") or "")
    return texts, levels, grades


# ── 요소 클래스 인코딩 ────────────────────────────────────────────────────────
#
# v6 는 요소값 0/1/2 만 갖는다. v8 은 3상태(proven_absent · present · unknown)이고
# **그 구분이 S3 정책의 전제다** — "부재가 입증된 S3"만 자동확정 후보이고 "아무 말 없는
# S3"는 검수로 가야 한다. 0 과 unknown 을 한 클래스로 뭉치면 게이트가 둘을 구별할 수
# 없으므로 v8 에서는 헤드를 4-way 로 넓힌다.
#
#   0 proven_absent   1 present_lv1   2 present_lv2   3 unknown
#
# unknown 은 서수가 아니다(2 보다 크지도 작지도 않다). 그래서 MAE 는 클래스 인덱스가
# 아니라 **점수로 환산한 뒤** 잰다. 등급 산출도 마찬가지다.
CLS_ABSENT, CLS_LV1, CLS_LV2, CLS_UNKNOWN = 0, 1, 2, 3


def cls_to_score(c: int) -> int:
    """등급 산출용 점수. unknown 은 0 으로 본다(요소가 확인되지 않았다)."""
    return 0 if c in (CLS_ABSENT, CLS_UNKNOWN) else int(c)


def cls_to_worst(c: int) -> int:
    """보수적 완성 — unknown 을 최악값으로 채운다. S3-입증형 판정에 쓴다."""
    if c == CLS_ABSENT:
        return 0
    return 2 if c == CLS_UNKNOWN else int(c)


def _codes_from_row(row: dict) -> tuple[int, int, int] | None:
    """v8 3상태와 v6 요소값을 모두 받는다."""
    fl = row.get("factor_labels")
    if isinstance(fl, dict) and all(f in fl for f in FACTORS):
        out = []
        for f in FACTORS:
            st = fl[f].get("state")
            if st == "proven_absent":
                out.append(CLS_ABSENT)
            elif st == "unknown":
                out.append(CLS_UNKNOWN)
            elif st == "present":
                lv = int(fl[f].get("level") or 0)
                if lv not in (1, 2):
                    return None
                out.append(lv)
            else:
                return None
        return tuple(out)  # type: ignore[return-value]
    efs = row.get("expected_factor_scores")
    if isinstance(efs, dict) and all(f in efs for f in FACTORS):
        return tuple(int(efs[f]) for f in FACTORS)  # type: ignore[return-value]
    return None


def _build_model(base: str, torch, n_classes: int = 3):
    from transformers import AutoModel

    class FactorModel(torch.nn.Module):
        """공유 백본 + 요소별 3-way 헤드 3개."""

        def __init__(self) -> None:
            super().__init__()
            self.backbone = AutoModel.from_pretrained(base)
            hidden = self.backbone.config.hidden_size
            self.dropout = torch.nn.Dropout(0.1)
            # 헤드를 ModuleList 가 아니라 이름으로 두면 state_dict 가 읽기 쉬워진다.
            self.head_secrecy = torch.nn.Linear(hidden, n_classes)
            self.head_value = torch.nn.Linear(hidden, n_classes)
            self.head_management = torch.nn.Linear(hidden, n_classes)

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
    # v8 3상태를 쓰면 4-way 여야 한다. proven_absent 와 unknown 을 한 클래스로 뭉치면
    # "부재가 입증된 S3" 와 "아무 말 없는 S3" 를 구별할 수 없고, S3 자동확정 정책 전체가
    # 성립하지 않는다.
    parser.add_argument("--classes", type=int, default=3, choices=(3, 4))
    # [비대칭 손실] 실측 2026-08-13: 홀드아웃에서 value 오류가 낮게봄 737 · 높게봄 33 으로
    # 22배 쏠린다. 미탐은 전부 낮게 본 오류다. 그런데 CrossEntropy 는 두 방향을 똑같이
    # 취급한다 - 1차 목표가 미탐 최소화인데 학습이 그것을 모른다.
    parser.add_argument("--under-penalty", type=float, default=0.0,
                        help="미탐 방향 벌점 계수. 0 이면 기존 CrossEntropy 그대로")
    # [벌점을 어느 규칙으로 잴 것인가] 실측 2026-08-14: business 판정면 과소분류 52건 중
    # **45건(86.5%)이 secrecy=absent** 한 상태에서 나왔다. value 는 lv2 를 88건 맞히고
    # management 도 72건 맞히는데 secrecy 만 47건을 absent 로 단언한다.
    #
    # 왜 그런가. under_penalty 가 cls_to_score 를 쓰는데 그 표에서 **unknown 도 0** 이다.
    # 즉 "모른다" 를 "공개됐음이 입증됨" 과 똑같이 벌한다. 유보에 아무 이득이 없으니
    # 모델은 확신 없이도 absent 를 고른다.
    #
    # 그런데 사람에게 보이는 등급은 serving_grade() 가 만들고 거기서 unknown 은 최악값
    # 으로 채워진다 — 유보는 미탐을 만들지 않는다. 미탐을 만드는 것은 absent 단언뿐이다.
    # 손실은 **실제로 사람이 보는 등급을 만드는 규칙**으로 재야 한다.
    #
    #     정답 lv2 · 예측 unknown   저술 gap 2 (벌점)  서빙 gap 0 (무해)   ← 유보. 검수로 감
    #     정답 lv2 · 예측 absent    저술 gap 2 (벌점)  서빙 gap 2 (미탐)   ← 단언. 무음 통과
    parser.add_argument("--penalty-rule", default="serving", choices=("serving", "authoring"),
                        help="벌점 산정 규칙. serving 이면 unknown 유보에 벌점이 없다")
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

    model = _build_model(args.base, torch, args.classes).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    lossf = torch.nn.CrossEntropyLoss()

    # 클래스 -> 등급 점수. 이 순서로 "낮게 봄" 을 정의한다(unknown 은 등급 산출에서 0 점).
    _cls_fn = cls_to_worst if args.penalty_rule == "serving" else cls_to_score
    _score = torch.tensor([float(_cls_fn(c)) for c in range(args.classes)],
                          device=device)

    def under_penalty(logits, target):
        """예측 분포가 정답보다 **낮은 점수** 쪽에 실은 질량에 벌점을 준다.

        sum_j p_j * max(0, score(y) - score(j)) 이며 미분 가능하다. 과분류(높게 봄)에는
        벌점이 없다 - 안전 방향이라 억제할 이유가 없다.

        점수표는 --penalty-rule 이 고른다. 기본 serving 에서는 unknown 이 최악값이라
        유보에 벌점이 붙지 않는다 - 모르는 것을 모른다고 말할 길이 열린다.
        """
        p = torch.softmax(logits, dim=-1)
        gap = (_score[target].unsqueeze(1) - _score.unsqueeze(0)).clamp(min=0)
        return (p * gap).sum(dim=-1).mean()
    steps = args.epochs * len(dl_train)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps,
                                                pct_start=0.1)

    def evaluate(loader, grades=None):
        model.eval()
        correct = [0, 0, 0]
        abs_err = [0, 0, 0]
        n = 0
        grade_hit = 0
        s3_true = s3_hit = s3_false = 0
        with torch.no_grad():
            for i, (ids, mask, y) in enumerate(loader):
                ids, mask, y = ids.to(device), mask.to(device), y.to(device)
                logits = model(ids, mask)
                preds = [lg.argmax(-1) for lg in logits]
                for k in range(3):
                    correct[k] += int((preds[k] == y[:, k]).sum())
                for b in range(y.size(0)):
                    # MAE 와 등급은 **점수로 환산한 뒤** 잰다. 4-way 에서 unknown(3) 은
                    # 서수가 아니라 클래스 인덱스로 빼면 무의미한 값이 나온다.
                    ps = [cls_to_score(int(preds[k][b])) for k in range(3)]
                    ts = [cls_to_score(int(y[b, k])) for k in range(3)]
                    for k in range(3):
                        abs_err[k] += abs(ps[k] - ts[k])
                    if grades is not None:
                        idx = i * loader.batch_size + b
                        if idx < len(grades) and grade_from_svm(*ps) == grades[idx]:
                            grade_hit += 1
                    # S3-입증형 판정 — 보수적 완성 후에도 S3 인가. 자동확정 후보 판별이다.
                    pw = grade_from_svm(*[cls_to_worst(int(preds[k][b])) for k in range(3)])
                    tw = grade_from_svm(*[cls_to_worst(int(y[b, k])) for k in range(3)])
                    if tw == "S3":
                        s3_true += 1
                        if pw == "S3":
                            s3_hit += 1
                    elif pw == "S3":
                        s3_false += 1
                n += y.size(0)
        return {
            "n": n,
            "acc": {FACTORS[k]: round(correct[k] / n, 4) for k in range(3)},
            "mae": {FACTORS[k]: round(abs_err[k] / n, 4) for k in range(3)},
            "grade_acc": round(grade_hit / n, 4) if grades is not None else None,
            # 자동확정 후보 판별 성능. s3_false 는 **자동확정하면 안 되는 문서를 후보로
            # 올린 건수**라 미탐 위험에 직결된다.
            "s3_provable_recall": round(s3_hit / s3_true, 4) if s3_true else None,
            "s3_provable_false": s3_false,
        }

    best_dev: tuple[float, int, dict | None] = (float("inf"), -1, None)
    for ep in range(args.epochs):
        model.train()
        total = 0.0
        for step, (ids, mask, y) in enumerate(dl_train):
            ids, mask, y = ids.to(device), mask.to(device), y.to(device)
            logits = model(ids, mask)
            loss = sum(lossf(logits[k], y[:, k]) for k in range(3))
            if args.under_penalty > 0:
                loss = loss + args.under_penalty * sum(
                    under_penalty(logits[k], y[:, k]) for k in range(3))
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
