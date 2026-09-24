#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 문서 파일럿 검사 — PREREG_PILOT.md 의 A(문서별)·B(집합) 검사와 블라인드 판정 묶음 생성.

사용:  python scripts/validate_docgen_pilot.py check      docs_XX.jsonl 을 모아 검사하고 pilot_docs_checked.jsonl 을 만든다
       python scripts/validate_docgen_pilot.py bundles   검사를 통과한 문서로 블라인드 판정 묶음(judge_bundle_0..7.json)을 만든다
       python scripts/validate_docgen_pilot.py analyze   판정 결과(judge_out_0..7.json)를 명세와 대조한다
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
D = POC / "reports" / os.environ.get("DOCGEN_DIR", "CLAUDE_DOCGEN_20260921")      # 2차는 DOCGEN_DIR=CLAUDE_DOCGEN_R2_20260921
G = ["TS", "S1", "S2", "S3"]
EXTRA = re.compile(r"비밀|기밀|대외비|극비|Level|레벨\s*\d")
ALIAS = re.compile(r"[가-힣]{1,6} [A-Z](?![A-Za-z])")
N_JUDGE = int(os.environ.get("N_JUDGE", "8"))
N_DOCS = int(os.environ.get("N_DOCS", "12"))


def load_specs() -> dict:
    return {s["agent_key"]: s for s in json.loads((D / "specs_pilot.json").read_text(encoding="utf-8"))}


def check() -> int:
    sys.path.insert(0, str(POC / "src"))
    from koipa.services.synth_quality import _grade_term_pattern  # noqa: PLC0415

    pat = _grade_term_pattern()
    specs = load_specs()
    rows, missing = [], []
    for b in range(N_DOCS):
        f = D / f"docs_{b:02d}.jsonl"
        if not f.exists():
            missing.append(f.name)
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    got = {r["agent_key"]: r for r in rows}
    out = []
    for k, s in specs.items():
        r = got.get(k)
        if not r:
            continue
        text = r["text"]
        ev = r.get("evidence") or {}
        flags = []
        if pat.search(unicodedata.normalize("NFKC", text)) or EXTRA.search(text):
            flags.append("grade_term")
        if not all(isinstance(ev.get(ax), str) and ev.get(ax) and ev[ax] in text for ax in ("S", "V", "M")):
            flags.append("evidence_not_in_text")
        if ALIAS.search(text):
            flags.append("alias_code")
        if abs(len(text) - s["length_chars"]) > 0.25 * s["length_chars"]:
            flags.append("length_off")
        out.append({"agent_key": k, "doc_key": s["doc_key"], "family_id": s["family_id"], "grade": s["grade"], "S": s["S"], "V": s["V"], "M": s["M"],
                    "domain": s["domain"], "form": s["form"], "subject": s["subject"], "text": text, "evidence": ev, "chars": len(text), "flags": flags})
    (D / "pilot_docs_checked.jsonl").write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in out), encoding="utf-8")
    print(f"작성 {len(out)}/{len(specs)}건 · 누락 파일 {missing}")
    fc = Counter(f for o in out for f in o["flags"])
    print("A. 문서별 검사 걸린 수:", dict(fc), "· 무결점", sum(1 for o in out if not o["flags"]), "건")
    if len(out) < 40:
        return 0
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold, cross_val_predict
    from sklearn.tree import DecisionTreeClassifier

    y = [o["grade"] for o in out]
    g = [o["family_id"] for o in out]
    ln = np.array([[o["chars"]] for o in out])
    cv = GroupKFold(n_splits=5)
    pl = cross_val_predict(DecisionTreeClassifier(max_depth=2, random_state=0), ln, y, groups=g, cv=cv)
    lens = {gr: [o["chars"] for o in out if o["grade"] == gr] for gr in G}
    print(f"B5. 길이만으로 등급 맞히기(결정 나무 깊이 2, 가족 5분할) 정확도 {np.mean(np.array(pl) == np.array(y)):.1%} (무작위 25%, 합격 ≤40%) · 등급별 글자수 중앙 "
          + " ".join(f"{gr} {int(np.median(v))}" for gr, v in lens.items()))
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
    xs = vec.fit_transform([o["text"] for o in out])
    pt = cross_val_predict(LogisticRegression(C=10, max_iter=3000, class_weight="balanced"), xs, y, groups=g, cv=cv)
    acc = float(np.mean(np.array(pt) == np.array(y)))
    print(f"B6. 글자 n-gram TF-IDF + 로지스틱(가족 5분할) 정확도 {acc:.1%} (합격 ≤60%; 현재 1,000건 계열 분할 F1 54.9%) → {'통과' if acc <= 0.6 else '표면 단서 의심'}")
    sim = (xs @ xs.T).toarray()
    np.fill_diagonal(sim, 0)
    fam = np.array(g)
    cross = max(float(sim[i][fam != fam[i]].max()) for i in range(len(out)))
    within = float(np.mean([sim[i][fam == fam[i]].max() for i in range(len(out))]))
    print(f"   가족 밖 최근접 코사인 최대 {cross:.2f}(≥0.95 이면 근접 중복) · 가족 안 최근접 코사인 평균 {within:.2f}")
    return 0


def bundles() -> int:
    docs = [json.loads(x) for x in (D / "pilot_docs_checked.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = [d for d in docs if "grade_term" not in d["flags"] and "evidence_not_in_text" not in d["flags"]]
    rng = random.Random(20260926)
    rng.shuffle(ok)
    key = {}
    batches = [[] for _ in range(N_JUDGE)]
    for n, d in enumerate(ok):
        jid = f"K{n + 1:03d}"
        key[jid] = d["doc_key"]
        batches[n % N_JUDGE].append({"item_id": jid, "text": d["text"]})
    (D / "JUDGE_KEY_PRIVATE.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    for b, rows in enumerate(batches):
        (D / f"judge_bundle_{b}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"판정 대상 {len(ok)}건(전체 {len(docs)}건 중 등급명·근거 검사 통과) · 묶음 {[len(b) for b in batches]}")
    return 0


def grade_of(s, v, m) -> str:
    if "HOLD" in (s, v, m):
        return "HOLD"
    p = s * v * m
    return "S3" if p == 0 else ("S2" if p in (1, 2) else ("S1" if p == 4 else "TS"))


def analyze() -> int:
    docs = {d["doc_key"]: d for d in (json.loads(x) for x in (D / "pilot_docs_checked.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())}
    files = [D / f"judge_out_{b}.json" for b in range(N_JUDGE)]
    if not all(f.exists() for f in files):
        print("판정 파일이 아직 다 없다:", [f.name for f in files if not f.exists()])
        return 1
    J = {}
    for f in files:
        for r in json.loads(f.read_text(encoding="utf-8")):
            J[r["item_id"]] = r
    key = json.loads((D / "JUDGE_KEY_PRIVATE.json").read_text(encoding="utf-8"))                    # 여기서 처음 연다
    rows = []
    for jid, dk in key.items():
        if jid not in J:
            continue
        d, j = docs[dk], J[jid]
        jg = grade_of(j["S"], j["V"], j["M"])
        rows.append({"doc_key": dk, "family": d["family_id"], "spec": d["grade"], "judge": jg, "S": (d["S"], j["S"]), "V": (d["V"], j["V"]), "M": (d["M"], j["M"]),
                     "domain": d["domain"], "form": d["form"], "ok": jg == d["grade"], "hold": jg == "HOLD"})
    n = len(rows)
    L = [f"블라인드 판정 {n}/{len(key)}건 (검사 통과 문서 기준)"]
    hold = sum(r["hold"] for r in rows)
    ok = sum(r["ok"] for r in rows)
    L.append(f"C7. HOLD 비율 {hold}/{n} = {hold / n:.1%} (합격 ≤25%; 현재 1,000건 81~95%) → {'통과' if hold / n <= 0.25 else '미달'}")
    L.append(f"C8. 판정 등급 = 명세 등급 {ok}/{n} = {ok / n:.1%} (합격 ≥80%) → {'통과' if ok / n >= 0.8 else '미달'}")
    for ax in ("S", "V", "M"):
        a = sum(1 for r in rows if r[ax][1] != "HOLD" and r[ax][0] == r[ax][1])
        h = sum(1 for r in rows if r[ax][1] == "HOLD")
        L.append(f"   {ax}축 일치 {a}/{n} = {a / n:.1%} · HOLD {h} ({h / n:.0%})")
    L.append("   등급별 일치: " + " · ".join(f"{g} {sum(r['ok'] for r in rows if r['spec'] == g)}/{sum(1 for r in rows if r['spec'] == g)}" for g in G))
    conf = Counter((r["spec"], r["judge"]) for r in rows)
    L.append("   혼동(명세→판정): " + " · ".join(f"{g}:" + "/".join(str(conf[(g, p)]) for p in G + ["HOLD"]) for g in G) + "   (열 순서 TS/S1/S2/S3/HOLD)")
    combos = Counter()
    good = Counter()
    for dk, d in docs.items():
        pass
    for r in rows:
        c = (r["S"][0], r["V"][0], r["M"][0])
        combos[c] += 1
        good[c] += r["ok"]
    L.append("   조합별 일치(S,V,M): " + " · ".join(f"{c}:{good[c]}/{combos[c]}" for c in sorted(combos)))
    bad_form = Counter(r["form"] for r in rows if not r["ok"])
    L.append("   불일치가 많은 양식: " + str(dict(bad_form.most_common(4))))
    (D / "pilot_judge_result.txt").write_text("\n".join(L), encoding="utf-8")
    (D / "pilot_judge_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(L))
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    return {"check": check, "bundles": bundles, "analyze": analyze}.get(cmd, lambda: print(__doc__) or 1)()


if __name__ == "__main__":
    sys.exit(main())
