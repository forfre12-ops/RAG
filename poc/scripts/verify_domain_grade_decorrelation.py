# -*- coding: utf-8 -*-
"""가설 검증: 등급·도메인을 분리해서 생성하면 guide40의 통계적 지름길이 줄어드는가.

배경(2026-10-01): guide40(64건)은 "부서·정보유형→등급을 표로 직접 배정"하는 방식이라
부서 어휘와 등급이 구조적으로 얽혔고, 사전 등록 기준(가족단위 교차검증이 무작위 섞기
95백분위 이내)을 통과하지 못했다(실측 100백분위).

이 스크립트는 반대 설계를 쓴다 — 제품 생성기(SyntheticDocGenerator)는 원래 등급
(GRADE_SITUATION_PROMPTS)과 도메인(DOMAIN_DOC_TYPES)이 독립된 축이다. 같은 도메인을
여러 등급에 고르게 흩어 생성하면(부서 하나가 한 등급에 몰리지 않으면), guide40 이
걸렸던 "부서 어휘=등급" 상관관계가 애초에 안 생길 수 있다는 가설을 검증한다.

API·사람 검수 없이, 로컬 Ollama 로 직접 생성기를 호출하고 같은 통계 감사(라벨 무작위
섞기 기준선)를 돌린다. guide40 과 규모를 맞춰(등급당 20건, 총 80건) 직접 비교한다.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
import time

_POC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_POC, "src"))
os.environ.setdefault("TESTING", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline

from koipa.adapters.llm.local_openai_provider import LocalOpenAIProvider  # noqa: E402
from koipa.modules.m1_synthesis.generator import (  # noqa: E402
    DOMAIN_DOC_TYPES,
    SyntheticDocGenerator,
    SynthRequest,
)
from koipa.services.synth_quality import (  # noqa: E402
    _document_quality_errors,
    _exposes_grade_token,
    _exposes_self_grade_declaration,
)

GRADES = ["TS", "S1", "S2", "S3"]
N_PER_GRADE = 20
# 사람이 쓰는 업무 도메인만 — "public"은 S3 전용 의미가 강해 뺀다(guide40의 '공개' 사례와
# 같은 정의상 쏠림을 피하려는 것).
DOMAINS = [
    "tech", "business", "finance", "hr", "legal", "mixed", "security", "ma",
    "semiconductor", "bio", "배터리", "반도체", "화학_제약", "바이오_농업",
    "소프트웨어", "경영정보", "기타",
]
assert set(DOMAINS) <= set(DOMAIN_DOC_TYPES), "DOMAINS 에 generator.DOMAIN_DOC_TYPES 에 없는 값이 있다"

OUT_DIR = os.path.join(_POC, "reports", "verify_domain_grade_decorrelation_20261001")


def build_requests(seed: int = 20261001) -> list[tuple[str, str]]:
    """등급마다 같은 도메인 집합을 다른 순서로 섞어 배정한다 — 등급-도메인 상관을 깬다."""
    rng = __import__("random").Random(seed)
    reqs: list[tuple[str, str]] = []
    for grade in GRADES:
        doms = DOMAINS.copy()
        rng.shuffle(doms)
        # 도메인 수(17)가 N_PER_GRADE(20)보다 적으면 처음 몇 개를 한 번 더 돈다.
        picked = (doms * ((N_PER_GRADE // len(doms)) + 1))[:N_PER_GRADE]
        reqs.extend((grade, d) for d in picked)
    return reqs


def generate(out_dir: str, model: str = "qwen3:14b") -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    llm = LocalOpenAIProvider(base_url="http://localhost:11434/v1", api_key="ollama", model=model, provider_label="ollama")
    gen = SyntheticDocGenerator(llm)
    reqs = build_requests()
    rows: list[dict] = []
    out_path = os.path.join(out_dir, "docs.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for i, (grade, domain) in enumerate(reqs):
            t0 = time.time()
            try:
                d = gen.generate(SynthRequest(target_grade=grade, domain=domain, count=1))[0]
                row = dict(
                    review_id=f"VD-{i:04d}", true_grade=grade, domain=domain,
                    title=d.title, body=d.body, doc_type=d.document_type,
                    parse_error=d.parse_error, label_source=d.label_source,
                    pii=d.pii_violations,
                )
            except Exception as exc:  # noqa: BLE001
                row = dict(review_id=f"VD-{i:04d}", true_grade=grade, domain=domain,
                           title="", body="", error=f"{type(exc).__name__}: {str(exc)[:160]}")
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[{i + 1}/{len(reqs)}] {grade}/{domain} {time.time() - t0:.0f}초 "
                  f"본문 {len(row.get('body', ''))}자", flush=True)
    return rows


def gate_check(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("body") and not r.get("parse_error") and not r.get("label_source")]
    grade_hits = [r["review_id"] for r in ok if _exposes_grade_token(r["title"] + "\n" + r["body"])]
    decl_hits = [r["review_id"] for r in ok if _exposes_self_grade_declaration(r["body"])]
    pii_hits = [r["review_id"] for r in ok if r.get("pii")]
    quality_flagged = [r["review_id"] for r in ok if _document_quality_errors(r["body"])]
    return dict(
        n_generated=len(rows), n_ok=len(ok),
        grade_token_exposed=grade_hits, self_grade_declaration=decl_hits,
        pii_detected=pii_hits, quality_flagged_count=len(quality_flagged),
    )


def _make_clf():
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1, max_features=4000)
    clf = LogisticRegression(max_iter=2000, class_weight="balanced")
    return make_pipeline(vec, clf)


def _cv_accuracy(texts, labels, seed: int, n_splits: int = 3) -> float:
    y = np.array(labels)
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    correct = total = 0
    for train_idx, test_idx in skf.split(texts, y):
        model = _make_clf()
        model.fit([texts[i] for i in train_idx], y[train_idx])
        pred = model.predict([texts[i] for i in test_idx])
        correct += int((pred == y[test_idx]).sum())
        total += len(test_idx)
    return correct / total


_WORD_RE = re.compile(r"[가-힣]{2,}")


def domain_only_predict(domains: list[str], grades: list[str]) -> float:
    """도메인만으로 등급을 맞히는 최빈값 분류기(라벨 누설 없이) — 가족단위 holdout 안 씀, 단순 참고."""
    by_domain = collections.defaultdict(collections.Counter)
    for d, g in zip(domains, grades):
        by_domain[d][g] += 1
    correct = 0
    for d, g in zip(domains, grades):
        majority = by_domain[d].most_common(1)[0][0]
        correct += int(majority == g)
    return correct / len(grades)


def audit(rows: list[dict], out_dir: str) -> dict:
    ok = [r for r in rows if r.get("body") and not r.get("parse_error") and not r.get("label_source")]
    texts = [r["title"] + "\n" + r["body"] for r in ok]
    grades = [r["true_grade"] for r in ok]
    domains = [r["domain"] for r in ok]

    n_repeats, n_perms = 30, 300
    real_accs = [_cv_accuracy(texts, grades, seed=s) for s in range(n_repeats)]
    real_mean = float(np.mean(real_accs))

    rng = np.random.default_rng(20261001)
    perm_accs = []
    for p in range(n_perms):
        shuffled = list(grades)
        rng.shuffle(shuffled)
        perm_accs.append(_cv_accuracy(texts, shuffled, seed=p))
    perm_arr = np.array(perm_accs)
    percentile = float((perm_arr < real_mean).mean())

    dom_acc = domain_only_predict(domains, grades)
    dom_counts = collections.defaultdict(collections.Counter)
    for d, g in zip(domains, grades):
        dom_counts[d][g] += 1
    max_domain_skew = max(
        (max(c.values()) / sum(c.values()) for c in dom_counts.values() if sum(c.values()) >= 2),
        default=0.0,
    )

    result = dict(
        n_docs=len(ok),
        class_counts=dict(collections.Counter(grades)),
        domain_counts_per_grade={g: dict(collections.Counter(
            domains[i] for i, gg in enumerate(grades) if gg == g)) for g in GRADES},
        ngram_classifier=dict(
            real_accuracy_mean=real_mean, real_accuracy_std=float(np.std(real_accs)),
            permutation_mean=float(perm_arr.mean()), permutation_max=float(perm_arr.max()),
            percentile_rank_of_real=percentile,
            verdict="통과" if percentile <= 0.95 else "미달(잔여 지름길 의심)",
        ),
        domain_majority_predict_accuracy=dom_acc,
        max_single_domain_grade_skew=max_domain_skew,
        guide40_comparison=dict(
            guide40_percentile=1.000, guide40_verdict="미달",
            note="guide40은 부서→등급 고정 매핑. 이 실험은 등급·도메인을 분리했다.",
        ),
    )
    out_path = os.path.join(out_dir, "audit_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main() -> None:
    print("=== 1단계: 생성(등급·도메인 분리 설계) ===", flush=True)
    rows = generate(OUT_DIR)

    print("\n=== 2단계: 규칙 게이트 ===", flush=True)
    gates = gate_check(rows)
    print(json.dumps({k: v for k, v in gates.items() if not isinstance(v, list)}, ensure_ascii=False, indent=1))
    for k in ("grade_token_exposed", "self_grade_declaration", "pii_detected"):
        if gates[k]:
            print(f"  {k}: {gates[k]}")

    print("\n=== 3단계: 통계적 지름길 감사(라벨 무작위 섞기 기준선) ===", flush=True)
    result = audit(rows, OUT_DIR)
    ng = result["ngram_classifier"]
    print(f"문서 {result['n_docs']}건 · 등급 분포 {result['class_counts']}")
    print(f"도메인-최빈등급 적중률(참고, 가족분리 아님): {result['domain_majority_predict_accuracy']:.3f}")
    print(f"단일 도메인 최대 등급 쏠림: {result['max_single_domain_grade_skew']:.3f}")
    print(f"[글자 n-gram] 실제 {ng['real_accuracy_mean']:.3f} · 무작위 섞기 평균 {ng['permutation_mean']:.3f}"
          f"(최댓값 {ng['permutation_max']:.3f}) · 백분위 {ng['percentile_rank_of_real']:.3f} · 판정 {ng['verdict']}")
    print(f"\n[guide40 대비] guide40 백분위 1.000(미달) vs 이번 {ng['percentile_rank_of_real']:.3f}({ng['verdict']})")
    print(f"\n결과 저장 -> {os.path.join(OUT_DIR, 'audit_result.json')}")
    print("VERIFY_DONE", flush=True)


if __name__ == "__main__":
    main()
