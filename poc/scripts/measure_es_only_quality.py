"""ES-only(검색형 kNN) 분류의 데이터 품질 측정 — 분류기 제거 의사결정용.

질문: 분류기를 빼고 ES(골든셋 kNN 검색)만으로 분류해도 데이터 품질이 유지되는가?
      유지된다면, 그 대가(골든셋 밖 = OOD)는 얼마나 큰가?

설계:
  - 레퍼런스(=ES가 뒤지는 골든셋)  : gold_real/classification_gold.jsonl − holdout (668)
  - 쿼리(=들어오는 실트래픽)        : gold_real/holdout_eval.jsonl       (109, 정답 label 보유)
    (holdout은 classification_gold의 부분집합이므로 레퍼런스에서 제외해 자기매칭 방지)
  - 임베딩 : BAAI/bge-m3 (운영과 동일 모델, sentence-transformers 백엔드, cosine)
  - 검색   : in-process 정확 코사인 kNN (ES dense_vector cosine+HNSW의 정확판; BM25 하이브리드 절반은 제외)

산출:
  1) OOD율        — top-1 코사인 유사도 < τ 인 쿼리 비율(= 검수 큐로 빠지는 비율 = 자동화율 상한)
  2) 커버 구간 오라벨율 — 유사도 ≥ τ 인 쿼리 중 kNN 예측라벨 ≠ 정답 비율
  3) TS 미탐 분해 — 정답 TS가 (a) OOD로 빠지는지 (b) 하위등급으로 오분류되는지(무인미탐 위험)
  임계 τ 를 스윕해 (자동화율 ↔ 품질) 트레이드오프 곡선을 만든다.

실행(poc/ 에서):
  python scripts/measure_es_only_quality.py
임베딩은 reports/ood_embeds.npz 에 캐시 — 재실행 시 즉시.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "datasets/gold_real/classification_gold.jsonl"
HOLDOUT = ROOT / "datasets/gold_real/holdout_eval.jsonl"
CACHE = ROOT / "reports/ood_embeds.npz"
OUT = ROOT / "reports/es_only_quality.json"
MODEL = "BAAI/bge-m3"
GRADES = ["TS", "S1", "S2", "S3"]
SEVERITY = {"TS": 3, "S1": 2, "S2": 1, "S3": 0}  # 높을수록 민감 → 하위로 떨어지면 미탐
TEXT_CAP = 2000   # 유사도 측정용 본문 절단(속도/일관성). 분류 품질엔 영향 작음.
MAX_SEQ = 512


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def text_of(r: dict) -> str:
    return (r.get("text") or r.get("body") or "").strip()


def sha(t: str) -> str:
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


def embed_all(texts: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    print(f"[embed] loading {MODEL} (cpu)...", flush=True)
    m = SentenceTransformer(MODEL, device="cpu")
    m.max_seq_length = MAX_SEQ
    print(f"[embed] encoding {len(texts)} docs (cap={TEXT_CAP}, max_seq={MAX_SEQ})...", flush=True)
    vecs = m.encode(
        [t[:TEXT_CAP] for t in texts],
        batch_size=16,
        normalize_embeddings=True,  # cosine = dot
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    return np.asarray(vecs, dtype=np.float32)


def main() -> int:
    gold = load(GOLD)
    holds = load(HOLDOUT)
    hold_ids = {r["doc_id"] for r in holds}

    # 레퍼런스 = gold − holdout (doc_id 기준 분리)
    ref = [r for r in gold if r["doc_id"] not in hold_ids]
    qry = holds
    print(f"[data] reference(골든)={len(ref)}  query(holdout)={len(qry)}")
    print(f"[data] ref  label dist: {dict(Counter(r['label'] for r in ref))}")
    print(f"[data] qry  label dist: {dict(Counter(r['label'] for r in qry))}")

    ref_text = [text_of(r) for r in ref]
    qry_text = [text_of(r) for r in qry]
    ref_lab = [r["label"] for r in ref]
    qry_lab = [r["label"] for r in qry]
    ref_hash = [sha(t) for t in ref_text]

    # ── 임베딩 (캐시) ──────────────────────────────────────────────
    sig = sha("|".join(ref_text) + "##" + "|".join(qry_text))[:12]
    if CACHE.exists():
        z = np.load(CACHE, allow_pickle=True)
        if str(z.get("sig")) == sig:
            print("[embed] cache hit")
            ref_vec, qry_vec = z["ref"], z["qry"]
        else:
            print("[embed] cache stale → 재계산")
            ref_vec = qry_vec = None
    else:
        ref_vec = qry_vec = None
    if ref_vec is None:
        allv = embed_all(ref_text + qry_text)
        ref_vec, qry_vec = allv[: len(ref_text)], allv[len(ref_text):]
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        np.savez(CACHE, ref=ref_vec, qry=qry_vec, sig=sig)
        print(f"[embed] cached → {CACHE}")

    # ── 코사인 kNN ────────────────────────────────────────────────
    # 정규화 벡터라 sim = qry · refᵀ
    sims = qry_vec @ ref_vec.T  # (109, 668)
    K = 5
    per_query = []
    for i in range(len(qry)):
        s = sims[i].copy()
        # 자기 텍스트(해시) 동일 ref 제외 — 방어적 leave-one-out
        qh = sha(qry_text[i])
        for j, rh in enumerate(ref_hash):
            if rh == qh:
                s[j] = -1.0
        order = np.argsort(-s)
        top1 = float(s[order[0]])
        topk = order[:K]
        # 유사도 가중 투표
        vote: dict[str, float] = defaultdict(float)
        for j in topk:
            vote[ref_lab[j]] += float(s[j])
        pred = max(vote.items(), key=lambda kv: kv[1])[0]
        per_query.append({
            "doc_id": qry[i]["doc_id"],
            "true": qry_lab[i],
            "pred": pred,
            "top1_sim": top1,
            "topk_labels": [ref_lab[j] for j in topk],
            "topk_sims": [round(float(s[j]), 4) for j in topk],
        })

    top1s = np.array([q["top1_sim"] for q in per_query])
    print(f"\n[sim] top-1 코사인 분포: min={top1s.min():.3f} p10={np.percentile(top1s,10):.3f} "
          f"median={np.median(top1s):.3f} p90={np.percentile(top1s,90):.3f} max={top1s.max():.3f}")

    # ── 임계 스윕 ─────────────────────────────────────────────────
    def metrics_at(tau: float) -> dict:
        covered = [q for q in per_query if q["top1_sim"] >= tau]
        ncov = len(covered)
        coverage = ncov / len(per_query)
        if ncov:
            err = sum(1 for q in covered if q["pred"] != q["true"]) / ncov
        else:
            err = 0.0
        # TS 미탐: 정답 TS인데 (OOD로 빠짐) or (커버됐는데 하위등급 예측)
        ts = [q for q in per_query if q["true"] == "TS"]
        ts_ood = sum(1 for q in ts if q["top1_sim"] < tau)
        ts_undercls = sum(1 for q in ts if q["top1_sim"] >= tau
                          and SEVERITY[q["pred"]] < SEVERITY["TS"])
        return {
            "tau": round(tau, 3),
            "coverage": round(coverage, 3),
            "ood_rate": round(1 - coverage, 3),
            "n_covered": ncov,
            "incov_error": round(err, 3),
            "incov_accuracy": round(1 - err, 3),
            "ts_total": len(ts),
            "ts_routed_ood": ts_ood,
            "ts_undercls_silent": ts_undercls,  # 무인미탐 위험: 커버 구간에서 TS→하위
        }

    taus = [round(x, 2) for x in np.arange(0.40, 0.91, 0.05)]
    sweep = [metrics_at(t) for t in taus]

    # baseline: 임계 없음(τ=0, 전량 자동확정)
    base = metrics_at(-1.0)

    # 목표 정확도 달성 최소 τ (그때의 자동화율)
    def first_reaching(acc_target: float):
        cand = [m for m in sweep if m["incov_accuracy"] >= acc_target and m["n_covered"] >= 10]
        if not cand:
            return None
        return min(cand, key=lambda m: m["tau"])  # 가장 낮은 τ = 최대 커버리지

    # ── 출력 ──────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("ES-only(골든셋 kNN) 분류 품질 — 자동화율 ↔ 오라벨율 트레이드오프")
    print("=" * 78)
    print(f"\n[전량 자동확정(임계 없음)] 정확도={base['incov_accuracy']:.1%}  "
          f"(오라벨율={base['incov_error']:.1%}, n={base['n_covered']})")
    print(f"  → TS {base['ts_total']}건 중 하위등급 오분류(무인미탐) {base['ts_undercls_silent']}건")

    print(f"\n{'τ(임계)':>7} {'자동화율':>8} {'OOD율':>7} {'커버n':>6} {'커버정확도':>9} {'오라벨율':>8} "
          f"{'TS→OOD':>7} {'TS미탐':>7}")
    print("-" * 78)
    for m in sweep:
        print(f"{m['tau']:>7.2f} {m['coverage']:>8.1%} {m['ood_rate']:>7.1%} {m['n_covered']:>6d} "
              f"{m['incov_accuracy']:>9.1%} {m['incov_error']:>8.1%} "
              f"{m['ts_routed_ood']:>4d}/{m['ts_total']:<2d} {m['ts_undercls_silent']:>7d}")

    print("\n[운영점 후보]")
    for tgt in (0.90, 0.95, 0.99):
        m = first_reaching(tgt)
        if m:
            print(f"  커버정확도 ≥ {tgt:.0%} : τ={m['tau']:.2f} 에서 자동화율 {m['coverage']:.1%} "
                  f"(검수로 {m['ood_rate']:.1%}), 커버 구간 TS미탐 {m['ts_undercls_silent']}건")
        else:
            print(f"  커버정확도 ≥ {tgt:.0%} : 달성 불가(어느 τ로도)")

    # 혼동행렬(전량)
    cm = defaultdict(lambda: defaultdict(int))
    for q in per_query:
        cm[q["true"]][q["pred"]] += 1
    print("\n[혼동행렬 — 전량 kNN 예측] 행=정답, 열=예측")
    print("        " + "".join(f"{g:>6}" for g in GRADES))
    for t in GRADES:
        print(f"  {t:>4}  " + "".join(f"{cm[t][p]:>6d}" for p in GRADES))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "design": {"reference_n": len(ref), "query_n": len(qry), "model": MODEL,
                   "knn_k": K, "backend": "in-process cosine (ES dense 정확판, BM25 제외)"},
        "top1_sim": {"min": float(top1s.min()), "p10": float(np.percentile(top1s, 10)),
                     "median": float(np.median(top1s)), "max": float(top1s.max())},
        "baseline_no_threshold": base,
        "sweep": sweep,
        "operating_points": {f"acc>={t}": first_reaching(t) for t in (0.90, 0.95, 0.99)},
        "confusion_matrix": {t: dict(cm[t]) for t in GRADES},
        "per_query": per_query,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n리포트 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
