#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""판례 라벨 교정본(v6)이 왜 고등급 문서를 S3 로 떨어뜨리는지 가른다.

왜 이 도구가 있는가(2026-09-10). 감리 별첨 185(가)는 "공개 판례문을 S3 로 정정하라"고
권고했고, 그 교정본 v6(v-ca93043a)은 공개 S3 과분류를 0.310→0.177 로 줄였다. 그런데
정리된 골든 후보 1,055건에서는 고등급→S3 무음 미탐이 16건→83건으로 5배가 됐다.
권고를 그대로 따르면 이 사업의 1차 목표(미탐 최소화)가 나빠진다. 어느 문서가
왜 떨어졌는지 모르면 고칠 자리를 정할 수 없다.

가르는 것:
    flip      정답 TS·S1 · v5 는 S3 아님 · v6 는 S3      ← v6 가 새로 만든 미탐
    both      정답 TS·S1 · v5·v6 둘 다 S3                ← 원래 있던 미탐
    kept      정답 TS·S1 · v6 도 S3 아님                  ← 대조군
각 군에서 길이 · 판결문 표지 · 법률 어휘 · 배치/번호대 · 범주형 필드 분포를 비교한다.

⚠ 정답은 생성 시 의도 등급이고 사람 확정 0건이다. 이 도구는 "v6 가 어떤 문서를
  다르게 보는가"까지만 말한다. 정확도로 인용하지 말 것.

사용:
    python scripts/eval_on_clean_candidates.py --model <v5> --model <v6> --cases tmp/cases.json
    python scripts/measure_court_fix_miss_shift.py --cases tmp/cases.json [--json out.json]
"""
from __future__ import annotations

import argparse
import io
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "src"))

HIGH = ("TS", "S1")
# 판결문 서식 표지 — v6 가 교정한 대상(띄어 쓴 표제 포함)
COURT = re.compile(r"【\s*[가-힣\s]{1,8}】|원\s*고|피\s*고|주\s*문|판\s*결|선\s*고|항\s*소|상\s*고|법\s*원|사\s*건\s*번\s*호|청구취지")
# 판결문이 아니어도 법률 문서에 흔한 어휘
LEGAL = re.compile(r"소송|분쟁|가처분|손해배상|위반|계약|경업금지|비밀유지|침해|합의|조항|법무|특허|심판")


def _suffix_band(doc_id: str) -> str:
    m = re.search(r"-(TS|S1|S2|S3)-(\d+)$", doc_id or "")
    if not m:
        return "(번호 없음)"
    n = int(m.group(2))
    lo = (n - 1) // 10 * 10 + 1
    return f"{m.group(1)}-{lo:03d}~{lo + 9:03d}"


def _batch(doc_id: str) -> str:
    m = re.match(r"(GOLD-[A-Z0-9]+)", doc_id or "")
    return m.group(1) if m else "(기타)"


def _feat(text: str) -> dict:
    return {"len": len(text), "court": len(COURT.findall(text)), "legal": len(LEGAL.findall(text))}


# 합성 생성기가 채운 슬롯 — 문서유형(## 제목) · 대상 · 문제. 떨어진 문서가 특정 시나리오에
# 몰리는지 보려면 번호대보다 이 값이 직접적이다(번호대는 시나리오의 대리 지표일 뿐이다).
SLOT_PATTERNS = {
    "유형": re.compile(r"^##\s*(.+)$", re.M),
    "대상": re.compile(r"대상은 \*\*(.+?)\*\*"),
    "문제": re.compile(r"문제는 \*\*(.+?)\*\*"),
}


def _slots(text: str) -> dict:
    out = {}
    for k, pat in SLOT_PATTERNS.items():
        m = pat.search(text)
        out[k] = m.group(1).strip()[:40] if m else "-"
    return out


def _summ(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    f = [_feat(r["text"]) for r in rows]
    return {
        "n": len(rows),
        "len_median": int(statistics.median(x["len"] for x in f)),
        "court_any_pct": round(100 * sum(x["court"] > 0 for x in f) / len(f), 1),
        "court_mean": round(statistics.mean(x["court"] for x in f), 2),
        "legal_any_pct": round(100 * sum(x["legal"] > 0 for x in f) / len(f), 1),
        "legal_mean": round(statistics.mean(x["legal"] for x in f), 2),
    }


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="v6 미탐 이동 분석")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--old", default="v-fe4b386b")
    ap.add_argument("--new", default="v-ca93043a")
    ap.add_argument("--json", default="")
    ap.add_argument("--examples", type=int, default=6)
    ap.add_argument("--train", action="append", default=[],
                    help="학습셋 train.jsonl(여러 번) — 떨어진 '대상' 문구가 어느 등급으로 배워졌는지 센다")
    ap.add_argument("--swap-model", default="",
                    help="개입 실험 — 대상 문구만 바꿔 끼워 이 모델로 다시 예측한다(상관이 아니라 원인인지)")
    a = ap.parse_args(argv)

    from eval_on_clean_candidates import load_candidates  # noqa: PLC0415

    cases = {m["name"]: {c["doc_id"]: c for c in m["cases"]}
             for m in json.loads(Path(a.cases).read_text(encoding="utf-8"))}
    old, new = cases[a.old], cases[a.new]
    rows = {r["doc_id"]: r for r in load_candidates()}

    groups: dict[str, list[dict]] = {"flip": [], "both": [], "kept": []}
    for did, c6 in new.items():
        c5 = old.get(did)
        r = rows.get(did)
        if not c5 or not r or c6["gold"] not in HIGH:
            continue
        if c6["pred"] == "S3" and c5["pred"] != "S3":
            groups["flip"].append(r | {"v5": c5["pred"]})
        elif c6["pred"] == "S3":
            groups["both"].append(r | {"v5": c5["pred"]})
        else:
            groups["kept"].append(r | {"v5": c5["pred"], "v6": c6["pred"]})

    out: dict = {"denominator_high": sum(len(g) for g in groups.values())}
    print(f"정답 TS·S1 문서 {out['denominator_high']}건 (정리된 골든 후보 기준)")
    print(f"{'군':6s} {'건':>4s} {'길이중앙':>8s} {'판결표지%':>9s} {'판결표지평균':>11s} {'법률어휘%':>9s} {'법률어휘평균':>11s}")
    for g, rs in groups.items():
        s = _summ(rs)
        out[g] = s
        if s["n"]:
            print(f"{g:6s} {s['n']:4d} {s['len_median']:8d} {s['court_any_pct']:9.1f} "
                  f"{s['court_mean']:11.2f} {s['legal_any_pct']:9.1f} {s['legal_mean']:11.2f}")

    for key, fn in (("band", lambda r: _suffix_band(r["doc_id"])), ("batch", lambda r: _batch(r["doc_id"])),
                    ("gold", lambda r: r["label"]),
                    ("대상", lambda r: _slots(r["text"])["대상"]),
                    ("문제", lambda r: _slots(r["text"])["문제"]),
                    ("유형", lambda r: _slots(r["text"])["유형"])):
        print(f"\n[{key}] 군별 분포 (flip / both / kept)")
        dist = {g: Counter(fn(r) for r in rs) for g, rs in groups.items()}
        keys = sorted(set().union(*dist.values()), key=lambda k: -(dist["flip"][k] + dist["both"][k]))
        out[key] = {k: {g: dist[g][k] for g in groups} for k in keys}
        for k in keys[:20]:
            tot = sum(dist[g][k] for g in groups)
            print(f"  {k:22s} {dist['flip'][k]:4d} {dist['both'][k]:4d} {dist['kept'][k]:4d}   "
                  f"떨어진 비율 {100 * (dist['flip'][k] + dist['both'][k]) / tot:5.1f}%")

    extra = sorted({k for g in groups.values() for r in g for k, v in r.items()
                    if k not in ("text", "doc_id", "label", "v5", "v6") and isinstance(v, (str, int))})
    for k in extra:
        vals = Counter(str(r.get(k)) for g in groups.values() for r in g)
        if 1 < len(vals) <= 30:
            print(f"\n[{k}] 군별 분포 (flip / both / kept)")
            dist = {g: Counter(str(r.get(k)) for r in rs) for g, rs in groups.items()}
            for v, _ in vals.most_common(15):
                tot = sum(dist[g][v] for g in groups)
                print(f"  {v[:22]:22s} {dist['flip'][v]:4d} {dist['both'][v]:4d} {dist['kept'][v]:4d}   "
                      f"떨어진 비율 {100 * (dist['flip'][v] + dist['both'][v]) / tot:5.1f}%")

    print("\n[flip 예시] v6 가 새로 S3 로 떨어뜨린 문서 앞부분")
    for r in groups["flip"][: a.examples]:
        head = re.sub(r"\s+", " ", r["text"])[:150]
        print(f"  {r['doc_id']} (정답 {r['label']} · v5 {r['v5']})  {head}")

    if a.train:
        # 떨어진 비율이 높은 '대상' 상위 3개와 법률 계열 단어가 학습셋에서 어느 등급으로
        # 배워졌는지 센다. v5 → v6 에서 그 문구의 S3 비중이 늘었으면, 판례 교정이 그 어휘를
        # S3 쪽으로 옮겨 사내 규정 문서까지 끌고 갔다는 뜻이다.
        fell = out.get("대상", {})
        top = [k for k, v in sorted(fell.items(), key=lambda kv: -(kv[1]["flip"] + kv[1]["both"]))
               if k != "-"][:3]
        probes = top + ["규정", "개정", "법령", "판결"]
        print("\n[학습셋] 문구가 든 행의 등급 분포")
        out["train"] = {}
        for tp in a.train:
            cnt = {p: Counter() for p in probes}
            tot: Counter = Counter()
            for line in Path(tp).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                g, t = str(d.get("label")), d.get("text") or ""
                tot[g] += 1
                for p in probes:
                    if p in t:
                        cnt[p][g] += 1
            print(f"  {tp}  전체 {sum(tot.values())}행 {dict(tot)}")
            for p in probes:
                c = cnt[p]
                n = sum(c.values())
                s3 = 100 * c["S3"] / n if n else 0.0
                print(f"    {p:14s} {n:5d}행  S3 {s3:5.1f}%  {dict(c)}")
            out["train"][tp] = {"total": dict(tot), "probes": {p: dict(cnt[p]) for p in probes}}

    if a.swap_model:
        # 개입 실험 — 축은 '대상' 문구 하나뿐이다. 다른 것은 한 글자도 바꾸지 않는다.
        #   처치군: 떨어진 문서(flip+both) 중 대상이 가장 많이 떨어진 값인 것 → 대상을 안전한 값으로
        #   대조군: 안 떨어진 문서(kept) 중 대상이 안전한 값인 것     → 대상을 떨어진 값으로
        # 처치군이 S3 에서 빠져나오고 대조군이 S3 로 떨어지면 그 문구가 원인이다.
        # 한쪽만 움직이면 문구가 아니라 문서의 다른 성질이 원인이다.
        from gate_p1_candidate import _pred_grade, predict_direct  # noqa: PLC0415

        fell = out.get("대상", {})
        rated = [(k, (v["flip"] + v["both"]) / max(1, sum(v.values())), sum(v.values()))
                 for k, v in fell.items() if k != "-"]
        bad = max((x for x in rated if x[2] >= 10), key=lambda x: x[1])[0]
        safe = min((x for x in rated if x[2] >= 20), key=lambda x: x[1])[0]
        treat = [r for g in ("flip", "both") for r in groups[g] if _slots(r["text"])["대상"] == bad]
        ctrl = [r for r in groups["kept"] if _slots(r["text"])["대상"] == safe]

        def _s3_rate(rs: list[dict], src: str, dst: str) -> tuple[float, float, int]:
            swapped = [r | {"text": r["text"].replace(src, dst)} for r in rs]
            before = [_pred_grade(p) for p in predict_direct(Path(a.swap_model), rs)]
            after = [_pred_grade(p) for p in predict_direct(Path(a.swap_model), swapped)]
            n = len(rs)
            return (100 * sum(g == "S3" for g in before) / n, 100 * sum(g == "S3" for g in after) / n, n)

        print(f"\n[개입 실험] 모델 {Path(a.swap_model).name} · 바꾼 것 = 대상 문구 하나")
        tb, ta, tn = _s3_rate(treat, bad, safe)
        cb, ca, cn = _s3_rate(ctrl, safe, bad)
        print(f"  처치군 {tn:3d}건  '{bad}' → '{safe}'   S3 비율 {tb:5.1f}% → {ta:5.1f}%")
        print(f"  대조군 {cn:3d}건  '{safe}' → '{bad}'   S3 비율 {cb:5.1f}% → {ca:5.1f}%")
        out["swap"] = {"model": a.swap_model, "bad": bad, "safe": safe,
                       "treat": {"n": tn, "s3_before": tb, "s3_after": ta},
                       "control": {"n": cn, "s3_before": cb, "s3_after": ca}}

    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
