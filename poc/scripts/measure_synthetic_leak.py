# -*- coding: utf-8 -*-
"""합성 1000건 코퍼스 — 학습 전에 반드시 도는 검증. 문제 있으면 숫자로 보여준다.

사용:
    PYTHONIOENCODING=utf-8 python measure_synthetic_leak.py <corpus.jsonl>

corpus.jsonl 각 줄: {"doc_id","text","grade","char_len_band","format","domain",...}
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def check_length_balance(rows: list[dict]) -> dict:
    """등급별 글자수 분포 — 등급 간 median 차이가 크면 길이가 새는 신호다."""
    by_grade = defaultdict(list)
    for r in rows:
        by_grade[r["grade"]].append(len(r.get("text", "")))
    out = {}
    for g, lens in by_grade.items():
        lens.sort()
        n = len(lens)
        out[g] = {
            "n": n, "min": lens[0], "max": lens[-1],
            "median": lens[n // 2],
            "mean": round(sum(lens) / n, 1),
        }
    medians = [v["median"] for v in out.values()]
    spread = max(medians) - min(medians) if medians else 0
    return {"by_grade": out, "median_spread": spread,
            "verdict": "위험 — 등급 간 길이차 큼" if spread > 300 else "양호"}


# 가이드 11쪽 정의 문구·패러프레이즈 — 이게 본문에 남아 있으면 "선언 금지" 지시를 어긴 것이다.
# 1차 점검(2026-09-16, 350건 부분표본)에서 "기밀|비밀|대외비|극비" 단순매칭이 "비밀번호"·
# "비밀유지약정"(NDA) 같은 정상 업무용어를 오탐한 것을 확인했다 — 자기선언 문맥만 남긴다.
_LEAK_PATTERNS = [
    r"승인된\s*자", r"업무상\s*필요한\s*자", r"임직원이라면\s*모두",
    r"경제적\s*가치가?\s*(없|있)", r"상당한\s*비용", r"비용\s*또는\s*노력",
    r"불특정\s*다수", r"통상적인\s*방법으로", r"보유자를\s*통하지\s*않으면",
    r"비밀로\s*관리", r"공개하는\s*정보",
    r"\b(TS|S1|S2|S3)\b",
    r"(이|본)\s*(문서|자료|정보)(는|은|의)?\s*(기밀|비밀|대외비|극비)",  # 자기선언 문맥만
    r"(기밀|비밀|대외비|극비)\s*(등급|문서|자료|정보)(입니다|이다|로\s*분류)",
]
_LEAK_RE = [re.compile(p) for p in _LEAK_PATTERNS]


def check_declaration_leak(rows: list[dict]) -> dict:
    hits_by_grade = defaultdict(Counter)
    doc_hit_count = 0
    for r in rows:
        text = r.get("text", "")
        grade = r.get("grade", "?")
        hit_any = False
        for pat, rex in zip(_LEAK_PATTERNS, _LEAK_RE):
            if rex.search(text):
                hits_by_grade[grade][pat] += 1
                hit_any = True
        if hit_any:
            doc_hit_count += 1
    return {
        "docs_with_any_leak_phrase": doc_hit_count,
        "total": len(rows),
        "rate": round(doc_hit_count / len(rows), 4) if rows else None,
        "by_grade_by_pattern": {g: dict(c) for g, c in hits_by_grade.items()},
        "verdict": "위험 — 선언 문구가 새고 있다" if doc_hit_count / max(len(rows), 1) > 0.02 else "양호",
    }


def check_shortcut_classifier(rows: list[dict]) -> dict:
    """본문만 보고 등급을 얼마나 맞히는지 — char n-gram + LinearSVC 5-fold. 기준선(무작위)=25%."""
    try:
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.model_selection import StratifiedKFold, cross_val_score
        from sklearn.svm import LinearSVC
    except ImportError as exc:
        return {"skipped": True, "reason": f"sklearn 없음: {exc}"}

    texts = [r.get("text", "") for r in rows]
    grades = [r.get("grade", "?") for r in rows]
    vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=2, max_features=20000)
    X = vec.fit_transform(texts)
    y = np.array(grades)
    clf = LinearSVC(max_iter=5000)
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    scores = cross_val_score(clf, X, y, cv=skf)
    acc = float(scores.mean())
    return {
        "cv_accuracy": round(acc, 4), "baseline_random": 0.25,
        "excess_over_baseline_pp": round((acc - 0.25) * 100, 1),
        "verdict": ("위험 — 본문만으로 등급이 높게 새고 있다" if acc > 0.45
                    else "경계 — 어느 정도 신호가 있다" if acc > 0.35
                    else "양호 — 기준선에 가깝다"),
    }


def check_near_duplicates(rows: list[dict], threshold: float = 0.85) -> dict:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as exc:
        return {"skipped": True, "reason": f"sklearn 없음: {exc}"}

    texts = [r.get("text", "") for r in rows]
    vec = TfidfVectorizer(analyzer="char", ngram_range=(3, 5), min_df=1)
    X = vec.fit_transform(texts)
    sim = cosine_similarity(X)
    n = len(rows)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= threshold:
                pairs.append((rows[i].get("doc_id", i), rows[j].get("doc_id", j), round(float(sim[i, j]), 3)))
    return {
        "near_dup_pairs": len(pairs), "threshold": threshold,
        "examples": pairs[:10],
        "verdict": "위험 — 근접 중복이 있다" if len(pairs) > n * 0.01 else "양호",
    }


def check_format_style_balance(rows: list[dict]) -> dict:
    """형식(format)·길이밴드가 등급별로 고르게 섞였는지."""
    cross = defaultdict(Counter)
    for r in rows:
        cross[r.get("grade", "?")][r.get("format", "?")] += 1
        cross[r.get("grade", "?")][r.get("char_len_band", "?")] += 1
    return {g: dict(c) for g, c in cross.items()}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: measure_synthetic_leak.py <corpus.jsonl>")
        return 1
    path = Path(sys.argv[1])
    rows = load(path)
    print(f"로드: {len(rows)}건 · {path}")

    checks = {
        "length_balance": check_length_balance(rows),
        "declaration_leak": check_declaration_leak(rows),
        "shortcut_classifier": check_shortcut_classifier(rows),
        "near_duplicates": check_near_duplicates(rows),
        "format_style_by_grade": check_format_style_balance(rows),
    }

    print("\n" + "=" * 78)
    for name, result in checks.items():
        print(f"\n[{name}]")
        print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])

    out_path = path.with_suffix(".leakcheck.json")
    out_path.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {out_path}")

    verdicts = []
    for k in ("length_balance", "declaration_leak", "shortcut_classifier", "near_duplicates"):
        v = checks[k].get("verdict")
        if v:
            verdicts.append(f"{k}: {v}")
    print("\n" + "\n".join(verdicts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
