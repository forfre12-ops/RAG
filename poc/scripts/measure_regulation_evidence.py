#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""규정 원문을 조항 조각으로 나눠 색인하고, "근거 표시"로 쓸 수 있는지 잰다.

두 가지를 잰다 — 등급을 바꾸는 시험이 아니다(등급 품질에는 벡터가 0.49 라 닫혀 있다).

  B1 조회 적중  조각을 무작위로 뽑아 LLM 이 그 조각이 적용될 업무 상황을 일상 표현으로 쓴다.
                그 글로 조회했을 때 원래 조각이 상위 k 에 드는가. 규모(조각 수백 개)와 무작위
                기준선 대비 값을 본다. ⚠ 조각을 만든 모델과 조회하는 모델이 달라도 "지어낸
                질의"라서 실문서 적중률이 아니다 — 규모 점검용이다.
  B2 근거 오표시 실제 문서로 상위 3개 조항을 조회하고, 그 조항이 그 문서에 **정말 관련 있는지**
                판정한다. 조회는 관련 조항이 없어도 늘 상위 k 를 돌려주므로, 무관한 조항이
                "근거"로 뜨는 비율과 점수로 걸러낼 수 있는지(AUROC)가 핵심이다.
                판정은 LLM 이라 사람이 다시 읽어야 한다. ⚠ 판정 프롬프트에 조항을 자르지 말 것 —
                900자로 자르면 조각 끝쪽 내용을 판정기·재판독자가 못 봐 결과가 틀린다(9/25 실측).

사용:
    poc/.venv/Scripts/python.exe scripts/measure_regulation_evidence.py \\
        --text <PAGE 표지가 있는 추출 텍스트> --docs <문서.jsonl> --out-dir <산출 폴더>
    --format pages(기본: 쪽 표지+ㅇ 글머리 지침) | article(제N조 규정, `**제N조(제목)**` 표기 지원)
    --b2-file 로 판정 캐시 이름을 바꿔 방식을 바꿔 다시 돌린다. ⚠ B2 의 LLM 판정은 사람 재판독과 일치가 낮았다(9/25) — 결과 인용 전에 반드시 사람이 다시 읽거나 주제별 정답 조항으로 잴 것.
    단계: chunk → embed → b1 → b2 (기본 전부, --stages 로 고름. 중간 결과는 out-dir 에 캐시)

전제: ollama 가 localhost:11434 에서 bge-m3 · qwen3:14b 를 서빙 중. think:false 를 요청 옵션으로
준다(/no_think 문구는 ollama 0.34.2 에서 추론을 못 끈다 — 9/21 실측).
"""
from __future__ import annotations

import argparse
import io
import json
import math
import random
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np

OLLAMA = "http://localhost:11434"
EMBED_MODEL = "bge-m3"
LLM_MODEL = "qwen3:14b"
SEED = 42


# ── 1. 조각 나누기 ────────────────────────────────────────────────────────────
_HEADER_RE = re.compile(r"^\s*\d*\s*│.*│\s*\d*\s*$")


def split_pages(raw: str) -> dict[int, str]:
    parts = re.split(r"\n=====PAGE (\d+)=====\n", raw)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def chunk_page(page_no: int, text: str, min_len: int = 120, max_len: int = 1200) -> list[dict]:
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln.strip() and not _HEADER_RE.match(ln) and len(ln.strip()) > 1]
    if sum(len(ln) for ln in lines) < 60:
        return []
    blocks: list[dict] = []
    heading = ""
    cur: list[str] = []

    def flush() -> None:
        if cur:
            blocks.append({"heading": heading, "text": "\n".join(cur).strip()})
        cur.clear()

    for ln in lines:
        s = ln.strip()
        is_heading = ln.startswith("  ") and not ln.startswith("   ") and len(s) < 60 \
            and not s.startswith(("ㅇ", "-", "※", "●", "·"))
        if is_heading:
            flush()
            heading = s
            continue
        if s.startswith("ㅇ"):
            flush()
        cur.append(s)
    flush()

    merged: list[dict] = []
    for b in blocks:
        if merged and len(merged[-1]["text"]) < min_len and merged[-1]["heading"] == b["heading"]:
            merged[-1]["text"] += "\n" + b["text"]
        else:
            merged.append(dict(b))
    if len(merged) > 1 and len(merged[-1]["text"]) < min_len:
        last = merged.pop()
        merged[-1]["text"] += "\n" + last["text"]

    out: list[dict] = []
    for b in merged:
        t = b["text"]
        while len(t) > max_len:
            cut = t.rfind("\n-", 0, max_len)
            cut = cut if cut > max_len // 2 else max_len
            out.append({"heading": b["heading"], "text": t[:cut].strip()})
            t = t[cut:].strip()
        if t:
            out.append({"heading": b["heading"], "text": t})
    for n, c in enumerate(out):
        c["page"] = page_no
        c["id"] = f"p{page_no}-{n}"
        c["body"] = (f"[{c['heading']}] " if c["heading"] else "") + c["text"]
    return out


_ARTICLE_RE = re.compile(r"^\s*[*#]*\s*(제\s*\d+\s*조(?:\s*의\s*\d+)?)\s*[\(（]([^)）]*)[\)）]")
_CHAPTER_RE = re.compile(r"^\s*#*\s*(제\s*\d+\s*장[^\n]*|부\s*칙[^\n]*)$")


def build_chunks_article(text: str, max_len: int = 1200) -> list[dict]:
    """`제N조(제목)` 단위 조각. 긴 조는 항(①②…) 경계에서 나눈다. page 자리에는 조 번호를 둔다."""
    chapter = ""
    arts: list[dict] = []
    for ln in text.split("\n"):
        ch = _CHAPTER_RE.match(ln.strip()) if ln.strip() else None
        am = _ARTICLE_RE.match(ln)
        if ch and not am:
            chapter = ch.group(1).strip()
            continue
        if am:
            arts.append({"chapter": chapter, "no": re.sub(r"\s+", "", am.group(1)), "title": am.group(2).strip(), "lines": [ln.strip()]})
        elif arts and ln.strip():
            arts[-1]["lines"].append(ln.strip())
    out: list[dict] = []
    for a in arts:
        body = "\n".join(a["lines"])
        parts = [body]
        while len(parts[-1]) > max_len:
            t = parts.pop()
            cut = max(t.rfind("\n", 0, max_len), max_len // 2)
            parts += [t[:cut].strip(), t[cut:].strip()]
        no = ("부칙" if a["chapter"].startswith("부") else "") + a["no"]  # 부칙 제1조 가 본문 제1조 와 id 가 겹치지 않게
        for k, t in enumerate(parts):
            out.append({"heading": f"{a['chapter']} {a['no']}({a['title']})".strip(), "text": t,
                        "page": no, "id": f"{no}-{k}",
                        "body": f"[{a['chapter']} {a['no']}({a['title']})] {t}"})
    return out


def build_chunks(text_path: Path, fmt: str = "pages") -> list[dict]:
    if fmt == "article":
        return [c for c in build_chunks_article(text_path.read_text(encoding="utf-8")) if len(c["text"]) >= 40]
    pages = split_pages(text_path.read_text(encoding="utf-8"))
    chunks: list[dict] = []
    for no in sorted(pages):
        chunks.extend(chunk_page(no, pages[no]))
    # 머리글·쪽번호 찌꺼기(글자 40 미만)는 색인에서 뺀다 — 근거로 보여 줄 내용이 없다.
    return [c for c in chunks if len(c["text"]) >= 40]


# ── 2. 임베딩·조회 ────────────────────────────────────────────────────────────
def _post(path: str, payload: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        OLLAMA + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def embed(texts: list[str], batch: int = 16, max_chars: int = 1500) -> np.ndarray:
    vecs: list[list[float]] = []
    for i in range(0, len(texts), batch):
        res = _post("/api/embed", {"model": EMBED_MODEL, "input": [t[:max_chars] for t in texts[i:i + batch]]})
        vecs.extend(res["embeddings"])
    m = np.array(vecs, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True).clip(min=1e-9)


def _bigrams(s: str) -> Counter:
    s = re.sub(r"\s+", "", s)
    return Counter(s[i:i + 2] for i in range(len(s) - 1))


class Lexical:
    """글자 2-gram TF-IDF. 한국어 형태소 없이도 조사 변화에 버틴다."""

    def __init__(self, docs: list[str]) -> None:
        self.tf = [_bigrams(d) for d in docs]
        df: Counter = Counter()
        for tf in self.tf:
            df.update(tf.keys())
        n = len(docs)
        self.idf = {g: math.log((n + 1) / (c + 1)) + 1 for g, c in df.items()}
        self.vec = [self._w(tf) for tf in self.tf]

    def _w(self, tf: Counter) -> dict:
        w = {g: (1 + math.log(c)) * self.idf.get(g, 0.0) for g, c in tf.items() if g in self.idf}
        norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
        return {g: v / norm for g, v in w.items()}

    def scores(self, query: str) -> np.ndarray:
        q = self._w(_bigrams(query))
        return np.array([sum(v * d.get(g, 0.0) for g, v in q.items()) for d in self.vec], dtype=np.float32)


def rrf(rank_lists: list[np.ndarray], k: int = 60) -> np.ndarray:
    total = np.zeros(len(rank_lists[0]), dtype=np.float32)
    for sc in rank_lists:
        order = np.argsort(-sc)
        for r, idx in enumerate(order):
            total[idx] += 1.0 / (k + r + 1)
    return total


class Index:
    def __init__(self, chunks: list[dict], vecs: np.ndarray) -> None:
        self.chunks, self.vecs = chunks, vecs
        self.lex = Lexical([c["body"] for c in chunks])

    def search(self, query: str, qvec: np.ndarray, top: int = 5) -> dict[str, list[tuple[int, float]]]:
        dense = self.vecs @ qvec
        lex = self.lex.scores(query)
        hyb = rrf([dense, lex])
        return {name: [(int(i), float(sc[i])) for i in np.argsort(-sc)[:top]]
                for name, sc in (("dense", dense), ("lexical", lex), ("hybrid", hyb))}


# ── 3. LLM ────────────────────────────────────────────────────────────────────
def chat(prompt: str, num_predict: int = 300, temperature: float = 0.0) -> str:
    res = _post("/api/chat", {
        "model": LLM_MODEL, "stream": False, "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": temperature, "num_predict": num_predict, "seed": SEED},
    })
    return res["message"]["content"].strip()


def auroc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks: dict[int, float] = {}
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    rsum = sum(ranks[k] for k, (_, lab) in enumerate(allv) if lab == 1)
    return (rsum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


# ── 4. 단계 ───────────────────────────────────────────────────────────────────
def stage_b1(idx: Index, out: Path, n: int) -> dict:
    rng = random.Random(SEED)
    pool = [i for i, c in enumerate(idx.chunks) if len(c["text"]) >= 200]
    picked = sorted(rng.sample(pool, min(n, len(pool))))
    qfile = out / "b1_queries.jsonl"
    done = {}
    if qfile.exists():
        for ln in qfile.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            done[r["chunk_index"]] = r
    with qfile.open("a", encoding="utf-8") as f:
        for i in picked:
            if i in done:
                continue
            c = idx.chunks[i]
            prompt = (
                "아래는 공공기관 지침의 한 대목이다. 이 대목이 적용될 만한 구체적인 업무 상황을 "
                "회사 내부 문서에서 발췌한 것처럼 2~3문장으로 써라.\n"
                "규칙: 지침의 전문용어와 문구를 그대로 쓰지 말고 일상 업무 표현으로 바꿔 쓸 것. "
                "지침 이름·조항 번호·'지침'이라는 말은 쓰지 말 것. 출력은 본문만.\n\n"
                f"[대목]\n{c['body'][:1000]}")
            q = chat(prompt, num_predict=220, temperature=0.3)
            done[i] = {"chunk_index": i, "chunk_id": c["id"], "page": c["page"], "query": q}
            f.write(json.dumps(done[i], ensure_ascii=False) + "\n")
            f.flush()
    rows = [done[i] for i in picked]
    qvecs = embed([r["query"] for r in rows])
    ks = (1, 3, 5)
    res: dict = {"n_queries": len(rows), "n_chunks": len(idx.chunks),
                 "random_baseline_strict": {k: round(k / len(idx.chunks), 4) for k in ks}}
    for mode in ("dense", "lexical", "hybrid"):
        strict = {k: 0 for k in ks}
        page = {k: 0 for k in ks}
        for r, qv in zip(rows, qvecs):
            top = idx.search(r["query"], qv, top=max(ks))[mode]
            for k in ks:
                ids = [t[0] for t in top[:k]]
                strict[k] += r["chunk_index"] in ids
                page[k] += any(idx.chunks[t]["page"] == r["page"] for t in ids)
        res[mode] = {"strict": {f"top{k}": f"{strict[k]}/{len(rows)}" for k in ks},
                     "same_page": {f"top{k}": f"{page[k]}/{len(rows)}" for k in ks}}
    return res


def stage_b2(idx: Index, docs: list[dict], out: Path, doc_chars: int, top_k: int,
             jname: str = "b2_judgments.jsonl") -> dict:
    qvecs = embed([d["text"] for d in docs], max_chars=doc_chars)
    jfile = out / jname
    done = {}
    if jfile.exists():
        for ln in jfile.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            done[(r["doc_id"], r["chunk_index"])] = r
    t0 = time.time()
    with jfile.open("a", encoding="utf-8") as f:
        for n, (d, qv) in enumerate(zip(docs, qvecs)):
            res = idx.search(d["text"], qv, top=top_k)
            dense = dict(res["dense"])
            dense_all = idx.vecs @ qv
            for rank, (ci, hs) in enumerate(res["hybrid"]):
                if (d["doc_id"], ci) in done:
                    continue
                c = idx.chunks[ci]
                prompt = (
                    "문서와 지침 조항이 주어진다. 이 조항이 이 문서를 (작성·분류·보존·공개·열람·보호·관리)하는 "
                    "방법을 **직접** 규정하는가?\n"
                    "관련(true): 조항이 이 문서가 다루는 사안이나 이 문서와 같은 종류의 문서의 취급을 직접 정한다.\n"
                    "무관(false): 낱말만 겹친다, 일반적 절차일 뿐이다, 다른 종류 문서의 규정이다.\n"
                    '출력은 JSON 한 줄만: {"relevant": true 또는 false, "reason": "20자 이내"}\n\n'
                    f"[문서 앞부분]\n{d['text'][:1000]}\n\n[조항]\n{c['body'][:1400]}")
                raw = chat(prompt, num_predict=80)
                m = re.search(r'"relevant"\s*:\s*(true|false)', raw)
                rec = {"doc_id": d["doc_id"], "chunk_index": ci, "chunk_id": c["id"], "page": c["page"],
                       "hybrid_rank": rank + 1, "dense_score": float(dense_all[ci]), "hybrid_score": hs,
                       "relevant": (m.group(1) == "true") if m else None, "raw": raw[:120]}
                done[(d["doc_id"], ci)] = rec
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
            if (n + 1) % 10 == 0:
                print(f"  b2 {n + 1}/{len(docs)}  {time.time() - t0:.0f}s", flush=True)
    # 집계
    by_doc: dict[str, list[dict]] = {}
    for (did, _), r in done.items():
        by_doc.setdefault(did, []).append(r)
    src = {d["doc_id"]: d.get("source", "") for d in docs}
    return _summarize_b2(by_doc, src, top_k)


def _summarize_b2(by_doc: dict, src: dict, top_k: int) -> dict:
    def group(d: str) -> str:
        return "합성업무문서" if src.get(d) == "proxy_gold_authored" else "판례·금융"
    res: dict = {"n_docs": len(by_doc)}
    unparsed = sum(1 for rs in by_doc.values() for r in rs if r["relevant"] is None)
    res["unparsed_judgments"] = unparsed
    for name, sel in (("전체", lambda d: True), ("합성업무문서", lambda d: group(d) == "합성업무문서"),
                      ("판례·금융", lambda d: group(d) == "판례·금융")):
        docs = [d for d in by_doc if sel(d)]
        if not docs:
            continue
        top1_rel = sum(1 for d in docs if next((r for r in by_doc[d] if r["hybrid_rank"] == 1), {}).get("relevant") is True)
        any_rel = sum(1 for d in docs if any(r["relevant"] is True for r in by_doc[d]))
        pairs = [r for d in docs for r in by_doc[d] if r["relevant"] is not None]
        rel_pairs = sum(1 for r in pairs if r["relevant"])
        top1 = {d: next((r for r in by_doc[d] if r["hybrid_rank"] == 1), None) for d in docs}
        pos = [t["dense_score"] for t in top1.values() if t and t["relevant"] is True]
        neg = [t["dense_score"] for t in top1.values() if t and t["relevant"] is False]
        res[name] = {
            "docs": len(docs),
            f"top{top_k}에_관련조항_하나라도": f"{any_rel}/{len(docs)}",
            "1위가_관련": f"{top1_rel}/{len(docs)}",
            "조항쌍_관련": f"{rel_pairs}/{len(pairs)}",
            "1위_dense점수_AUROC(관련 vs 무관)": None if auroc(pos, neg) is None else round(auroc(pos, neg), 3),
            "1위_dense점수_관련_중앙": round(float(np.median(pos)), 3) if pos else None,
            "1위_dense점수_무관_중앙": round(float(np.median(neg)), 3) if neg else None,
        }
    return res


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="규정 조항 근거 표시 시험")
    ap.add_argument("--text", required=True)
    ap.add_argument("--format", choices=["pages", "article"], default="pages", help="pages=쪽 표지+글머리 지침, article=제N조 규정")
    ap.add_argument("--docs", required=True, help="jsonl (doc_id, text, source)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--stages", default="chunk,embed,b1,b2")
    ap.add_argument("--b1-n", type=int, default=60)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--doc-chars", type=int, default=1500)
    ap.add_argument("--b2-file", default="b2_judgments.jsonl", help="B2 판정 캐시 파일명(방식을 바꿔 다시 돌릴 때 새 이름)")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stages = set(a.stages.split(","))

    chunks = build_chunks(Path(a.text), a.format)
    (out / "chunks.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks), encoding="utf-8")
    lens = sorted(len(c["text"]) for c in chunks)
    print(f"조각 {len(chunks)}개 · 글자 중앙 {lens[len(lens) // 2]} · 최소 {lens[0]} · 최대 {lens[-1]}")

    vfile = out / "chunk_vecs.npy"
    if vfile.exists() and np.load(vfile).shape[0] == len(chunks):
        vecs = np.load(vfile)
    elif "embed" in stages or "b1" in stages or "b2" in stages:
        vecs = embed([c["body"] for c in chunks])
        np.save(vfile, vecs)
    else:
        return 0
    idx = Index(chunks, vecs)
    summary: dict = {"n_chunks": len(chunks), "embed_model": EMBED_MODEL, "llm_model": LLM_MODEL, "seed": SEED}

    if "b1" in stages:
        summary["B1"] = stage_b1(idx, out, a.b1_n)
        print(json.dumps(summary["B1"], ensure_ascii=False, indent=1))
    if "b2" in stages:
        docs = [json.loads(ln) for ln in Path(a.docs).read_text(encoding="utf-8").splitlines() if ln.strip()]
        summary["B2"] = stage_b2(idx, docs, out, a.doc_chars, a.top_k, a.b2_file)
        print(json.dumps(summary["B2"], ensure_ascii=False, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
