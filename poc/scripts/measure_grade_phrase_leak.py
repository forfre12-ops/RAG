#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""본문에 되풀이되는 문장이 등급을 알려주는가 — 흔적을 **알고 있지 않아도** 찾는다.

왜 이 도구가 있는가(2026-09-12). 골든 후보 1,055건에서 등급별 고정 문구 하나가
85.3% 를 맞히는 것을 찾았는데, 찾은 방법이 **생성기 코드를 읽고 그 문구를 알아낸 뒤
세는 것**이었다. 그 방식으로는 다음에 다른 형태의 흔적이 생기면 못 잡는다.
이 도구는 문구를 미리 알지 못해도 찾는다 — 여러 문서에 되풀이되는 문장을 모아
등급 쏠림을 재고, 쏠린 것부터 보여 준다.

⚠ 앞선 세척(`clean_candidate_answer_leak.py`)은 "## 등급 제안 사유" 절을 걷었다.
  그 세척을 통과한 판에서도 이 도구는 흔적을 찾는다 — 세척은 아는 형태만 지운다.
⚠ 제목 누출은 `measure_title_grade_leak.py` 가 잰다(본문 첫 줄 기준). 이 도구는
  **본문 안쪽 문장**을 본다. 두 도구를 합치지 않는다 — 재는 대상이 다르다.

재는 방법:
    ① 본문을 문장으로 자른다(줄바꿈·마침표 기준, 너무 짧은 것은 버린다)
    ② 여러 문서에 되풀이되는 문장만 남긴다(--min-docs)
    ③ 문장마다 등급 분포를 보고 '한 등급 비중'(purity)을 잰다
    ④ 문장을 단서로 삼아 최빈 등급을 찍었을 때의 전체 적중률을 낸다
       — 이 값이 기준선(가장 흔한 등급 비율)보다 크게 높으면 흔적이다

사용:
    python scripts/measure_grade_phrase_leak.py
    python scripts/measure_grade_phrase_leak.py --min-docs 10 --top 15
    python scripts/measure_grade_phrase_leak.py --json reports/GRADE_PHRASE_LEAK.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

from _cli_io import force_utf8_stdio
from classification_audit_inputs import DEFAULT_POOL, load_audit_pool, verify_audit_snapshot
from evaluation_inputs import sha256

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

# ⚠ 쉼표에서도 자른다. 처음에는 마침표·줄바꿈만으로 잘랐는데, 흔적이 **문장 안에 박혀**
#   있고(예: "최초 제기된 문제는 <고정 문구>이다, …") 그 문장 전체는 주제어가 문서마다 달라
#   유일해진다 — 되풀이 문장으로 안 잡혔다. 실제로 첫 판은 쏠림 0개로 나왔다(2026-09-12).
_SPLIT = re.compile(r"[\n。.!?,，、]+")
_WS = re.compile(r"\s+")


def sentences(text: str, min_chars: int) -> set[str]:
    """문서 한 건의 문장 집합. 같은 문장이 한 문서에 여러 번 나와도 한 번으로 센다."""
    out = set()
    for raw in _SPLIT.split(text or ""):
        s = _WS.sub(" ", raw).strip(" -·*#|")
        if len(s) >= min_chars:
            out.add(s)
    return out


def _null_single_clue(cand: list[dict], per_doc: list[set[str]], min_docs: int,
                      *, trials: int = 20, seed: int = 7) -> float:
    """라벨을 섞고 같은 자로 잰 '단서 하나로 찍기' 적중률(0~1).

    회차를 20 으로 둔다 — 문장 집합이 커서 한 회차가 비싸고, 값이 안정적이다
    (후보 1,055건에서 20회 범위 29.5~31.6%).
    """
    rng = random.Random(seed)
    labels = [c["label"] for c in cand]
    n = len(cand)
    total = 0.0
    for _ in range(trials):
        rng.shuffle(labels)
        by_sentence: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for sents, label in zip(per_doc, labels):
            for s in sents:
                by_sentence[s][label] += 1
        best: dict[str, tuple[float, int, str, str]] = {}
        for s, dist in by_sentence.items():
            docs = sum(dist.values())
            if docs < min_docs:
                continue
            grade = max(dist, key=lambda g: (dist[g], g))
            best[s] = (dist[grade] / docs, docs, grade, s)
        hit = 0
        for sents, label in zip(per_doc, labels):
            picks = [best[s] for s in sents if s in best]
            if picks and max(picks)[2] == label:
                hit += 1
        total += hit / n
    return total / trials


def audit(*, min_docs: int, min_chars: int, pool: Path | None = None,
          split: str = "development", view: str = "body_only", cv_seeds: int = 0) -> dict:
    if min_docs < 2 or min_chars < 1 or not 0 <= cv_seeds <= 100:
        raise ValueError("min-docs >= 2, min-chars >= 1, cv-seeds in 0..100 required")
    cand, input_audit = load_audit_pool(pool, split=split, view=view)
    n = len(cand)
    # 문장 집합은 한 번만 자른다 — 라벨 섞기에서 수십 번 다시 자르면 몇 분이 걸린다.
    per_doc_sentences = [sentences(c["text"], min_chars) for c in cand]
    by_sentence: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for c, sents in zip(cand, per_doc_sentences):
        for s in sents:
            by_sentence[s][c["label"]] += 1

    grades = collections.Counter(c["label"] for c in cand)
    base = max(grades.values()) / n if n else 0.0

    rows = []
    for s, dist in by_sentence.items():
        docs = sum(dist.values())
        if docs < min_docs:
            continue
        top_grade = max(dist, key=lambda g: (dist[g], g))
        top_n = dist[top_grade]
        rows.append({
            "sentence": s,
            "docs": docs,
            "purity": top_n / docs,
            "top_grade": top_grade,
            "grades": dict(dist),
            "exclusive": len(dist) == 1,
        })
    rows.sort(key=lambda r: (-r["purity"], -r["docs"], r["sentence"]))

    # 단서 하나로 등급을 찍으면 몇 %를 맞히는가 — 문서마다 **가장 쏠린 문장**을 단서로 쓴다.
    hit = covered = 0
    best = {r["sentence"]: r for r in rows}
    for c in cand:
        cand_rows = [best[s] for s in sentences(c["text"], min_chars) if s in best]
        if not cand_rows:
            continue
        covered += 1
        pick = max(cand_rows, key=lambda r: (r["purity"], r["docs"], r["top_grade"], r["sentence"]))
        hit += (pick["top_grade"] == c["label"])

    # ⭐ 진짜 기준선은 **라벨을 섞었을 때** 같은 자가 내는 값이다. 이 추정량은 문서마다
    #   후보 문장 중 **가장 쏠린 것**을 고르므로, 관계가 없어도 커질 수 있다.
    #   [[permutation-baseline-not-majority-2026-09-12]]
    #   ⚠ 실측(후보 1,055건): 실측 91.4% · 섞으면 30.5% → 차이 +60.9%p. 여기서는 부풀림이
    #     아니라 진짜 누설이었다. 그래도 값은 항상 **차이**로 읽는다.
    null = _null_single_clue(cand, per_doc_sentences, min_docs) if n else 0.0

    result = {
        "status": "MEASURED_NOT_QUALIFIED",
        "input_audit": input_audit,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version,
        "char_cv_executed": bool(cv_seeds),
        "phrase_permutation": {"trials": 20, "seed": 7},
        "source_sha256": {name: sha256(Path(__file__).with_name(name)) for name in
                          ("measure_grade_phrase_leak.py", "classification_audit_inputs.py",
                           "measure_ngram_shortcuts.py", "evaluation_inputs.py")},
        "n_candidates": n,
        "grade_distribution": dict(grades),
        "baseline_majority": round(base, 4),
        "null_single_clue": round(null, 4),
        "excess_pp": round((hit / n - null) * 100, 1) if n else 0.0,
        "min_docs": min_docs,
        "min_chars": min_chars,
        "n_repeated_sentences": len(rows),
        "n_exclusive_sentences": sum(1 for r in rows if r["exclusive"]),
        "covered_docs": covered,
        "single_clue_hits": hit,
        "single_clue_accuracy": round(hit / n, 4) if n else 0.0,
        "top": rows[:50],
    }
    if cv_seeds:
        from measure_ngram_shortcuts import measure
        result["char_ngram_probe"] = measure(cand, seeds=cv_seeds)
    verify_audit_snapshot(input_audit)
    result["input_snapshot_verified_after_measurement"] = True
    return result


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="되풀이 문장의 등급 쏠림 계수")
    ap.add_argument("--min-docs", type=int, default=20, help="이 수 이상 문서에 나오는 문장만 본다")
    ap.add_argument("--min-chars", type=int, default=12, help="이보다 짧은 문장은 버린다")
    ap.add_argument("--top", type=int, default=10, help="화면에 보일 상위 문장 수")
    ap.add_argument("--json", default="")
    ap.add_argument("--pool", default="", help="다른 후보 풀 디렉터리(기본: 골든 후보 풀)")
    ap.add_argument("--split", choices=("development", "sealed_candidate", "all"), default="development",
                    help="분할 풀의 기본값은 development. 봉인 분할은 명시적으로만 읽는다")
    ap.add_argument("--view", choices=("body_only", "body_plus_synthetic_context"), default="body_only")
    ap.add_argument("--cv-seeds", type=int, default=0, help="문자 n-gram/계열 CV 반복 수(0=미실행)")
    ap.add_argument("--strict", action="store_true", help="지름길 경고 또는 문자/계열 CV 미실행이면 종료 3")
    ap.add_argument("--max-excess-pp", type=float, default=20.0, help="문장 단서 경고 임계; 사업 합격선 아님")
    ap.add_argument("--show-phrases", action="store_true", help="원문 구절을 출력/기록(기본은 해시만)")
    a = ap.parse_args(argv)

    try:
        if a.top < 0 or not 0 <= a.max_excess_pp <= 100:
            raise ValueError("top must be >= 0 and max-excess-pp must be in 0..100")
        out = (_POC / a.json).resolve() if a.json else None
        pool = Path(a.pool).resolve() if a.pool else DEFAULT_POOL.resolve()
        if out and (out.exists() or out.is_relative_to(pool)):
            raise ValueError("Report must be new and outside the input pool")
        r = audit(min_docs=a.min_docs, min_chars=a.min_chars, pool=pool,
                  split=a.split, view=a.view, cv_seeds=a.cv_seeds)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"검사 실패: {exc}", file=sys.stderr)
        return 2
    warnings, incomplete = [], []
    if r["excess_pp"] > a.max_excess_pp:
        warnings.append("repeated_phrase_shortcut")
    if a.cv_seeds:
        for name in ("stratified_cv", "family_cv"):
            probe = r["char_ngram_probe"][name]
            if probe["status"] != "MEASURED":
                incomplete.append(name + "_unavailable")
            elif probe["mean"] >= 0.8 and probe["excess_pp"] >= 30:
                warnings.append(name + "_shortcut_signal")
    else:
        incomplete.append("char_and_family_cv_not_executed")
    r["warnings"] = warnings
    r["incomplete_checks"] = incomplete
    r["strict_diagnostic_gate_passed"] = not warnings and not incomplete
    r["engineering_alarm_thresholds"] = {"phrase_excess_pp": a.max_excess_pp,
                                          "cv_mean": 0.8, "cv_excess_pp": 30,
                                          "business_acceptance_criteria": False}
    r["status"] = "SHORTCUT_WARNING" if warnings else ("INCOMPLETE_CHECKS" if incomplete else "MEASURED_NOT_QUALIFIED")
    if not a.show_phrases:
        r["top"] = [{k: v for k, v in row.items() if k != "sentence"} |
                    {"sentence_sha256": hashlib.sha256(row["sentence"].encode()).hexdigest()}
                    for row in r["top"]]
    if a.pool:
        print("풀: %s" % a.pool)
    print("입력 %d건 · 검토/미라벨 제외 %d건 · 관점 %s" %
          (r["input_audit"]["n_input_rows"], r["input_audit"]["excluded_review_rows"], a.view))
    print("분모: 후보 %d건 · 등급 분포 %s" % (r["n_candidates"], r["grade_distribution"]))
    print("되풀이 문장 %d개(%d개 문서 이상) · 그중 한 등급 전용 %d개"
          % (r["n_repeated_sentences"], r["min_docs"], r["n_exclusive_sentences"]))
    print("단서 하나로 등급 찍기: %d/%d = %.1f%%"
          % (r["single_clue_hits"], r["n_candidates"], r["single_clue_accuracy"] * 100))
    print("  ⭐라벨을 섞었을 때 %.1f%% → **차이 %+.1f%%p** (이 값으로 판단한다)"
          % (r["null_single_clue"] * 100, r["excess_pp"]))
    print("  (참고) 가장 흔한 등급만 찍기 %.1f%% — 범주 수·최댓값 고르기에 부푼다"
          % (r["baseline_majority"] * 100))
    print("\n쏠린 문장 상위 %d개:" % a.top)
    for row in r["top"][:a.top]:
        clue = row.get("sentence", row.get("sentence_sha256", ""))
        print("  %5.1f%% %4d건 %-4s %s" % (row["purity"] * 100, row["docs"], row["top_grade"], clue[:80]))
    print("상태: %s · 고객사 정확도/학습 자격을 입증하지 않음" % r["status"])
    if a.cv_seeds:
        for name in ("stratified_cv", "family_cv"):
            probe = r["char_ngram_probe"][name]
            if probe["status"] == "MEASURED":
                print("  %s: %d seeds · 평균 %.2f%% · permutation %.2f%%" %
                      (name, a.cv_seeds, probe["mean"] * 100, probe["permutation_mean"] * 100))
            else:
                print("  %s: %s" % (name, probe["status"]))
    else:
        print("  문자 n-gram/계열 CV는 미실행(--cv-seeds로 지정)")
    if a.json:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(r, ensure_ascii=False, indent=1) + "\n")
        print("\n기록: %s" % out)
    return 3 if a.strict and (warnings or incomplete) else 0


if __name__ == "__main__":
    raise SystemExit(main())
