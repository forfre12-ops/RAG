# -*- coding: utf-8 -*-
"""규정 참고 표시를 **운영 임베더**(KURE-v1)로 잰다 — 색인 속도와 조회 적중.

왜 있나(2026-09-25). 처음 잰 값(문서 3건 중 1건에서 직접 적용 문장이 뜸)은 시험용 임베더(ollama bge-m3)로 낸 것이었다.
운영은 KURE-v1 이고, 색인 시간은 "조항+문장당 0.51초"라는 다른 작업의 값에서 옮겨 온 **추정**이었다. 둘 다 운영 임베더로
다시 재는 도구다. 회원사 규정이 들어오면 같은 명령으로 그 규정에서도 잰다.

무엇을 재나
  ① 속도   모델 적재 · 규정 색인(표시 대상 조항 + 서두가 아닌 문장, 32개씩 묶음 — 서비스와 같다) · 문서 벡터(청크 임베딩 평균)
  ② 적중   문서 → 규정 조회(제품 엔진 그대로) 1위 · 상위 3위가 **사람이 미리 정한 적용 조항**에 드는가, 무작위일 때의 기대 적중과 나란히

어떻게 재나(제품과 같은 경로)
  · 규정: split_regulation → tag_clause → 표시 대상만 → compose_embed_text 임베딩 (services/regulation_service 와 같다)
  · 문서: PreprocessPipeline(청크 512자·겹침 64·PII 마스킹) → 청크 임베딩 평균 → 단위벡터 (services/document_vector_service 와 같다)
  · 조회: RegulationIndex.find(문서 벡터, 문서 글자, 1개/3개)

⚠ 정답 조항(`--labels`)은 **사람이 조회 전에 정한 것**이어야 한다. 조회 결과를 보고 정하면 적중률이 부풀려진다.
⚠ 규정·문서가 시험용(우리가 만든 것)이면 그 값은 회원사 실제 규정의 값이 아니다. 결과 JSON 에 입력 파일 경로를 남긴다.

사용:
  python scripts/measure_regulation_runtime.py --regulation 규정.md --docs 문서.jsonl --labels 정답.json [--device cpu] [--json out.json]
  문서.jsonl  한 줄에 {"doc_id": ..., "text": ...}
  정답.json   {"doc_id": [적용 조항 번호(숫자), ...], ...}   예: {"D-001": [12, 40]}
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

EMBED_BATCH = 32          # services/regulation_service.EMBED_BATCH 와 같은 값


def _article_no(article_no: str) -> int | None:
    m = re.match(r"제(\d+)조", article_no)
    return int(m.group(1)) if m else None


def random_hit(n: int, labeled: int, k: int) -> float:
    """무작위로 k 개를 골랐을 때 정답 조항이 하나라도 들 확률 = 1 - C(n-L,k)/C(n,k)."""
    if n <= 0 or labeled <= 0:
        return 0.0
    k = min(k, n)
    if n - labeled < k:
        return 1.0
    return 1.0 - math.comb(n - labeled, k) / math.comb(n, k)


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _signals(index, first, doc_vec, doc_text: str) -> dict:
    """1위로 보인 조항이 **얼마나 확실히** 1위였는지 — 문턱(regulation_min_similarity)을 정하는 데 쓸 수 있는가를 재려는 값.

    화면·API 에는 나가지 않는다(점수 비노출 원칙). 조회 엔진 내부(index.find)와 같은 계산을 다시 해서 옆에서 읽는다.
    """
    import numpy as np  # noqa: PLC0415

    from koipa.regulation.index import _rank_positions  # noqa: PLC0415

    dv = np.asarray(doc_vec, dtype=np.float32)
    dense = index._matrix @ dv
    lex = index._lexical.scores(doc_text)
    rr_d, rr_l = _rank_positions(dense), _rank_positions(lex)
    pos = next(i for i, c in enumerate(index.clauses) if c.clause_id == first.clause_id)
    clause = index.clauses[pos]
    cand = [float(s.vec @ dv) for s in clause.sentences if not s.is_lead and s.vec is not None]
    ordered = sorted((float(x) for x in dense), reverse=True)
    return {"dense_shown": round(float(dense[pos]), 4), "dense_max": round(ordered[0], 4),
            "dense_gap_to_second": round(ordered[0] - ordered[1], 4) if len(ordered) > 1 else None,
            "dense_rank": int(rr_d[pos]), "lex_shown": round(float(lex[pos]), 4), "lex_max": round(float(lex.max()), 4),
            "lex_rank": int(rr_l[pos]), "sentence_cos": round(max(cand), 4) if cand else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regulation", required=True, help="규정 파일(txt/md)")
    ap.add_argument("--docs", required=True, help="문서 jsonl — {doc_id, text}")
    ap.add_argument("--labels", required=True, help="정답 json — {doc_id: [조항 번호, ...]}")
    ap.add_argument("--device", choices=("auto", "cpu"), default="auto", help="cpu 면 GPU 를 가린다(고객사 대부분의 환경)")
    ap.add_argument("--max-docs", type=int, default=0, help="앞에서 N건만(0=전부)")
    ap.add_argument("--json", default="", help="결과를 이 경로에 JSON 으로 저장")
    ap.add_argument("--dump-items", default="", help="문서마다 검수 화면에 실제로 보일 항목(1위)을 문서 앞부분과 함께 저장 — 사람 판정용")
    ap.add_argument("--dump-head", type=int, default=700, help="--dump-items 에 실을 문서 앞부분 글자 수")
    ap.add_argument("--dump-topk", type=int, default=1, help="--dump-items 에 문서마다 실을 후보 개수(조회 순위 순) — LLM 재판정 같은 후단 시험용")
    a = ap.parse_args(argv)

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    # 설정은 koipa 를 부르기 전에 정한다 — 운영과 같은 임베더(hf)·캐시 없음(시간을 정직하게 재려고)
    os.environ.setdefault("EMBEDDING_PROVIDER", "hf")
    os.environ.setdefault("EMB_CACHE_ENABLED", "0")
    os.environ.setdefault("EMB_REDIS_ENABLED", "0")
    if a.device == "cpu":
        # ⚠ 빈 문자열("")이 아니라 "-1" 이다 — Windows 는 빈 값으로 환경변수를 설정하면 **지워 버려서** GPU 가 그대로 보인다
        #   (2026-09-25 실측: --device cpu 로 돌렸는데 장치 줄에 cuda 가 찍혔다). 그래서 결과에 장치를 항상 적는다.
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

    import numpy as np  # noqa: PLC0415

    from koipa.adapters.embedding import build_embedder, embedder_digest  # noqa: PLC0415
    from koipa.modules.m2_preprocess.pipeline import PreprocessPipeline  # noqa: PLC0415
    from koipa.regulation import vectors  # noqa: PLC0415
    from koipa.regulation.index import ClauseRec, RegulationIndex, SentenceRec  # noqa: PLC0415
    from koipa.regulation.splitter import compose_embed_text, split_regulation  # noqa: PLC0415
    from koipa.regulation.tagger import default_display, tag_clause  # noqa: PLC0415
    from koipa.services.document_vector_service import _mean_unit_vector  # noqa: PLC0415

    reg_path, docs_path, labels_path = Path(a.regulation), Path(a.docs), Path(a.labels)
    labels = {k: {int(x) for x in v} for k, v in json.loads(labels_path.read_text(encoding="utf-8")).items()}
    docs = [json.loads(ln) for ln in docs_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    docs = [d for d in docs if d["doc_id"] in labels]
    if a.max_docs > 0:
        docs = docs[: a.max_docs]

    out: dict = {"inputs": {"regulation": str(reg_path), "docs": str(docs_path), "labels": str(labels_path),
                            "docs_measured": len(docs), "device_requested": a.device}}

    # ── 임베더 ──────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    emb = build_embedder()
    digest = embedder_digest(emb)
    if digest["effective"] == "hash" or digest["degraded"]:
        print(f"⛔ 실제 임베더가 아니다({digest}) — 이 숫자는 인용할 수 없다. 모델이 캐시에 있는지 확인하라.")
        return 2
    emb.embed(["워밍업"])
    load_s = time.perf_counter() - t0
    try:
        import torch  # noqa: PLC0415
        device = "cuda:" + torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
        threads = torch.get_num_threads()
    except Exception:  # noqa: BLE001
        device, threads = "unknown", 0
    if a.device == "cpu" and device.startswith("cuda"):
        print(f"⛔ --device cpu 로 요청했는데 장치가 {device} 이다 — 이 시간은 CPU 값이 아니다. 중단한다.")
        return 2
    out["embedder"] = {**digest, "device": device, "torch_threads": threads, "load_and_warmup_seconds": round(load_s, 2)}
    print(f"임베더 {digest['effective']} · 차원 {digest['dim']} · 장치 {device} · 스레드 {threads} · 적재+워밍업 {load_s:.1f}s")

    # ── 규정 색인(서비스와 같은 순서) ─────────────────────────────────────
    res = split_regulation(reg_path.read_text(encoding="utf-8"))
    shown = [c for c in res.clauses if default_display(tag_clause(c.title, c.chapter))]
    items: list[tuple[str, int, int | None, str]] = []      # (종류, 조항 순번, 문장 순번, 글자)
    for c in shown:
        items.append(("clause", c.seq, None, compose_embed_text(c.article_no, c.title, c.chapter, c.text)))
        for s in c.sentences:
            if not s.is_lead:
                items.append(("sentence", c.seq, s.seq, s.text))
    t0 = time.perf_counter()
    vecs: list[list[float]] = []
    for i in range(0, len(items), EMBED_BATCH):
        vecs.extend(emb.embed([t for _, _, _, t in items[i:i + EMBED_BATCH]]).vectors)
    reg_s = time.perf_counter() - t0
    out["regulation_index"] = {"mode": res.mode, "clauses": len(res.clauses), "display_clauses": len(shown),
                               "embedded_items": len(items), "seconds": round(reg_s, 2),
                               "seconds_per_item": round(reg_s / max(1, len(items)), 3)}
    print(f"규정 색인: 분할 {res.mode} · 조항 {len(res.clauses)} · 표시 대상 {len(shown)} · 임베딩 {len(items)}건 "
          f"→ {reg_s:.1f}s ({reg_s / max(1, len(items)):.3f}s/건)")

    by_clause_vec = {seq: v for (kind, seq, _, _), v in zip(items, vecs) if kind == "clause"}
    by_sent_vec = {(seq, sq): v for (kind, seq, sq, _), v in zip(items, vecs) if kind == "sentence"}
    recs = []
    for c in shown:
        sents = tuple(SentenceRec(f"{c.seq}-{s.seq}", s.seq, s.text, s.is_lead, s.list_group,
                                  None if s.is_lead else vectors.normalize(by_sent_vec[(c.seq, s.seq)]))
                      for s in c.sentences)
        recs.append(ClauseRec(f"c{c.seq}", "r", "규정", "v", c.seq, c.article_no, c.title,
                              compose_embed_text(c.article_no, c.title, c.chapter, c.text),
                              vectors.normalize(by_clause_vec[c.seq]), sents))
    index = RegulationIndex(recs, digest["effective"])
    shown_nos = {_article_no(c.article_no) for c in shown}

    # ── 문서 벡터(제품과 같은 경로) + 조회 ─────────────────────────────────
    pre = PreprocessPipeline()
    per_doc_s: list[float] = []
    chunk_counts: list[int] = []
    find_ms: list[float] = []
    h1 = h3 = 0
    r1 = r3 = 0.0
    scored = 0
    details = []
    shown_items: list[dict] = []
    for d in docs:
        result = pre.run_text_full(d["text"])
        texts = [c.text for c in result.chunks if c.text]
        if not texts:
            continue
        t0 = time.perf_counter()
        dv = np.asarray(_mean_unit_vector([list(v) for v in emb.embed(texts).vectors]), dtype=np.float32)
        per_doc_s.append(time.perf_counter() - t0)
        chunk_counts.append(len(texts))
        doc_text = "\n".join(texts[:30])
        t1 = time.perf_counter()
        top1 = index.find(vectors.normalize(dv), doc_text, max_items=1)
        top3 = index.find(vectors.normalize(dv), doc_text, max_items=3)
        find_ms.append((time.perf_counter() - t1) * 1000 / 2)
        lab = labels[d["doc_id"]]
        lab_shown = lab & shown_nos
        hit1 = bool(top1.items) and _article_no(top1.items[0].article_no) in lab
        hit3 = any(_article_no(i.article_no) in lab for i in top3.items)
        if lab_shown:
            scored += 1
            r1 += random_hit(len(shown), len(lab_shown), 1)
            r3 += random_hit(len(shown), len(lab_shown), 3)
        h1 += hit1
        h3 += hit3
        details.append({"doc_id": d["doc_id"], "labels": sorted(lab), "top1": top1.items[0].article_no if top1.items else None,
                        "top3": [i.article_no for i in top3.items], "hit1": hit1, "hit3": hit3, "chunks": len(texts)})
        if a.dump_items:
            first = top1.items[0] if top1.items else None
            cands = index.find(vectors.normalize(dv), doc_text, max_items=max(1, a.dump_topk)).items
            shown_items.append({
                "candidates": [{"article_no": c.article_no, "title": c.title, "sentences": list(c.sentences),
                                "is_grade_list": c.is_grade_list} for c in cands],
                "doc_id": d["doc_id"], "doc_text_head": d["text"][:a.dump_head], "labels": sorted(lab), "hit1": hit1,
                "reason": top1.reason,
                "item": None if first is None else {
                    "regulation": first.rgltn_nm, "article_no": first.article_no, "title": first.title,
                    "sentences": list(first.sentences), "is_grade_list": first.is_grade_list},
                "signals": None if first is None else _signals(index, first, vectors.normalize(dv), doc_text)})

    n = len(details)
    out["documents"] = {"count": n, "chunks_total": sum(chunk_counts),
                        "chunks_per_doc_median": sorted(chunk_counts)[len(chunk_counts) // 2] if chunk_counts else 0,
                        "embed_seconds_per_doc_median": round(sorted(per_doc_s)[len(per_doc_s) // 2], 2) if per_doc_s else 0,
                        "embed_seconds_per_chunk": round(sum(per_doc_s) / max(1, sum(chunk_counts)), 3)}
    out["lookup"] = {"top1_hits": h1, "top3_hits": h3, "documents": n,
                     "random_top1_expected": round(r1, 1), "random_top3_expected": round(r3, 1),
                     "docs_with_a_labeled_shown_clause": scored,
                     "find_ms_median": round(_percentile(find_ms, 0.5), 3), "find_ms_p95": round(_percentile(find_ms, 0.95), 3)}
    out["details"] = details
    print(f"문서 벡터: {n}건 · 청크 {sum(chunk_counts)}개 · 문서당 중앙 {out['documents']['embed_seconds_per_doc_median']}s · "
          f"청크당 {out['documents']['embed_seconds_per_chunk']}s")
    print(f"조회 적중: 1위 {h1}/{n} (무작위 기대 {r1:.1f}) · 상위 3위 {h3}/{n} (무작위 기대 {r3:.1f}) · 조회 자체 p95 {out['lookup']['find_ms_p95']}ms")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"저장: {a.json}")
    if a.dump_items:
        Path(a.dump_items).write_text(json.dumps(shown_items, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"검수 화면에 보일 항목 저장(사람 판정용): {a.dump_items} — {len(shown_items)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
