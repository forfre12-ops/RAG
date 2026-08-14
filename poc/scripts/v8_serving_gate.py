"""실문서 전체에 **서빙 게이트 전체 구조**를 걸고 자동확정률과 무음 미탐을 잰다.

구조는 세 층이고 각 층이 서로의 약점을 갚는다.

  1층 출처   공개 출처면 S3 로 확정한다. 비공지성은 문서 밖의 사실이고, 공개 여부의
             신뢰할 만한 증거는 본문 표현이 아니라 **출처**다. 본문을 읽어서 "공개됐다"
             를 알아내려는 시도 자체가 잘못이었다.

  2층 단언    출처가 공개가 아니면 `absent` 는 p >= kappa 일 때만 채택한다. absent 는
     문턱     서빙 규칙에서 **무음 미탐을 만드는 유일한 클래스**다. 미달이면 unknown 으로
             내리고, 보수적 완성이 최악값으로 채워 검수로 보낸다.

  3층 확신    세 요소가 모두 확정(absent/lv1/lv2)이고 최소 확신이 tau 이상일 때만
     게이트   자동확정한다. unknown 이 하나라도 있으면 사람이 본다.

kappa 를 왜 tau 와 같은 값으로 두는가. 자동확정은 "사람 없이 통과시킨다" 는 결정이고
absent 단언은 "곱을 0 으로 만들어 S3 로 떨어뜨린다" 는 결정이다. **둘 다 사람 없이
문서를 통과시키는 결정**이므로 같은 증거 기준을 요구하는 것이 일관된다. 판정면 곡선을
보고 고른 값이 아니다 — 곡선에 맞춰 고르면 그 면이 소비된다.
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
CLS_ABSENT, CLS_LV1, CLS_LV2, CLS_UNKNOWN = 0, 1, 2, 3
# 하향 단언 - 이 클래스들이 고등급을 깬다. absent 는 곱을 0 으로 만들고 lv1 은
# s==2/v==2 조건을 깨 S2 를 상한으로 만든다. 둘 다 무음 미탐의 경로다.
DOWNGRADE = (CLS_ABSENT, CLS_LV1)
PUBLIC = ("판례", "공시", "금융보고서", "보도자료", "공개")


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (c + r) / d)


def is_public(r: dict) -> bool:
    src = str(r.get("source") or r.get("label_source") or "")
    md = r.get("metadata") or {}
    src = str(md.get("source_type") or md.get("source") or src)
    return any(t in src for t in PUBLIC)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="서빙 게이트 전체 구조 판정")
    ap.add_argument("--model", default="artifacts/factor_model/v8_sec")
    ap.add_argument("--base", default="kakaobank/kf-deberta-base")
    ap.add_argument("--tau", type=float, default=0.99)
    ap.add_argument("--kappa", type=float, default=0.99)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--kappa-lv1", action="store_true",
                    help="lv1 단언에도 문턱을 건다 - lv1 도 고등급을 깬다")
    ap.add_argument("--auto-top", action="store_true",
                    help="서빙 등급이 TS 면 확신 문턱 없이 확정한다 - 미탐 불가능")
    ap.add_argument("--unseal", action="store_true")
    ap.add_argument("--report", default="reports/V8_SERVING_GATE.json")
    args = ap.parse_args(argv)

    import torch
    from transformers import AutoTokenizer

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm
    from train_factor_model import _build_model
    from v8_judge import cls_to_worst, predict

    tok = AutoTokenizer.from_pretrained(args.base)
    model = _build_model(args.base, torch, 4).cuda()
    model.load_state_dict(torch.load(Path(args.model) / "model.pt"))

    sets = [("business_work", "datasets/v8_real/business_work.jsonl"),
            ("finance", "datasets/v8_real/finance.jsonl"),
            ("court", "datasets/v8_real/court.jsonl")]
    if args.unseal:
        sets.insert(1, ("business_sealed", "datasets/v8_real/business_sealed.jsonl"))
        print("⚠ 봉인 해제. 이 결과를 보고 학습을 고치면 이 면도 소비된다.\n")

    out: dict = {"model": args.model, "tau": args.tau, "kappa": args.kappa,
                 "unsealed": args.unseal}
    tot = {"n": 0, "auto": 0, "miss": 0, "auto_src": 0, "auto_conf": 0}
    print(f"tau {args.tau} · kappa {args.kappa}\n")
    print(f"{'면':16s}{'n':>5s}{'자동확정':>10s}{'무음미탐':>9s}{'미탐상한':>10s}"
          f"{'확정정확':>9s}{'과분류':>8s}{'이진정확':>9s}{'비밀놓침':>8s}{'헛경보':>8s}")
    for name, path in sets:
        rows = [json.loads(l) for l in Path(path).read_text("utf-8").splitlines() if l.strip()]
        rows = [r for r in rows if (r.get("text") or r.get("content")) and r.get("label") in ORDER]
        texts = [r.get("text") or r.get("content") for r in rows]
        truth = [r["label"] for r in rows]
        preds, probs = predict(model, tok, texts, "cuda", args.max_len, args.batch,
                               want_probs=True)

        grade: list[str] = []
        auto: list[int] = []
        why: list[str] = []
        for i in range(len(rows)):
            if is_public(rows[i]):                                   # 1층
                grade.append("S3"); auto.append(i); why.append("출처"); continue
            c3 = list(preds[i])
            for k in range(3):                                       # 2층
                # [하향 단언] 무음 미탐을 만드는 것은 absent 만이 아니다. 정본에서
                # TS/S1 은 s==2 and v==2 를 요구하므로 **lv1 단언도 고등급을 깬다**:
                #     s=1 v=2 m=2 -> 곱 4 이지만 s!=2 라 S2 가 상한
                # 실측 2026-08-14(r13): 수준 내용을 넣자 secrecy lv1 이 1건에서 24건
                # 으로 늘고 비밀놓침이 3 -> 23 으로 뛰었다. absent 만 지키는 문턱은
                # 이 경로를 못 막는다.
                #
                # 그래서 **하향 단언 전체**에 문턱을 건다. unknown 은 여전히 무료다 -
                # 유보는 보수적 완성이 받아 검수로 보낸다.
                low = DOWNGRADE if args.kappa_lv1 else (CLS_ABSENT,)
                if c3[k] in low and probs[i][k][c3[k]] < args.kappa:
                    c3[k] = CLS_UNKNOWN
            g = grade_from_svm(*[cls_to_worst(c) for c in c3])
            grade.append(g)
            settled = all(c != CLS_UNKNOWN for c in c3)               # 3층
            conf_ok = min(max(d) for d in probs[i]) >= args.tau
            # [최상단 확정] 미탐은 **낮게 본 오류**다. TS 는 최상단이라 TS 로 확정하면
            # 정의상 미탐이 될 수 없다 - 그보다 높은 등급이 없다. 그래서 TS 에는 확신
            # 문턱을 요구하지 않는다. 대가는 과분류이고 그것은 위험이 아니라 비용이다.
            #
            # 이 문을 열 때 반드시 함께 봐야 하는 것이 **과분류율**이다. 전부 TS 라고
            # 답하면 미탐 0 에 자동확정 100% 가 나오지만 아무 판단도 하지 않은 것이다.
            if args.auto_top and g == "TS":
                auto.append(i); why.append("최상단")
            elif settled and conf_ok:
                auto.append(i); why.append("확신")
            else:
                why.append("검수")

        miss = [i for i in auto if ORDER[grade[i]] > ORDER[truth[i]]]
        # 과분류 - 위험이 아니라 비용이다. 그러나 이것이 커지면 자동확정이 판단을 하지
        # 않고 전부 최상단으로 밀어 올린 것뿐이므로 반드시 함께 본다.
        over = [i for i in auto if ORDER[grade[i]] < ORDER[truth[i]]]
        exact = [i for i in auto if grade[i] == truth[i]]
        # [이진 판정] 4등급 일치는 S1 -> TS 를 오류로 센다. 그러나 운영에서 그 둘은
        # 같은 결정이다 - 둘 다 "영업비밀이니 보호하라" 다. 실제로 갈리는 결정은
        # **영업비밀인가 아닌가** 이고, 그것이 미탐이 문제가 되는 경계다.
        #
        # 그리고 정본상 S1 은 management 가 0 으로 확정될 때만 나오는데(조합 (2,2,0)
        # 하나뿐) 보수적 완성이 unknown 을 2 로 채우므로 **S1 은 도달 불가**다. 4등급
        # 지표는 그 도달 불가분을 전부 오류로 세어 성능을 구조적으로 깎는다.
        SECRET = ("TS", "S1")
        tb = [t in SECRET for t in truth]
        pb = [grade[i] in SECRET for i in range(len(rows))]
        bin_hit = sum(1 for i in range(len(rows)) if tb[i] == pb[i])
        bin_miss = [i for i in range(len(rows)) if tb[i] and not pb[i]]   # 놓친 영업비밀
        bin_over = [i for i in range(len(rows)) if pb[i] and not tb[i]]   # 헛경보
        bin_auto_miss = [i for i in auto if tb[i] and not pb[i]]
        n_src = sum(1 for w in why if w == "출처")
        n_cnf = sum(1 for w in why if w in ("확신", "최상단"))
        blk = {"n": len(rows), "auto_n": len(auto),
               "auto_rate": round(len(auto) / len(rows), 4),
               "silent_miss_n": len(miss),
               "silent_miss_95_upper": round(wilson_upper(len(miss), len(auto)), 5),
               "by_source": n_src, "by_conf": n_cnf,
               "auto_exact": len(exact), "auto_over": len(over),
               "binary": {
                   "acc": round(bin_hit / len(rows), 4),
                   "missed_secret_n": len(bin_miss),
                   "missed_secret_rate": round(len(bin_miss) / max(1, sum(tb)), 4),
                   "false_alarm_n": len(bin_over),
                   "auto_missed_secret_n": len(bin_auto_miss),
                   "auto_missed_95_upper": round(wilson_upper(len(bin_auto_miss), len(auto)), 5),
               },
               "auto_exact_rate": round(len(exact) / len(auto), 4) if auto else None,
               "grade_acc": round(sum(1 for a, b in zip(truth, grade) if a == b) / len(rows), 4),
               "pred_dist": dict(Counter(grade)), "truth_dist": dict(Counter(truth))}
        out[name] = blk
        print(f"{name:16s}{len(rows):>5d}{blk['auto_rate']:>10.1%}{len(miss):>9d}"
              f"{blk['silent_miss_95_upper']:>10.5f}"
              f"{(blk['auto_exact_rate'] or 0):>9.3f}{len(over):>8d}"
              f"{blk['binary']['acc']:>9.3f}{len(bin_miss):>8d}{len(bin_over):>8d}")
        tot["n"] += len(rows); tot["auto"] += len(auto); tot["miss"] += len(miss)
        tot["auto_src"] += n_src; tot["auto_conf"] += n_cnf
        tot["exact"] = tot.get("exact", 0) + len(exact)
        tot["over"] = tot.get("over", 0) + len(over)

    tot["auto_rate"] = round(tot["auto"] / tot["n"], 4)
    tot["silent_miss_95_upper"] = round(wilson_upper(tot["miss"], tot["auto"]), 5)
    out["total"] = tot
    print(f"\n{'전체':16s}{tot['n']:>5d}{tot['auto_rate']:>10.1%}{tot['miss']:>9d}"
          f"{tot['silent_miss_95_upper']:>10.5f}"
          f"{(tot['exact'] / tot['auto'] if tot['auto'] else 0):>9.3f}{tot['over']:>8d}")
    print("")
    print(f"확정 경로  출처 {tot['auto_src']}건 · 확신/최상단 {tot['auto_conf']}건")
    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
