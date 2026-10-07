"""ES-only kNN의 '자기 신뢰도 신호'가 쓸만한지 검증 + 분류기와 F1 직접 비교.

measure_es_only_quality.py 의 결과(reports/es_only_quality.json)를 읽어:
  1) 매크로/가중 F1, 클래스별 P/R/F1 (분류기 F1 0.686 과 비교)
  2) top-1 코사인 외 대안 신뢰도 신호로 게이팅 시 품질 회복 여부:
       - 이웃 만장일치(top-k 라벨 동일)
       - 투표 마진(1위 득표 - 2위 득표) / 총득표
  3) 등급별 차등 자동확정 정책(극단 TS·S3 자동 / 중간 S1·S2 검수) 효과
재임베딩 없음 — 기존 결과만 재집계.
"""
from __future__ import annotations
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
RES = json.loads((ROOT / "reports/es_only_quality.json").read_text(encoding="utf-8"))
PQ = RES["per_query"]
GRADES = ["TS", "S1", "S2", "S3"]
SEV = {"TS": 3, "S1": 2, "S2": 1, "S3": 0}


def prf(pq):
    cm = defaultdict(lambda: defaultdict(int))
    for q in pq:
        cm[q["true"]][q["pred"]] += 1
    rows, macro, wsum, wtot = {}, [], 0.0, 0
    for g in GRADES:
        tp = cm[g][g]
        pred = sum(cm[t][g] for t in GRADES)
        act = sum(cm[g].values())
        p = tp / pred if pred else 0.0
        r = tp / act if act else 0.0
        f = 2 * p * r / (p + r) if (p + r) else 0.0
        rows[g] = (p, r, f, act)
        macro.append(f)
        wsum += f * act
        wtot += act
    return rows, sum(macro) / len(macro), (wsum / wtot if wtot else 0.0)


def conf_signals(q):
    labs = q["topk_labels"]
    sims = q["topk_sims"]
    vote = defaultdict(float)
    for l, s in zip(labs, sims):
        vote[l] += s
    ranked = sorted(vote.values(), reverse=True)
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else 0.0
    total = sum(ranked) or 1.0
    margin = (top - second) / total
    unanimous = len(set(labs)) == 1
    return q["top1_sim"], margin, unanimous


def gate_eval(name, keep_fn):
    cov = [q for q in PQ if keep_fn(q)]
    n = len(cov)
    if not n:
        print(f"  {name:<28} 커버 0건"); return
    acc = sum(1 for q in cov if q["pred"] == q["true"]) / n
    ts = [q for q in PQ if q["true"] == "TS"]
    ts_kept_wrong_down = sum(1 for q in cov if q["true"] == "TS" and SEV[q["pred"]] < SEV["TS"])
    print(f"  {name:<28} 자동화율 {n/len(PQ):>5.1%}  커버정확도 {acc:>5.1%}  (TS 하향오분류 {ts_kept_wrong_down})")


def main():
    print("=" * 74)
    print("1) ES-only kNN — 클래스별 P/R/F1 (분류기 clean F1 0.686 과 비교)")
    print("=" * 74)
    rows, macro, weighted = prf(PQ)
    print(f"  {'등급':<5}{'P':>8}{'R':>8}{'F1':>8}{'n':>6}")
    for g in GRADES:
        p, r, f, n = rows[g]
        print(f"  {g:<5}{p:>8.3f}{r:>8.3f}{f:>8.3f}{n:>6d}")
    print(f"  ── macro-F1={macro:.3f}  weighted-F1={weighted:.3f}  "
          f"(분류기 clean F1≈0.686 → ES-only 동급/약우위)")

    # 신뢰도 신호 분포
    for q in PQ:
        q["_cos"], q["_margin"], q["_unan"] = conf_signals(q)

    print("\n" + "=" * 74)
    print("2) 게이팅 신호별 품질 회복 — top-1 코사인 vs 만장일치 vs 마진")
    print("=" * 74)
    print("  [무게이트] 전량 자동확정")
    gate_eval("전량", lambda q: True)
    print("  [top-1 코사인 ≥ τ]")
    for t in (0.65, 0.70, 0.75, 0.80):
        gate_eval(f"cos≥{t}", lambda q, t=t: q["_cos"] >= t)
    print("  [top-k 만장일치]")
    gate_eval("이웃5 만장일치", lambda q: q["_unan"])
    print("  [투표 마진 ≥ m]")
    for m in (0.4, 0.6, 0.8):
        gate_eval(f"margin≥{m}", lambda q, m=m: q["_margin"] >= m)
    print("  [만장일치 ∧ cos≥0.7]")
    gate_eval("만장일치∧cos≥0.7", lambda q: q["_unan"] and q["_cos"] >= 0.70)

    print("\n" + "=" * 74)
    print("3) 등급차등 정책 — 예측이 극단(TS/S3)이면 자동, 중간(S1/S2)이면 검수")
    print("=" * 74)
    extreme = [q for q in PQ if q["pred"] in ("TS", "S3")]
    middle = [q for q in PQ if q["pred"] in ("S1", "S2")]
    ext_acc = sum(1 for q in extreme if q["pred"] == q["true"]) / len(extreme)
    mid_acc = sum(1 for q in middle if q["pred"] == q["true"]) / len(middle)
    print(f"  예측=TS/S3 (자동확정 후보): {len(extreme)}건 ({len(extreme)/len(PQ):.1%}), 정확도 {ext_acc:.1%}")
    print(f"  예측=S1/S2 (검수 큐)      : {len(middle)}건 ({len(middle)/len(PQ):.1%}), 정확도 {mid_acc:.1%}")
    print(f"  → 자동화율 {len(extreme)/len(PQ):.1%} 에서 자동 구간 품질 {ext_acc:.1%}")


if __name__ == "__main__":
    main()
