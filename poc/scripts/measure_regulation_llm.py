# -*- coding: utf-8 -*-
"""규정 참고 표시의 「로컬 LLM 으로 해당 항 고르기」 옵션을 **제품 경로 그대로** 잰다 — 보이는 문서 수 · 문서당 지연 · 화면에 뜰 항목.

왜 있나(2026-09-26). 조회 점수로는 해당 여부를 못 가르므로(설계서 §3.4) 로컬 LLM 이 후보 조항에서 항을 고르게 했다(§3.6). 이 도구는
서비스(RegulationEvidenceService) 를 실제 임베더·실제 로컬 LLM 공급자로 돌려, 옵션을 켰을 때 문서마다 무엇이 보이는지와 걸리는 시간을 낸다.
결과 JSON 은 `measure_regulation_runtime.py --dump-items` 와 같은 모양(`item`·`reason`)이라 `analyze_regulation_judgments.py` 와 판정 시트에 그대로 쓴다.

어떻게 재나(제품과 같은 경로)
  · 규정 색인·문서 벡터: measure_regulation_runtime.py 와 같다(split_regulation → 임베딩, PreprocessPipeline → 청크 평균)
  · 조회·LLM: RegulationEvidenceService._find(...) — 후보 조항 rank_clauses → llm_select.select_applicable(공급자 = build_provider)
  · 공급자: --provider(기본 ollama). ⚠ 모델 이름은 환경변수 LOCAL_LLM_MODEL 로 정한다(예: qwen3:14b)

⚠ 정답 조항(`--labels`)은 사람이 조회 전에 정한 것이어야 한다. 이 도구는 정답으로 점수를 매기지 않고 문서 목록을 거르는 데만 쓴다.
⚠ 규정·문서가 시험용이면 그 값은 회원사 실제 규정의 값이 아니다.

사용:
  LOCAL_LLM_MODEL=qwen3:14b python scripts/measure_regulation_llm.py --regulation 규정.md --docs 문서.jsonl --labels 정답.json \
      --out llm_items.json [--candidates 5] [--doc-chars 1500] [--max-docs N] [--device cpu]
"""

from __future__ import annotations

import argparse
import inspect
import io
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regulation", required=True, help="규정 파일(txt/md)")
    ap.add_argument("--docs", required=True, help="문서 jsonl — {doc_id, text}")
    ap.add_argument("--labels", required=True, help="문서 목록을 거르는 정답 json — {doc_id: [...]} (키만 쓴다)")
    ap.add_argument("--out", required=True, help="문서마다 화면에 뜰 항목을 저장할 JSON")
    ap.add_argument("--provider", default="ollama", help="LLM 공급자(로컬만: ollama·vllm·local_openai·lm_studio)")
    ap.add_argument("--mode", choices=("single", "per_candidate"), default="per_candidate",
                    help="single = 문서를 한 번만 보이고 가장 직접적인 항을 고르게(호출 2번) · per_candidate = 후보마다 따로 묻기(기본, 호출 6번)")
    ap.add_argument("--base-url", default="", help="Ollama 주소를 정한다(예: http://127.0.0.1:11434/v1 — 윈도에서 localhost 는 호출마다 0.4초쯤 더 든다)")
    ap.add_argument("--candidates", type=int, default=5)
    ap.add_argument("--doc-chars", type=int, default=1500)
    ap.add_argument("--timeout-s", type=float, default=120.0)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--max-docs", type=int, default=0)
    ap.add_argument("--dump-head", type=int, default=2000)
    ap.add_argument("--dump-raw", action="store_true", help="문서마다 LLM 호출의 답 원문·프롬프트 글자 수를 결과에 남긴다(답을 못 읽는 원인을 볼 때)")
    ap.add_argument("--device", choices=("auto", "cpu"), default="auto")
    a = ap.parse_args(argv)

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    os.environ.setdefault("EMBEDDING_PROVIDER", "hf")
    os.environ.setdefault("EMB_CACHE_ENABLED", "0")
    os.environ.setdefault("EMB_REDIS_ENABLED", "0")
    if a.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"       # 빈 문자열이 아니라 "-1" — Windows 는 빈 값이면 변수를 지운다

    import numpy as np  # noqa: PLC0415

    from koipa.adapters.embedding import build_embedder, embedder_digest  # noqa: PLC0415
    from koipa.adapters.llm import build_provider  # noqa: PLC0415
    from koipa.modules.m2_preprocess.pipeline import PreprocessPipeline  # noqa: PLC0415
    from koipa.regulation import vectors  # noqa: PLC0415
    from koipa.regulation.index import ClauseRec, RegulationIndex, SentenceRec  # noqa: PLC0415
    from koipa.regulation.splitter import compose_embed_text, split_regulation  # noqa: PLC0415
    from koipa.regulation.tagger import default_display, tag_clause  # noqa: PLC0415
    from koipa.services.document_vector_service import _mean_unit_vector  # noqa: PLC0415
    from koipa.services.regulation_evidence_service import RegulationEvidenceService  # noqa: PLC0415

    keys = set(json.loads(Path(a.labels).read_text(encoding="utf-8")))
    docs = [json.loads(ln) for ln in Path(a.docs).read_text(encoding="utf-8").splitlines() if ln.strip()]
    docs = [d for d in docs if d["doc_id"] in keys]
    if a.max_docs > 0:
        docs = docs[: a.max_docs]

    emb = build_embedder()
    digest = embedder_digest(emb)
    if digest["effective"] == "hash" or digest["degraded"]:
        print(f"⛔ 실제 임베더가 아니다({digest}) — 이 숫자는 인용할 수 없다.")
        return 2
    emb.embed(["워밍업"])

    res = split_regulation(Path(a.regulation).read_text(encoding="utf-8"))
    shown = [c for c in res.clauses if default_display(tag_clause(c.title, c.chapter))]
    items: list[tuple[str, int, int | None, str]] = []
    for c in shown:
        items.append(("clause", c.seq, None, compose_embed_text(c.article_no, c.title, c.chapter, c.text)))
        for s in c.sentences:
            if not s.is_lead:
                items.append(("sentence", c.seq, s.seq, s.text))
    vecs: list[list[float]] = []
    for i in range(0, len(items), 32):
        vecs.extend(emb.embed([t for _, _, _, t in items[i:i + 32]]).vectors)
    by_clause = {seq: v for (k, seq, _, _), v in zip(items, vecs, strict=True) if k == "clause"}
    by_sent = {(seq, sq): v for (k, seq, sq, _), v in zip(items, vecs, strict=True) if k == "sentence"}
    recs = []
    for c in shown:
        sents = tuple(SentenceRec(f"{c.seq}-{s.seq}", s.seq, s.text, s.is_lead, s.list_group,
                                  None if s.is_lead else vectors.normalize(by_sent[(c.seq, s.seq)])) for s in c.sentences)
        recs.append(ClauseRec(f"c{c.seq}", "r", "규정", "v", c.seq, c.article_no, c.title,
                              compose_embed_text(c.article_no, c.title, c.chapter, c.text), vectors.normalize(by_clause[c.seq]), sents))
    index = RegulationIndex(recs, digest["effective"])

    pre = PreprocessPipeline()
    doc_vec: dict[str, np.ndarray] = {}
    doc_text: dict[str, str] = {}
    for d in docs:
        result = pre.run_text_full(d["text"])
        texts = [c.text for c in result.chunks if c.text]
        if not texts:
            continue
        doc_vec[d["doc_id"]] = np.asarray(_mean_unit_vector([list(v) for v in emb.embed(texts).vectors]), dtype=np.float32)
        doc_text[d["doc_id"]] = "\n".join(texts[:30])[:6000]

    class _Store:
        def get(self, doc_id: str):
            v = doc_vec.get(doc_id)
            return None if v is None else SimpleNamespace(model=digest["effective"], embedding=v)

    if a.base_url and a.provider == "ollama":
        from koipa.adapters.llm.local_openai_provider import LocalOpenAIProvider  # noqa: PLC0415
        provider = LocalOpenAIProvider(base_url=a.base_url, api_key="ollama", provider_label="ollama", model=os.environ.get("LOCAL_LLM_MODEL", "qwen3:14b"))
    else:
        provider = build_provider(a.provider)
    raw_calls: list[dict] = []
    if a.dump_raw:
        _orig = provider.generate

        _orig_takes_no_reasoning = "no_reasoning" in inspect.signature(_orig).parameters

        def _recording(prompt, *, system=None, max_tokens=1024, temperature=0.7, json_schema=None, no_reasoning=False):
            # ⚠ 서명에 json_schema·no_reasoning 이 있어야 한다 — accepts_json_schema()·llm_select 가 서명을 보고 구조화 출력·추론 끄기를 요청할지 정한다
            #   (**kw 로 받으면 스키마가 꺼지고 Ollama 의 Qwen3 가 추론을 돌려 답이 비는 채로 재게 된다)
            extra = {"no_reasoning": no_reasoning} if _orig_takes_no_reasoning else {}
            r = _orig(prompt, system=system, max_tokens=max_tokens, temperature=temperature, json_schema=json_schema, **extra)
            u = getattr(r, "usage", None)
            raw_calls.append({"prompt_chars": len(prompt), "text": getattr(r, "text", ""), "ok": getattr(u, "success", True),
                              "error": getattr(u, "error_code", None), "finish": (getattr(r, "meta", None) or {}).get("finish_reason"),
                              "out_tokens": getattr(u, "output_tokens", None)})
            return r

        provider.generate = _recording
    cfg = SimpleNamespace(regulation_evidence_max_items=1, regulation_min_similarity=0.0, regulation_llm_select_enabled=True,
                          llm_provider=a.provider, regulation_llm_candidates=a.candidates, regulation_llm_doc_chars=a.doc_chars,
                          regulation_llm_timeout_s=a.timeout_s, regulation_llm_max_concurrency=a.concurrency, regulation_llm_cache_ttl_s=0,
                          regulation_llm_mode=a.mode)
    svc = RegulationEvidenceService(vector_store=_Store(), settings_obj=cfg, doc_text_provider=lambda d: doc_text[d],
                                    document_exists=lambda d: True, llm_provider_factory=lambda: provider)
    groups = {digest["effective"]: index}
    print(f"모델 {getattr(provider, 'model', '?')} · 공급자 {a.provider} · 방식 {a.mode} · 후보 {a.candidates}개 · 문서 {len(doc_vec)}건")

    out, secs = [], []
    full_path: list[bool] = []                 # 문서마다 — 공개 자료로 걸러지지 않아 후보 판정까지 간 문서인가(걸러진 문서는 종류 확인 호출 하나로 끝나 더 빠르다)
    by_reason: dict[str, int] = {}
    for d in docs:
        if d["doc_id"] not in doc_vec:
            continue
        raw_calls.clear()
        t0 = time.perf_counter()
        r = svc._find(d["doc_id"], groups, None, use_cache=False)
        secs.append(time.perf_counter() - t0)
        full_path.append(r.reason != "public_document")
        by_reason["(보임)" if r.items else str(r.reason)] = by_reason.get("(보임)" if r.items else str(r.reason), 0) + 1
        first = r.items[0] if r.items else None
        out.append({"doc_id": d["doc_id"], "doc_text_head": d["text"][: a.dump_head], "reason": r.reason, "secs": round(secs[-1], 3),
                    "item": None if first is None else {"regulation": first.rgltn_nm, "article_no": first.article_no, "title": first.title,
                                                       "sentences": list(first.sentences), "is_grade_list": first.is_grade_list}})
        if a.dump_raw:
            out[-1]["raw"] = list(raw_calls)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    full = [s for s, f in zip(secs, full_path, strict=True) if f]
    secs.sort()
    n = len(secs)
    print(f"문서 {n}건 · 규정이 보인 문서 {by_reason.get('(보임)', 0)}건 · 이유별 {by_reason}")
    print(f"문서당 지연: 평균 {sum(secs) / n:.1f}s · 중앙 {secs[n // 2]:.1f}s · p95 {secs[min(n - 1, int(0.95 * (n - 1) + 0.5))]:.1f}s · 최대 {secs[-1]:.1f}s")
    if full:
        print(f"  그중 공개 자료로 걸러지지 않은 문서 {len(full)}건(후보 판정까지 감): 평균 {sum(full) / len(full):.1f}s · 최대 {max(full):.1f}s")
    print(f"저장: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
