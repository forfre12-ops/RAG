"""v8 판정 — 사전등록한 지표를 그대로 잰다.

`docs/V8_PREREGISTERED_CRITERIA.md` §2 의 순서를 따른다. **미탐이 F1 보다 앞선다.**

    1  고등급 과소분류율
    2  형태별 최악 성능 (홀드아웃 형태 각각 - 평균으로 가리지 않는다)
    3  자동확정 후보 판별 (S3-입증형 recall 과 오탐)
    4  요소별 MAE (점수 환산 후 - unknown 은 서수가 아니다)
    5  F1_macro

부가 관측(판정 조건은 아니나 반드시 기록):

    counterfactual 쌍 정합률   쌍은 한 문장만 다르다. 양쪽을 다 맞혀야 경계를 배운 것이다.
                              한쪽만 맞히면 사전분포로 찍는 것과 구분되지 않는다.
    언어 함정별 성적            함정이 있는 문서에서 떨어지면 어휘에 반응하는 것이다
    계보별 성적                prose·terse·field - 문체를 가로질러 유지되는가
    S3 유형별                  입증형이 무신호형보다 높아야 한다. 반대면 부재 입증 문장을
                              못 읽는 것이고 S3 자동확정 정책 전체가 성립하지 않는다
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

NL = chr(10)
FACTORS = ("secrecy", "value", "management")
GRADES = ("TS", "S1", "S2", "S3")
ORDER = {g: i for i, g in enumerate(GRADES)}   # 작을수록 높은 등급


def cls_to_score(c: int) -> int:
    return 0 if c in (0, 3) else int(c)


def cls_to_worst(c: int) -> int:
    return 0 if c == 0 else (2 if c == 3 else int(c))


def row_codes(row: dict) -> tuple[int, int, int]:
    fl = row["factor_labels"]
    out = []
    for f in FACTORS:
        st = fl[f]["state"]
        out.append(0 if st == "proven_absent" else 3 if st == "unknown" else int(fl[f]["level"]))
    return tuple(out)  # type: ignore[return-value]


def f1_macro(truth: list[str], pred: list[str]) -> float:
    tot = 0.0
    for g in GRADES:
        tp = sum(1 for t, p in zip(truth, pred) if t == g and p == g)
        fp = sum(1 for t, p in zip(truth, pred) if t != g and p == g)
        fn = sum(1 for t, p in zip(truth, pred) if t == g and p != g)
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        tot += 2 * pr * rc / (pr + rc) if pr + rc else 0.0
    return tot / len(GRADES)


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    """이항 비율의 95% 상한. k=0 일 때도 0 이 아니라 표본수에 맞는 상한을 준다.

    "미탐 0건" 을 표본수 없이 읽으면 안 된다 - 10건에서의 0 과 600건에서의 0 은 전혀
    다른 증거다(사전등록 §3).
    """
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (c + r) / d)


def predict(model, tok, texts: list[str], device, max_len: int, batch: int,
            want_probs: bool = False):
    """예측 클래스와(선택) 헤드별 최대확률을 돌려준다.

    확률을 같이 뽑는 이유는 게이트 시뮬레이션 때문이다. 추론을 두 번 돌리면 2시간짜리
    사이클이 또 늘어나므로 한 번에 뽑아 기록으로 남긴다.
    """
    import torch

    out: list[tuple[int, int, int]] = []
    probs: list[tuple[float, float, float]] = []
    model.eval()
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            enc = tok(texts[i:i + batch], truncation=True, max_length=max_len,
                      padding=True, return_tensors="pt")
            logits = model(enc["input_ids"].to(device), enc["attention_mask"].to(device))
            preds = [lg.argmax(-1).tolist() for lg in logits]
            out.extend(zip(preds[0], preds[1], preds[2]))
            if want_probs:
                # **전체 분포**를 남긴다. 최대확률만 남기면 게이트가 "lv1 과 lv2 가 접전"
                # 같은 경계 불확실성을 못 본다. 4차 미탐 5건이 전부 lv2->lv1 이었고,
                # 그 경계가 등급을 뒤집는다((2,2,1)=TS vs (1,2,1)=S2).
                sm = [torch.softmax(lg, dim=-1).tolist() for lg in logits]
                probs.extend(zip(sm[0], sm[1], sm[2]))
    return (out, probs) if want_probs else out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="v8 판정 - 사전등록 지표")
    ap.add_argument("--model", default="artifacts/factor_model/v8")
    ap.add_argument("--eval", default="datasets/v8/holdout_forms.jsonl")
    ap.add_argument("--base", default="kakaobank/kf-deberta-base")
    ap.add_argument("--classes", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--report", default=None)
    ap.add_argument("--records", default=None,
                    help="문서별 예측·확률 기록 — 게이트 시뮬레이션 입력")
    args = ap.parse_args(argv)

    import torch
    from transformers import AutoTokenizer

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm
    from train_factor_model import _build_model

    rows = [json.loads(l) for l in Path(args.eval).read_text("utf-8").splitlines() if l.strip()]
    print(f"[eval] {args.eval} - {len(rows)}건")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(args.base)
    model = _build_model(args.base, torch, args.classes).to(device)
    model.load_state_dict(torch.load(Path(args.model) / "model.pt", map_location=device, weights_only=True))

    preds, probs = predict(model, tok, [r["text"] for r in rows], device,
                           args.max_len, args.batch, want_probs=True)

    truth_g = [r["label"] for r in rows]
    pred_g = [grade_from_svm(*[cls_to_score(c) for c in p]) for p in preds]

    def slice_stats(idx: list[int]) -> dict:
        if not idx:
            return {"n": 0}
        t = [truth_g[i] for i in idx]
        p = [pred_g[i] for i in idx]
        hi = [i for i in idx if truth_g[i] in ("TS", "S1")]
        un = [i for i in hi if ORDER[pred_g[i]] > ORDER[truth_g[i]]]
        return {
            "n": len(idx),
            "acc": round(sum(1 for a, b in zip(t, p) if a == b) / len(idx), 4),
            "f1_macro": round(f1_macro(t, p), 4),
            "high_n": len(hi),
            "under_n": len(un),
            "under_rate": round(len(un) / len(hi), 4) if hi else None,
        }

    def group(keyfn) -> dict:
        g: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(rows):
            k = keyfn(r)
            if k is not None:
                g[str(k)].append(i)
        return {k: slice_stats(v) for k, v in sorted(g.items())}

    # 1. 고등급 과소분류
    high_idx = [i for i, g in enumerate(truth_g) if g in ("TS", "S1")]
    under = [i for i in high_idx if ORDER[pred_g[i]] > ORDER[truth_g[i]]]
    to_s3 = [i for i in high_idx if pred_g[i] == "S3"]

    # 3. S3 자동확정 후보 판별 - 보수적 완성 후에도 S3 인가
    s3_hit = s3_true = s3_false = 0
    for i, p in enumerate(preds):
        tw = grade_from_svm(*[cls_to_worst(c) for c in row_codes(rows[i])])
        pw = grade_from_svm(*[cls_to_worst(c) for c in p])
        if tw == "S3":
            s3_true += 1
            s3_hit += int(pw == "S3")
        elif pw == "S3":
            s3_false += 1

    # 4. 요소별
    mae, exact = {}, {}
    for k, f in enumerate(FACTORS):
        e = s = 0
        for i, p in enumerate(preds):
            tc = row_codes(rows[i])
            s += abs(cls_to_score(p[k]) - cls_to_score(tc[k]))
            e += int(p[k] == tc[k])
        mae[f] = round(s / len(rows), 4)
        exact[f] = round(e / len(rows), 4)

    # 부가: counterfactual 쌍
    pairs: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if r.get("pair_id"):
            pairs[r["pair_id"]].append(i)
    both = one = 0
    for idx in pairs.values():
        if len(idx) != 2:
            continue
        hits = sum(1 for i in idx if pred_g[i] == truth_g[i])
        both += int(hits == 2)
        one += int(hits == 1)

    report = {
        "eval_set": args.eval,
        "model": args.model,
        "n": len(rows),
        "1_high_grade": {
            "high_n": len(high_idx),
            "under_n": len(under),
            "under_rate": round(len(under) / len(high_idx), 4) if high_idx else None,
            "under_rate_95_upper": round(wilson_upper(len(under), len(high_idx)), 4),
            "to_s3_n": len(to_s3),
        },
        "2_by_form": group(lambda r: r["form_id"]),
        "3_s3_provable": {
            "true_n": s3_true,
            "recall": round(s3_hit / s3_true, 4) if s3_true else None,
            "false_n": s3_false,
            "false_rate_95_upper": round(wilson_upper(s3_false, max(1, len(rows) - s3_true)), 4),
        },
        "4_factor": {"mae": mae, "exact": exact},
        "5_overall": slice_stats(list(range(len(rows)))),
        "extra_pairs": {
            "pair_n": len(pairs),
            "both_correct": both,
            "one_correct": one,
            "consistency": round(both / len(pairs), 4) if pairs else None,
        },
        "extra_by_lineage": group(lambda r: r.get("lineage")),
        "extra_by_s3_kind": group(lambda r: r.get("s3_kind")),
        "extra_by_trap": group(lambda r: "trap=%s" % bool(r.get("has_language_trap"))),
    }

    if args.records:
        with Path(args.records).open("w", encoding="utf-8") as fh:
            for i, r in enumerate(rows):
                fh.write(json.dumps({
                    "doc_id": r["doc_id"], "form_id": r["form_id"],
                    "truth": truth_g[i], "pred": pred_g[i],
                    "truth_codes": list(row_codes(r)), "pred_codes": list(preds[i]),
                    "head_conf": [round(max(d), 6) for d in probs[i]],
                    "head_dist": [[round(x, 6) for x in d] for d in probs[i]],
                    "s3_kind": r.get("s3_kind"), "pair_id": r.get("pair_id"),
                    "lineage": r.get("lineage"),
                }, ensure_ascii=False) + NL)
        print(f"[records] {args.records}")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print(f"[report] {args.report}")

    print()
    print("사전등록 예측 대조")
    f1 = report["5_overall"].get("f1_macro") or 0.0
    verdict = "(B) 사실을 읽는다" if f1 >= 0.85 else ("부분 성공" if f1 >= 0.60 else "(A) 문자열을 외웠다")
    print(f"  홀드아웃 F1_macro {f1:.4f}  ->  {verdict}")
    cons = report["extra_pairs"]["consistency"] or 0.0
    pv = "경계를 배웠다" if cons >= 0.80 else ("한쪽만 맞힘 = 사전분포와 구분 불가" if cons <= 0.60 else "중간")
    print(f"  쌍 정합률 {cons:.4f}  ->  {pv}")
    sk = report["extra_by_s3_kind"]
    if "S3-입증형" in sk and "S3-무신호형" in sk:
        a, b = sk["S3-입증형"].get("acc"), sk["S3-무신호형"].get("acc")
        if a is not None and b is not None:
            note = "정상" if a > b else "역전 - 부재 입증 문장을 못 읽는다"
            print(f"  S3 입증형 {a:.4f} vs 무신호형 {b:.4f}  ->  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
