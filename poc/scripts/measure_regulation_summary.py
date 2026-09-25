#!/usr/bin/env python
# -*- coding: utf-8 -*-
""""규정에 있는 것만 요약해서 보여 주기"가 쓸 만한지 잰다 — measure_regulation_evidence.py 의 다음 단계.

문서 하나에 대해 규정 조항 상위 3개를 조회하고, LLM 이 그 조항 **안의 내용만** 항목으로 정리한다.
항목 = {조항 번호, 한 문장 요약, 조항 원문에서 그대로 복사한 인용}. 판정은 LLM 이 아니라 기계 검사와 사람 재판독이다
(LLM 판정기는 사람 재판독과 일치가 낮았다 — 9/25).

자동 검사(결정형):
  인용 일치   quote 가 조항 원문에 글자 그대로(공백·* 제외) 들어 있는가, 느슨히(가장 긴 공통 구간 90%↑)
  숫자 근거   요약에 나온 숫자가 조항 원문에 모두 있는가(없으면 지어낸 숫자)
  조항 범위   인용한 조항 번호가 실제로 준 조항 안에 있는가
  등급 언급   요약이 등급 이름을 말하는가 / "이 문서는 ○○" 식으로 문서 등급을 판정하는가
  기권        관련 조항이 없어야 하는 문서(판례·금융 · 적용 안 되는 규정)에서 항목을 비우는가
  유용성      요약 항목이 사람이 미리 정한 '직접 적용 조항'을 인용하는가(--labels)
  결정성      같은 시드 두 번의 출력 일치, 다른 시드와 인용 조항 집합 겹침

단계:  run     조회 + LLM 요약을 results jsonl 에 이어 쓴다(같은 키는 건너뜀)
       report  results jsonl 을 집계한다
"""
from __future__ import annotations

import argparse
import difflib
import io
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_regulation_evidence as ev  # noqa: E402

PROMPT = (
    "당신은 회사 규정에서 관련 내용을 찾아 정리해 주는 도우미다. 아래 [문서]를 다룰 때 참고할 내용을 "
    "[규정 조항]에서만 골라 정리하라.\n"
    "규칙:\n"
    "1. 조항에 적힌 내용만 쓴다. 조항에 없는 내용·추측·일반 상식을 덧붙이지 않는다.\n"
    "2. 이 문서의 등급을 판정하지 않는다. '이 문서는 ○○ 등급이다'라고 쓰지 않는다. 조항이 정한 기준만 옮긴다.\n"
    "3. 문서와 직접 관련 없는 조항은 쓰지 않는다. 관련 있는 조항이 하나도 없으면 items 를 빈 배열로 둔다.\n"
    "4. 각 항목에는 조항 번호(clause), 한 문장 요약(summary), 조항 원문에서 글자 그대로 복사한 인용(quote, 20~80자)을 넣는다.\n"
    "5. 항목은 최대 3개.\n"
    '출력은 JSON 만: {{"items":[{{"clause":"제N조","summary":"...","quote":"..."}}]}}\n\n'
    "[문서]\n{doc}\n\n[규정 조항]\n{clauses}\n")

GRADES = ("극비", "기밀", "대외비", "일반")


def art_no(cid: str) -> int | None:
    m = re.match(r"제(\d+)조", cid)
    return int(m.group(1)) if m else None


def parse_exclude(spec: str) -> set[int]:
    out: set[int] = set()
    for part in filter(None, (spec or "").split(",")):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


def ckey(cid: str) -> str:
    """조각 id → 조항 키. 제N조 규정은 '제34조', 쪽 단위 지침은 'p212'."""
    return cid.rsplit("-", 1)[0]


def norm(s: str) -> str:
    return re.sub(r"[\s*]+", "", s)


def llm(prompt: str, model: str, seed: int, num_gpu: int | None, num_predict: int = 500) -> tuple[str, float]:
    opts = {"temperature": 0.0, "seed": seed, "num_predict": num_predict}
    if num_gpu is not None:
        opts["num_gpu"] = num_gpu
    body = {"model": model, "stream": False, "format": "json", "think": False,
            "messages": [{"role": "user", "content": prompt}], "options": opts}
    t0 = time.time()
    try:
        res = ev._post("/api/chat", body, timeout=900)
    except Exception:  # think 미지원 모델
        body.pop("think")
        res = ev._post("/api/chat", body, timeout=900)
    return res["message"]["content"].strip(), time.time() - t0


def load_reg(regdir: Path, exclude: set[int]) -> ev.Index:
    chunks = [json.loads(ln) for ln in (regdir / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    vecs = np.load(regdir / "chunk_vecs.npy")
    keep = [i for i, c in enumerate(chunks)
            if art_no(c["id"]) not in exclude and not c["id"].startswith("부칙")]
    return ev.Index([chunks[i] for i in keep], vecs[keep])


def clause_block(chunks: list[dict]) -> str:
    return "\n\n".join(f"[{ckey(c['id'])}]\n{c['text'][:1400]}" for c in chunks)


def do_run(a) -> None:
    regdir = Path(a.regdir)
    idx = load_reg(regdir, parse_exclude(a.exclude))
    docs = [json.loads(ln) for ln in Path(a.docs).read_text(encoding="utf-8").splitlines() if ln.strip()]
    labels = {k: set(v) for k, v in json.loads(Path(a.labels).read_text(encoding="utf-8")).items()} if a.labels else {}
    if a.which == "biz":
        docs = [d for d in docs if d["doc_id"] in labels]
    elif a.which == "cases":
        docs = [d for d in docs if d.get("source") != "proxy_gold_authored"]
    if a.limit:
        docs = docs[:a.limit]
    qv = ev.embed([d["text"] for d in docs], max_chars=1500)
    run_id = f"{a.tag}|{a.model}|{a.mode}|seed{a.seed}|gpu{a.num_gpu if a.num_gpu is not None else 'auto'}"
    rfile = Path(a.results)
    done = set()
    if rfile.exists():
        for ln in rfile.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            done.add((r["run_id"], r["doc_id"]))
    with rfile.open("a", encoding="utf-8") as f:
        for n, (d, q) in enumerate(zip(docs, qv)):
            if (run_id, d["doc_id"]) in done:
                continue
            if a.mode == "oracle":
                want = labels[d["doc_id"]]
                dense = idx.vecs @ q
                cand = [i for i, c in enumerate(idx.chunks) if art_no(c["id"]) in want]
                top = sorted(cand, key=lambda i: -dense[i])[:3]
            else:
                top = [t[0] for t in idx.search(d["text"], q, top=3)["hybrid"]]
            given = [idx.chunks[i] for i in top]
            raw, dt = llm(PROMPT.format(doc=d["text"][:1200], clauses=clause_block(given)),
                          a.model, a.seed, a.num_gpu)
            rec = {"run_id": run_id, "doc_id": d["doc_id"], "group": "biz" if d["doc_id"] in labels else "case",
                   "given": [c["id"] for c in given], "raw": raw, "sec": round(dt, 2)}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            if (n + 1) % 10 == 0:
                print(f"  {run_id} {n + 1}/{len(docs)}", flush=True)


def analyze(rec: dict, chunk_text: dict[str, str], labels: dict[str, set]) -> dict:
    out = {"parse": False, "items": []}
    try:
        obj = json.loads(rec["raw"])
        items = obj.get("items", [])
        assert isinstance(items, list)
        out["parse"] = True
    except Exception:
        return out
    given_keys = {ckey(g) for g in rec["given"]}
    for it in items[:5]:
        raw_key = re.sub(r"\s", "", str(it.get("clause", "")))
        # 모델이 '제34조(외부 제공의 승인)' 처럼 제목을 붙여 적어도 같은 조항으로 본다
        km = re.search(r"제\d+조|p\d+", raw_key)
        key = km.group(0) if km else raw_key
        summ, quote = str(it.get("summary", "")), str(it.get("quote", ""))
        cno = art_no(key)
        ctext = " ".join(t for cid, t in chunk_text.items() if ckey(cid) == key and cid in rec["given"])
        nq, nc = norm(quote), norm(ctext)
        exact = bool(nq) and nq in nc
        sm = difflib.SequenceMatcher(None, nq, nc, autojunk=False)
        lcs = sm.find_longest_match(0, len(nq), 0, len(nc)).size / len(nq) if nq and nc else 0.0
        nums_s = set(re.findall(r"\d+(?:\.\d+)?", summ)) - set(re.findall(r"\d+", key))
        nums_c = set(re.findall(r"\d+(?:\.\d+)?", ctext))
        out["items"].append({
            "cno": cno, "in_given": key in given_keys, "quote_exact": exact, "quote_fuzzy": lcs >= 0.9,
            "nums_ok": nums_s <= nums_c, "grade_word": any(g in summ for g in GRADES),
            "judges_doc": bool(re.search(r"이 문서(는|의)?[^.。]{0,25}(극비|기밀|대외비|일반)", summ)),
        })
    doc_labels = labels.get(rec["doc_id"])
    out["hit"] = any(i["cno"] in doc_labels for i in out["items"]) if doc_labels else None
    return out


def do_report(a) -> None:
    regdirs = {t: Path(p) for t, p in (kv.split("=", 1) for kv in a.regmap)}
    chunk_text: dict[str, dict[str, str]] = {}
    for tag, rd in regdirs.items():
        lines = (rd / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
        chunk_text[tag] = {c["id"]: c["text"] for c in (json.loads(ln) for ln in lines if ln.strip())}
    labels = {k: set(v) for k, v in json.loads(Path(a.labels).read_text(encoding="utf-8")).items()} if a.labels else {}
    runs: dict[str, list[dict]] = {}
    for ln in Path(a.results).read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        runs.setdefault(r["run_id"], []).append(r)
    rows = []
    for rid, recs in sorted(runs.items()):
        tag = rid.split("|")[0]
        ct = chunk_text[tag]
        an = [(r, analyze(r, ct, labels)) for r in recs]
        for grp in ("biz", "case"):
            sub = [(r, x) for r, x in an if r["group"] == grp]
            if not sub:
                continue
            items = [i for _, x in sub for i in x["items"]]
            nonempty = sum(1 for _, x in sub if x["items"])
            row = {"run": rid, "group": grp, "docs": len(sub),
                   "parse_ok": f"{sum(x['parse'] for _, x in sub)}/{len(sub)}",
                   "항목이_있는_문서": f"{nonempty}/{len(sub)}",
                   "문서당_항목": round(len(items) / len(sub), 2),
                   "sec_중앙": round(float(np.median([r['sec'] for r, _ in sub])), 1),
                   "sec_최대": round(max(r['sec'] for r, _ in sub), 1)}
            if items:
                for k, lab in (("quote_exact", "인용_글자일치"), ("quote_fuzzy", "인용_느슨일치"),
                               ("nums_ok", "숫자_근거"), ("in_given", "조항_범위내"),
                               ("grade_word", "등급이름_언급"), ("judges_doc", "문서등급_판정투")):
                    row[lab] = f"{sum(i[k] for i in items)}/{len(items)}"
            if grp == "biz" and labels:
                hit = sum(1 for _, x in sub if x.get("hit"))
                good = sum(1 for _, x in sub for i in x["items"] if i["cno"] in labels[_["doc_id"]])
                row["직접적용_조항을_인용한_문서"] = f"{hit}/{len(sub)}"
                row["항목_정밀도(직접적용/전체)"] = f"{good}/{len(items)}"
            rows.append(row)
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    # 결정성: 같은 조건, 시드만 다른 run 끼리
    by_key: dict[tuple, dict[str, dict]] = {}
    for rid, recs in runs.items():
        tag, model, mode, seed, gpu = rid.split("|")
        by_key.setdefault((tag, model, mode, gpu), {})[seed] = {r["doc_id"]: r for r in recs}
    for key, seeds in by_key.items():
        if len(seeds) < 2:
            continue
        names = sorted(seeds)
        s0, s1 = seeds[names[0]], seeds[names[1]]
        common = sorted(set(s0) & set(s1))
        same = sum(1 for d in common if s0[d]["raw"] == s1[d]["raw"])
        def cset(r):
            try:
                return {re.sub(r"\s", "", str(i.get("clause", ""))) for i in json.loads(r["raw"]).get("items", [])}
            except Exception:
                return set()
        jac = [len(cset(s0[d]) & cset(s1[d])) / max(1, len(cset(s0[d]) | cset(s1[d]))) if (cset(s0[d]) | cset(s1[d])) else 1.0 for d in common]
        print(f"결정성 {key} {names}: 출력 완전 일치 {same}/{len(common)} · 인용 조항 집합 겹침(Jaccard) 평균 {np.mean(jac):.2f}")


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="규정 요약 시험")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--regdir", required=True)
    r.add_argument("--tag", required=True, help="규정 이름표(집계에서 조각 원문을 찾는 키)")
    r.add_argument("--docs", required=True)
    r.add_argument("--labels", help="doc_id → 직접 적용 조항 번호 목록 json")
    r.add_argument("--exclude", default="", help="색인에서 뺄 조 번호(예: 1-9,19-25)")
    r.add_argument("--model", default="qwen3:14b")
    r.add_argument("--mode", choices=["retrieved", "oracle"], default="retrieved")
    r.add_argument("--which", choices=["all", "biz", "cases"], default="all")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--num-gpu", type=int, default=None)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--results", required=True)
    p = sub.add_parser("report")
    p.add_argument("--results", required=True)
    p.add_argument("--labels")
    p.add_argument("--regmap", nargs="+", required=True, help="태그=규정폴더 (예: org=.../sample_org)")
    a = ap.parse_args(argv)
    do_run(a) if a.cmd == "run" else do_report(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
