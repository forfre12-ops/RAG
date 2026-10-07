# -*- coding: utf-8 -*-
"""규정 참고 표시 — 사람 판정(도움·무용·오도) 집계.

왜 있나(2026-09-26). 검수 화면에 실제로 보일 항목을 사람이 읽고 판정한 값은 **읽은 사람에 따라 크게 달라졌다** —
같은 71건에서 도움이 13%·20%·35%, 오도가 14%·27%·32% 였다(가장 관대한 쪽이 이 기능을 설계한 사람). 그래서 판정은 여러 명이 따로 읽고,
이 도구로 한꺼번에 집계한다. 회원사 규정 파일럿(설계서 W-15)에서 같은 명령으로 다시 쓴다.

무엇을 내나
  ① 판정자별 도움·무용·오도 건수(분명한 것만 센 값 함께)
  ② 판정자 간 일치 — 세 갈래 일치 건수와 κ, '도움인가'·'오도인가' 두 갈래 일치
  ③ 3명 이상이면 다수결·전원 일치 건수
  ④ 사람이 조회 전에 정한 적용 조항(dump 의 hit1)과 판정의 교차, 조항별 표시·도움·오도
  ⑤ 조회 점수(dump 의 signals)가 도움과 그 밖(무용+오도), 오도와 그 밖을 가르는가 — AUROC, 그리고 문턱을 올릴 때의 모의

입력
  --dump     scripts/measure_regulation_runtime.py --dump-items 가 쓴 JSON (문서 순서 = 판정 번호)
  --readers  판정 JSON 여러 개. 파일 이름(확장자 뺀 것)이 판정자 이름. 형식 — {"0": "Hc", "1": "Ub", ...} 또는
             {"0": {"v": "Hc", "why": "..."}} 또는 {"verdicts": {...}}. 모든 문서(0~N-1)에 판정이 있어야 한다.
             판정 = H 도움 · U 무용 · M 오도(적용되지 않는데 다른 등급 쪽으로 끌 수 있는 표시) + 확신도 c(분명)·b(경계)

⚠ AUROC 는 0.5 가 '못 가름'이다. 문턱 모의는 **문턱을 올려도 도움 비율이 오르지 않으면 점수로 거를 수 없다**는 뜻이다.
⚠ 판정자 사이 차이가 크면 어느 한 사람의 값을 인용하지 말고 범위(가장 낮은 값~가장 높은 값)와 다수결을 함께 적는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

CLASSES = "HUM"
NAMES = {"H": "도움", "U": "무용", "M": "오도"}
TIE = "?"


def load_verdicts(path: Path, n: int) -> dict[int, str]:
    """판정 JSON 을 {문서 번호: 'Hc'} 로 읽는다. 번호 0~n-1 모두에 'H|U|M' + 'c|b' 가 있어야 한다."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw = raw.get("verdicts", raw) if isinstance(raw, dict) else raw
    out = {int(k): (x["v"] if isinstance(x, dict) else x) for k, x in raw.items() if str(k).isdigit()}
    bad = [i for i in range(n) if out.get(i, "")[:1] not in tuple(CLASSES) or out.get(i, "")[1:] not in ("c", "b")]
    if bad:
        raise ValueError(f"{path.name}: 판정이 없거나 형식이 다른 문서 {len(bad)}건(예: {bad[:5]}) — 'Hc'·'Ub'·'Mb' 형식으로 {n}건 모두")
    return {i: out[i] for i in range(n)}


def kappa(a: list[str], b: list[str], labels: str = CLASSES) -> float:
    """Cohen 의 κ — 우연히 겹칠 몫을 뺀 일치도(1=완전 일치, 0=우연 수준)."""
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pe = sum((a.count(k) / n) * (b.count(k) / n) for k in labels)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def auroc(pos: list[float], neg: list[float]) -> float:
    """양성 점수가 음성 점수보다 클 확률(동점은 반). 0.5=못 가름, 1.0=완전히 가름."""
    if not pos or not neg:
        return float("nan")
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))


def majority(votes: list[str]) -> str:
    """가장 많은 표의 분류. 1·2위가 같으면 TIE."""
    top = Counter(votes).most_common()
    return top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else TIE


def _features() -> dict:
    return {
        "dense_shown": lambda s: s["dense_shown"],
        "dense_gap": lambda s: s["dense_gap_to_second"],
        "sentence_cos": lambda s: s["sentence_cos"],
        "lex_shown": lambda s: s["lex_shown"],
        "lex_ratio": lambda s: s["lex_shown"] / s["lex_max"] if s["lex_max"] else 0.0,
        "-dense_rank": lambda s: -s["dense_rank"],
        "-lex_rank": lambda s: -s["lex_rank"],
        "both_top": lambda s: float(s["dense_rank"] == 0 and s["lex_rank"] == 0),
    }


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", required=True, help="measure_regulation_runtime.py --dump-items 결과 JSON")
    ap.add_argument("--readers", nargs="+", required=True, help="판정 JSON 파일들(파일 이름이 판정자 이름)")
    a = ap.parse_args(argv)

    dump = json.loads(Path(a.dump).read_text(encoding="utf-8"))
    n = len(dump)
    try:
        readers = {Path(p).stem: load_verdicts(Path(p), n) for p in a.readers}
    except ValueError as e:
        print(f"⛔ {e}")
        return 2
    names = list(readers)
    print(f"[입력] 문서 {n}건 · 판정자 {len(names)}명 {names}")

    for nm, v in readers.items():
        c, cl = Counter(s[0] for s in v.values()), Counter(s[0] for s in v.values() if s[1] == "c")
        print(f"  {nm}: " + " · ".join(f"{NAMES[k]} {c[k]}({c[k] / n:.0%}, 분명 {cl[k]})" for k in CLASSES))

    if len(names) >= 2:
        print("[판정자 간 일치 — 세 갈래(도움/무용/오도), 확신도는 무시]")
        for x, y in combinations(names, 2):
            va, vb = [readers[x][i][0] for i in range(n)], [readers[y][i][0] for i in range(n)]
            same = sum(p == q for p, q in zip(va, vb, strict=True))
            h = sum((p == "H") == (q == "H") for p, q in zip(va, vb, strict=True))
            m = sum((p == "M") == (q == "M") for p, q in zip(va, vb, strict=True))
            print(f"  {x}-{y}: 일치 {same}/{n} (κ={kappa(va, vb):.2f}) · '도움인가' 일치 {h}/{n} · '오도인가' 일치 {m}/{n}")

    labelsets = {nm: {i: v[i][0] for i in range(n)} for nm, v in readers.items()}
    if len(names) >= 3:
        cons = {i: majority([readers[nm][i][0] for nm in names]) for i in range(n)}
        c = Counter(cons.values())
        print(f"[다수결 {len(names)}명] " + " · ".join(f"{NAMES[k]} {c[k]}({c[k] / n:.0%})" for k in CLASSES) + f" · 동률 {c[TIE]}")
        allsame = sum(len({readers[nm][i][0] for nm in names}) == 1 for i in range(n))
        print(f"  모두 같은 문서 {allsame}/{n}")
        for k in ("H", "M"):
            every = sum(all(readers[nm][i][0] == k for nm in names) for i in range(n))
            anyone = sum(any(readers[nm][i][0] == k for nm in names) for i in range(n))
            print(f"  {NAMES[k]}: 모두 {every}건 · 한 명이라도 {anyone}건")
        labelsets["다수결"] = cons

    if all("hit1" in d for d in dump):
        print("[사람이 조회 전에 정한 적용 조항 × 판정]")
        for nm, v in readers.items():
            tab = {k: [0, 0] for k in CLASSES}
            for i, d in enumerate(dump):
                tab[v[i][0]][0 if d["hit1"] else 1] += 1
            print(f"  {nm}: " + " · ".join(f"{NAMES[k]} 적중 {t[0]}/비적중 {t[1]}" for k, t in tab.items()))

    ref = labelsets.get("다수결", labelsets[names[0]])
    by: dict[str, list[int]] = {}
    for i, d in enumerate(dump):
        by.setdefault(d["item"]["article_no"] if d["item"] else "(표시 없음)", []).append(i)
    print(f"[조항별 표시·판정 — {'다수결' if '다수결' in labelsets else names[0]} 기준]")
    for art, idx in sorted(by.items(), key=lambda kv: -len(kv[1])):
        c = Counter(ref[i] for i in idx)
        print(f"  {art}: {len(idx)}건 · 도움 {c['H']} · 무용 {c['U']} · 오도 {c['M']}")

    scored = [i for i, d in enumerate(dump) if d.get("signals")]
    if len(scored) == n:
        print("[조회 점수가 판정을 가르는가 — AUROC (0.5=못 가름)]")
        feats = _features()
        for nm, lab in labelsets.items():
            for target, title in (("H", "도움 vs 그 밖"), ("M", "오도 vs 그 밖")):
                row = []
                for fn_name, fn in feats.items():
                    pos = [fn(dump[i]["signals"]) for i, k in lab.items() if k == target]
                    neg = [fn(dump[i]["signals"]) for i, k in lab.items() if k not in (target, TIE)]
                    row.append(f"{fn_name}={auroc(pos, neg):.2f}")
                print(f"  {nm} {title}: " + " · ".join(row))
        ds = sorted(d["signals"]["dense_shown"] for d in dump)
        print(f"[문턱 모의] 밀집 점수(regulation_min_similarity 가 비교하는 값) 분포: 최소 {ds[0]:.3f} · 중앙 {ds[n // 2]:.3f} · 최대 {ds[-1]:.3f}")
        for nm, lab in labelsets.items():
            print(f"  {nm}: 점수 ≥ τ 인 문서만 보일 때")
            for q in (0.0, 0.2, 0.4, 0.6):
                tau = ds[int(q * (n - 1))]
                kept = [i for i in lab if dump[i]["signals"]["dense_shown"] >= tau and lab[i] != TIE]
                c, m = Counter(lab[i] for i in kept), max(1, len(kept))
                print(f"    τ={tau:.3f}: 보임 {len(kept)}/{n} · 도움 {c['H']}({c['H'] / m:.0%}) · 무용 {c['U']} · 오도 {c['M']}({c['M'] / m:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
