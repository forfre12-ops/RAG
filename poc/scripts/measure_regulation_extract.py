#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 없이 "규정의 일치한 문장만 원문 그대로" 보여 주는 방식(추출식)을 잰다.

measure_regulation_summary.py 의 LLM 요약이 (1) CPU 에서 문서당 약 95초, (2) 한 조항 안에서 엉뚱한 문장을 골라
등급 방향으로 끌고, (3) 적용 안 되는 규정에서도 무관 요약을 냈다. 이 도구는 세 가지를 LLM 없이 푸는지 본다.

  문서를 문단(줄 묶음)으로 나누고, 규정 조항을 문장(줄)으로 나눠 bge-m3 로 임베딩한다.
  문장 점수 = 문서 문단들과의 최대 코사인.
  S1  조항 상위 3(하이브리드 조회) 안에서 점수 최고 문장 1개씩
  S2  색인 전체 문장에서 조항당 1개, 점수 상위 3개
  표시 = 문장 원문 + 조항 번호. 생성이 없으므로 충실성 문제가 없다.

자동 지표: 직접 적용 조항 인용 문서 · 항목 정밀도 · 표시 문장이 말한 등급 vs 문서 정답 등급 · 기권(문장 점수 분포).
사람 재판독은 별도(--dump 로 표본 출력).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_regulation_evidence as ev  # noqa: E402
import measure_regulation_summary as sm  # noqa: E402

GRADES = ("극비", "기밀", "대외비", "일반")
MAP = {"TS": "극비", "S1": "기밀", "S2": "대외비", "S3": "일반"}


LEAD_RE = re.compile(r"다음\s*(각\s*호|기준|과\s*같|중)|다음에\s*따른다")   # 내용 없는 서두 문장
GRADE_ITEM_RE = re.compile(r"^\d+\.\s*(극비|기밀|대외비|일반)\s*[:：]")      # 등급별로 한 줄씩 정한 목록 항목


def clause_sentences(c: dict, drop_lead: bool = False) -> list[str]:
    out: list[str] = []
    for ln in c["text"].split("\n"):
        ln = re.sub(r"\*\*", "", ln).strip()
        if not ln or re.match(r"^제\d+조\s*[\(（]", ln):
            continue
        for s in re.split(r"(?<=다\.)\s+", ln):
            s = s.strip()
            if len(s) >= 12 and not (drop_lead and LEAD_RE.search(s)):
                out.append(s)
    return out


def expand_grade_list(c: dict, sentence: str) -> str:
    """등급별 목록의 한 줄이 뽑히면 한 등급 규칙만 보여 그 등급으로 끌지 않도록 목록 전체(모든 등급 줄)를 보인다."""
    if not GRADE_ITEM_RE.match(sentence):
        return sentence
    lines = [re.sub(r"\*\*", "", ln).strip() for ln in c["text"].split("\n")]
    return " / ".join(ln for ln in lines if GRADE_ITEM_RE.match(ln))


def doc_segments(text: str, n: int = 14) -> list[str]:
    segs = []
    for ln in text.split("\n"):
        ln = ln.strip()
        if len(ln) < 30 or set(ln) <= set("|-: "):
            continue
        segs.append(ln[:300])
        if len(segs) >= n:
            break
    return segs or [text[:300]]


def auroc(pos, neg):
    return ev.auroc(pos, neg)


def build_sentence_index(idx: ev.Index, drop_lead: bool = False) -> tuple[list[tuple[int, str]], np.ndarray]:
    rows = [(ci, s) for ci, c in enumerate(idx.chunks) for s in clause_sentences(c, drop_lead)]
    vecs = ev.embed([s for _, s in rows], max_chars=400)
    return rows, vecs


_SEG_CACHE: dict[str, np.ndarray] = {}
_Q_CACHE: dict[str, np.ndarray] = {}


def score_doc(doc_text: str, sv: np.ndarray) -> np.ndarray:
    if doc_text not in _SEG_CACHE:
        _SEG_CACHE[doc_text] = ev.embed(doc_segments(doc_text), max_chars=300)
    return (_SEG_CACHE[doc_text] @ sv.T).max(axis=0)  # 문장별: 문서 문단들과의 최대 코사인


def qvec_of(text: str) -> np.ndarray:
    if text not in _Q_CACHE:
        _Q_CACHE[text] = ev.embed([text], max_chars=1500)[0]
    return _Q_CACHE[text]


def pick(idx: ev.Index, rows, sv, doc: dict, qvec, mode: str) -> list[dict]:
    sc = score_doc(doc["text"], sv)
    if mode in ("S1", "S1c"):
        top = [t[0] for t in idx.search(doc["text"], qvec, top=3)["hybrid"]]
        items = []
        for ci in top:
            cand = [(sc[k], k) for k, (cj, _) in enumerate(rows) if cj == ci]
            if cand:
                s, k = max(cand)
                items.append((s, k))
    else:
        seen, items = set(), []
        for k in np.argsort(-sc):
            cj = rows[k][0]
            if cj in seen:
                continue
            seen.add(cj)
            items.append((float(sc[k]), int(k)))
            if len(items) == 3:
                break
    def shown(k: int) -> str:
        return expand_grade_list(idx.chunks[rows[k][0]], rows[k][1]) if mode == "S1c" else rows[k][1]
    return [{"clause": sm.ckey(idx.chunks[rows[k][0]]["id"]), "sentence": shown(k), "score": round(float(s), 4)}
            for s, k in items]


def cno(clause: str):
    m = re.match(r"제(\d+)조", clause)
    return int(m.group(1)) if m else None


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="추출식 규정 근거 표시 시험")
    ap.add_argument("--regdir", required=True)
    ap.add_argument("--guidedir", help="적용 안 되는 규정 폴더(기권 시험)")
    ap.add_argument("--docs", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--exclude", default="1-11,13,15,17,19-25")
    ap.add_argument("--out", required=True)
    ap.add_argument("--modes", default="S1,S2", help="S1 · S2 · S1c(서두 문장 제외 + 등급별 목록은 전체 표시)")
    a = ap.parse_args(argv)
    docs = [json.loads(ln) for ln in Path(a.docs).read_text(encoding="utf-8").splitlines() if ln.strip()]
    labels = {k: set(v) for k, v in json.loads(Path(a.labels).read_text(encoding="utf-8")).items()}
    biz = [d for d in docs if d["doc_id"] in labels]
    cases = [d for d in docs if d.get("source") != "proxy_gold_authored"]

    idx = sm.load_reg(Path(a.regdir), sm.parse_exclude(a.exclude))
    modes = a.modes.split(",")
    rows, sv = build_sentence_index(idx, drop_lead="S1c" in modes)
    print(f"규정 조각 {len(idx.chunks)} · 문장 {len(rows)}")
    res: dict = {}
    dump: list[dict] = []
    best: dict[str, dict[str, list[float]]] = {m: {} for m in modes}
    for mode in modes:
        print("모드", mode, flush=True)
        hit = 0
        good = tot = 0
        gr = {"정답 등급만 언급": 0, "정답 등급 포함+다른 등급": 0, "다른 등급만 언급": 0, "등급 언급 없음": 0}
        bs = []
        for d in biz:
            qv = qvec_of(d["text"])
            it = pick(idx, rows, sv, d, qv, mode)
            nos = [cno(i["clause"]) for i in it]
            hit += any(n in labels[d["doc_id"]] for n in nos)
            good += sum(n in labels[d["doc_id"]] for n in nos)
            tot += len(nos)
            true = MAP[d["label"]]
            ment = {g for i in it for g in GRADES if g in i["sentence"]}
            if not ment:
                gr["등급 언급 없음"] += 1
            elif ment == {true}:
                gr["정답 등급만 언급"] += 1
            elif true in ment:
                gr["정답 등급 포함+다른 등급"] += 1
            else:
                gr["다른 등급만 언급"] += 1
            bs.append(max(i["score"] for i in it))
            dump.append({"mode": mode, "doc_id": d["doc_id"], "group": "biz", "items": it})
        best[mode]["biz"] = bs
        cs = []
        for d in cases:
            qv = qvec_of(d["text"])
            it = pick(idx, rows, sv, d, qv, mode)
            cs.append(max(i["score"] for i in it))
            dump.append({"mode": mode, "doc_id": d["doc_id"], "group": "case", "items": it})
        best[mode]["case"] = cs
        res[mode] = {"직접적용_조항을_인용한_문서": f"{hit}/{len(biz)}", "항목_정밀도": f"{good}/{tot}", "등급": gr,
                     "1위문장점수_AUROC(업무문서 vs 판례·금융)": round(auroc(bs, cs), 3),
                     "점수_중앙(업무/판례)": [round(float(np.median(bs)), 3), round(float(np.median(cs)), 3)]}
    # 적용 안 되는 규정(기록물관리 지침)에서 같은 업무문서의 최고 문장 점수
    if a.guidedir:
        gidx = sm.load_reg(Path(a.guidedir), set())
        grows, gsv = build_sentence_index(gidx)
        print(f"지침 조각 {len(gidx.chunks)} · 문장 {len(grows)}")
        gsc = []
        for n, d in enumerate(biz):
            gsc.append(float(score_doc(d["text"], gsv).max()))
            if (n + 1) % 20 == 0:
                print("  지침 점수", n + 1, flush=True)
        osc = []
        for d in biz:
            osc.append(float(score_doc(d["text"], sv).max()))
        res["규정_적용성(같은 업무문서 71건의 최고 문장 점수)"] = {
            "시연규정_중앙": round(float(np.median(osc)), 3), "기록물관리지침_중앙": round(float(np.median(gsc)), 3),
            "AUROC(시연규정 vs 지침)": round(auroc(osc, gsc), 3)}
    Path(a.out).write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in dump), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
