"""룰의 두 결함 — 범용 영문 약어 부스트 · 부정문/인용 — 을 고치기 전에 센다.

왜 필요한가(2026-09-29). 룰 진단에서 "명백한 결함 4개" 중 둘(겹치는 키워드 이중 계산 B2,
문서 길이 반영 B1)은 `measure_rule_variants.py` 가 이미 재서 **고치지 않기로** 결론 났다
(B2 1,481건 중 변경 0건 · B1 은 길이 중립 셋에서 미탐 증가). 나머지 둘은 잰 적이 없다.

    D1  `_HIGH_RISK_PATTERNS` 의 범용 영문 약어(API·license·draft·LTE·OECD …)가 매치마다
        점수를 얹는다. 공개 문서에도 흔한 낱말이라 저등급 문서를 끌어올릴 수 있다.
    D2  시드는 부분 문자열 매칭이라 "영업비밀이 아닙니다" 의 `영업비밀` 도 그대로 센다.

이 스크립트는 **배포 코드를 수정하지 않고** 다음을 센다.

    [약어] 토큰별로 어느 정답 등급 문서에서 뜨는가 + 그 토큰만 빼면 무엇이 바뀌는가
           (룰 등급 · FNR-safe 상향 발동). 상향은 모델보다 높을 때만 적용되므로 룰 단독으로는
           "이득/손실"을 확정하지 못한다 — 후보를 고르는 용도다. 확정은 서빙 경로 전후 비교.
    [부정] 좁은 서술형 부정("<시드>이 아닙니다")과 인용("'<시드>'라는 표현")이 실문서에 몇 건
           있는가, 그것을 세지 않으면 룰 등급이 어떻게 바뀌는가.

⚠ 평가셋은 길이·문체 지름길이 있다(holdout109 길이 1-NN 0.826). 절대 수치가 아니라
  **방향**만 인용한다. 정답은 기계 라벨이며 고객사 정확도가 아니다.

사용:
    PYTHONIOENCODING=utf-8 python scripts/measure_rule_acronym_negation.py [--out reports/x.json]
"""
from __future__ import annotations

try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from koipa.modules.m3_labeling import rule_engine as re_mod
from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine
from koipa.modules.m3_labeling.seeds import GRADE_ORDER, KEYWORD_SEEDS

try:
    from measure_rule_variants import DEFAULT_SETS, THRESHOLDS, load_set
except ImportError:  # 패키지로 import
    from scripts.measure_rule_variants import DEFAULT_SETS, THRESHOLDS, load_set

GRADES = ("TS", "S1", "S2", "S3")

# ── D2: 좁은 부정·인용 표현 ────────────────────────────────────────────────
# FNR-safe 방향이 최우선이다. "유출하지 않도록" · "침해가 없었다" 처럼 **비밀이 여전히
# 비밀인** 문장을 깎으면 미탐이 된다. 그래서 시드 **바로 뒤**에서 그 시드를 서술어로 부정하는
# 형태만 잡는다(조사 + 아니/아님/해당하지 않).
_PARTICLE = r"(?:이|가|은|는|도|으로|로|에|에는)?"
NEG_AFTER = re.compile(
    rf"^{_PARTICLE}\s*(?:(?:에\s*)?해당(?:되)?지\s*(?:않|아니)|해당\s*(?:안|없)|아니(?:다|며|고|라|므로|기)|아닙|아님|아닌)"
)
# 인용 = 시드를 따옴표로 감싸 낱말 자체를 언급하는 경우("'기밀'이라는 표현").
QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"), ("`", "`"))
QUOTE_AFTER = re.compile(r"^(?:이?라는|이?라고|이?란|라는)\s*(?:표현|용어|말|단어|문구|표시|글자)?")


def occurrences(text: str, kw: str):
    i = text.find(kw)
    while i != -1:
        yield i, i + len(kw)
        i = text.find(kw, i + 1)


def is_negated(text: str, end: int) -> bool:
    return bool(NEG_AFTER.match(text[end:end + 16]))


def is_quoted_mention(text: str, start: int, end: int) -> bool:
    if start == 0 or end >= len(text):
        return False
    for lq, rq in QUOTE_PAIRS:
        if text[start - 1] == lq and text[end] == rq and QUOTE_AFTER.match(text[end + 1:end + 14]):
            return True
    return False


# ── D1: 영문 약어 토큰 분해 ────────────────────────────────────────────────
def _top_level_split(body: str) -> list[str]:
    """중첩 그룹 안의 `|` 는 자르지 않는다(`customer\s*(?:list|database)`)."""
    out, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "|" and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def split_tokens(patterns):
    """`\\b(?:A|B|C)\\b` 형태를 토큰 목록으로. 그 밖의 형태는 통째로 한 토큰."""
    out = []
    for gi, (grade, pat, weight, factor) in enumerate(patterns):
        m = re.fullmatch(r"\\b\(\?:(.*)\)\\b", pat)
        toks = _top_level_split(m.group(1)) if m else [pat]
        for t in toks:
            out.append({"grade": grade, "token": t, "weight": weight, "factor": factor, "group": gi})
    return out


def patterns_without(patterns, drop: set[str]):
    """토큰 집합 drop 을 뺀 패턴 목록(교체용 사본)."""
    new = []
    for grade, pat, weight, factor in patterns:
        m = re.fullmatch(r"\\b\(\?:(.*)\)\\b", pat)
        if not m:
            if pat not in drop:
                new.append((grade, pat, weight, factor))
            continue
        keep = [t for t in _top_level_split(m.group(1)) if t not in drop]
        if keep:
            new.append((grade, r"\b(?:" + "|".join(keep) + r")\b", weight, factor))
    return new


def uplift_level(scores: dict[str, float]):
    for g in ("TS", "S1", "S2"):
        if scores.get(g, 0.0) >= THRESHOLDS[g]:
            return g
    return None


def rank(g):
    return GRADE_ORDER[g]


def label(engine, text):
    r = engine.label(text)
    return r.grade, uplift_level(r.grade_scores), r.grade_scores


def measure_acronyms(docs_by_set, engine):
    original = list(re_mod._HIGH_RISK_PATTERNS)
    tokens = split_tokens(original)
    all_docs = [(s, g, t) for s, docs in docs_by_set.items() for g, t in docs]

    # 토큰 매치 캐시
    rx = {tk["token"]: re.compile(r"\b(?:" + tk["token"] + r")\b" if not tk["token"].startswith("(?:") else tk["token"], re.I)
          for tk in tokens}
    base = {}
    rows = []
    for tk in tokens:
        name = tk["token"]
        hit_docs = [(s, g, t) for s, g, t in all_docs if rx[name].search(t)]
        by_gold = Counter(g for _, g, _ in hit_docs)
        boost = tk["grade"]
        over = sum(1 for _, g, _ in hit_docs if rank(g) > rank(boost))      # 정답이 부스트 등급보다 낮다
        exact = sum(1 for _, g, _ in hit_docs if g == boost)
        under = sum(1 for _, g, _ in hit_docs if rank(g) < rank(boost))     # 정답이 부스트보다 높다
        # 토큰만 빼고 룰을 다시 돌려 무엇이 바뀌는지
        content_changed = uplift_changed = 0
        content_gain = content_loss = 0
        removed_overshoot = removed_needed = 0
        if hit_docs:
            re_mod._HIGH_RISK_PATTERNS[:] = patterns_without(original, {name})
            for s, g, t in hit_docs:
                key = (s, hash(t))
                if key not in base:
                    re_mod._HIGH_RISK_PATTERNS[:] = original
                    base[key] = label(engine, t)
                    re_mod._HIGH_RISK_PATTERNS[:] = patterns_without(original, {name})
                b_grade, b_up, _ = base[key]
                a_grade, a_up, _ = label(engine, t)
                if a_grade != b_grade:
                    content_changed += 1
                    if a_grade == g and b_grade != g:
                        content_gain += 1
                    if b_grade == g and a_grade != g:
                        content_loss += 1
                if a_up != b_up:
                    uplift_changed += 1
                    # 상향이 정답보다 높은 곳에서 빠지면 과상향 제거, 정답 이하에서 빠지면 필요한 상향 상실 후보
                    if b_up and rank(b_up) < rank(g):
                        removed_overshoot += 1
                    elif b_up and rank(b_up) >= rank(g):
                        removed_needed += 1
            re_mod._HIGH_RISK_PATTERNS[:] = original
        rows.append({
            "token": name, "boost": boost, "weight": tk["weight"],
            "docs": len(hit_docs), "gold": {g: by_gold.get(g, 0) for g in GRADES},
            "over": over, "exact": exact, "under": under,
            "content_changed": content_changed, "content_gain": content_gain, "content_loss": content_loss,
            "uplift_changed": uplift_changed,
            "uplift_removed_overshoot": removed_overshoot, "uplift_removed_not_over": removed_needed,
        })
    re_mod._HIGH_RISK_PATTERNS[:] = original
    return rows


def measure_negation(docs_by_set):
    seeds = [(s["keyword"], s["grade"]) for s in KEYWORD_SEEDS]
    neg_total = quote_total = 0
    neg_docs, quote_docs = [], []
    per_seed = Counter()
    for sname, docs in docs_by_set.items():
        for gold, text in docs:
            n_neg = n_quote = 0
            for kw, sg in seeds:
                for a, b in occurrences(text, kw):
                    if is_negated(text, b):
                        n_neg += 1
                        per_seed[(kw, sg, "neg")] += 1
                        neg_docs.append((sname, gold, sg, kw, text[max(0, a - 14):b + 16].replace("\n", " ")))
                    elif is_quoted_mention(text, a, b):
                        n_quote += 1
                        per_seed[(kw, sg, "quote")] += 1
                        quote_docs.append((sname, gold, sg, kw, text[max(0, a - 14):b + 16].replace("\n", " ")))
            neg_total += n_neg
            quote_total += n_quote
    return {
        "neg_occurrences": neg_total, "quote_occurrences": quote_total,
        "neg_docs": neg_docs, "quote_docs": quote_docs, "per_seed": per_seed,
    }


class NegAwareEngine(LabelRuleEngine):
    """시험용: 부정·인용으로 걸린 시드 매치는 세지 않는다(배포 코드 무수정)."""

    def _count(self, text, kw, pattern_type, *, query_vec=None):
        if pattern_type != "exact" or not kw:
            return super()._count(text, kw, pattern_type, query_vec=query_vec)
        n = 0
        for a, b in occurrences(text, kw):
            if is_negated(text, b) or is_quoted_mention(text, a, b):
                continue
            n += 1
        return n


def load_unlabeled(path: str) -> list[str]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            t = row.get("text") or row.get("content") or ""
            if isinstance(t, str) and t.strip():
                out.append(t)
    return out


def blast_radius(texts: list[str], engine, drop: set[str]) -> dict:
    """정답이 없는 문서에서 토큰 제거·부정 미집계가 몇 건의 룰 출력을 바꾸는가."""
    original = list(re_mod._HIGH_RISK_PATTERNS)
    aware = NegAwareEngine(seeds=KEYWORD_SEEDS)
    changed_tok = changed_up = changed_neg = 0
    neg_hits = 0
    per_tok = Counter()
    for t in texts:
        b_grade, b_up, _ = label(engine, t)
        for kw, _g in ((s["keyword"], s["grade"]) for s in KEYWORD_SEEDS):
            for a, e in occurrences(t, kw):
                if is_negated(t, e) or is_quoted_mention(t, a, e):
                    neg_hits += 1
        if aware.label(t).grade != b_grade:
            changed_neg += 1
        if drop:
            re_mod._HIGH_RISK_PATTERNS[:] = patterns_without(original, drop)
            a_grade, a_up, _ = label(engine, t)
            re_mod._HIGH_RISK_PATTERNS[:] = original
            if a_grade != b_grade:
                changed_tok += 1
            if a_up != b_up:
                changed_up += 1
            for tok in drop:
                if re.search(r"\b(?:" + tok + r")\b", t, re.I):
                    per_tok[tok] += 1
    re_mod._HIGH_RISK_PATTERNS[:] = original
    return {"docs": len(texts), "neg_or_quote_matches": neg_hits, "neg_changes_grade": changed_neg,
            "drop_changes_grade": changed_tok, "drop_changes_uplift": changed_up, "drop_token_docs": dict(per_tok)}


def main() -> int:
    ap = argparse.ArgumentParser(description="룰 결함 D1(영문 약어)·D2(부정·인용) 측정")
    ap.add_argument("--unlabeled", action="append", default=[],
                    help="정답 없는 jsonl(text 필드). 영향 범위만 센다")
    ap.add_argument("--drop", default="", help="빼는 후보 토큰(쉼표). 정답 없는 문서 영향 범위 계수용")
    ap.add_argument("--set", dest="sets", action="append", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-docs", type=int, default=1, help="이 수 미만으로 뜨는 토큰은 표에서 뺀다")
    args = ap.parse_args()

    paths = args.sets or DEFAULT_SETS
    docs_by_set = {}
    for p in paths:
        if not Path(p).exists():
            print(f"  (없음) {p}")
            continue
        d = load_set(p)
        if d:
            docs_by_set[p] = d
    seen: set[str] = set()
    dup = 0
    for p, docs in list(docs_by_set.items()):
        uniq = []
        for g, t in docs:
            if t in seen:
                dup += 1
                continue
            seen.add(t)
            uniq.append((g, t))
        docs_by_set[p] = uniq
    total = sum(len(v) for v in docs_by_set.values())
    print(f"같은 본문이 두 셋에 있어 뺀 것 {dup}건")
    print(f"측정 대상 {total}건: " + " · ".join(f"{Path(p).name}={len(v)}" for p, v in docs_by_set.items()))

    engine = LabelRuleEngine(seeds=KEYWORD_SEEDS)

    print("\n" + "=" * 96 + "\n[D1] 영문 약어 토큰별 — 어느 정답 등급 문서에서 뜨나\n" + "=" * 96)
    rows = measure_acronyms(docs_by_set, engine)
    print(f"{'토큰':<16}{'부스트':<6}{'가중':<5}{'문서':>5}  {'정답분포 TS/S1/S2/S3':<22}{'과':>4}{'일치':>5}{'하':>4}"
          f"  {'룰등급변경(이득/손실)':<20}{'상향변경(과상향제거/그외)'}")
    for r in sorted(rows, key=lambda r: (-r["over"], -r["docs"])):
        if r["docs"] < args.min_docs:
            continue
        gd = "/".join(str(r["gold"][g]) for g in GRADES)
        print(f"{r['token']:<16}{r['boost']:<6}{r['weight']:<5}{r['docs']:>5}  {gd:<22}{r['over']:>4}{r['exact']:>5}{r['under']:>4}"
              f"  {r['content_changed']:>3}({r['content_gain']}/{r['content_loss']}){'':<9}"
              f"{r['uplift_changed']:>3}({r['uplift_removed_overshoot']}/{r['uplift_removed_not_over']})")
    zero = [r["token"] for r in rows if r["docs"] == 0]
    print(f"\n평가셋 {total}건에서 한 번도 안 뜬 토큰 {len(zero)}개 / 전체 {len(rows)}개 (근거 없음 ≠ 무해; 효과를 못 잰 것)")

    print("\n" + "=" * 96 + "\n[D2] 부정문·인용 — 실문서에서 몇 건 나오나\n" + "=" * 96)
    neg = measure_negation(docs_by_set)
    print(f"서술형 부정 매치 {neg['neg_occurrences']}건 · 인용 언급 매치 {neg['quote_occurrences']}건 (시드 404개 × 문서 {total}건 전수)")
    by = Counter((sn, g) for sn, g, *_ in neg["neg_docs"])
    print("부정 매치 문서의 정답등급:", dict(Counter(g for _, g, *_ in neg["neg_docs"])))
    for sn, gold, sg, kw, ctx in neg["neg_docs"][:12]:
        print(f"   [{Path(sn).stem[:22]} 정답{gold} 시드{sg}] …{ctx}…")
    for sn, gold, sg, kw, ctx in neg["quote_docs"][:8]:
        print(f"   [인용 {Path(sn).stem[:22]} 정답{gold} 시드{sg}] …{ctx}…")

    print("\n[D2] 세지 않았을 때 룰 등급 변화 (룰 단독)")
    aware = NegAwareEngine(seeds=KEYWORD_SEEDS)
    chg = Counter()
    detail = []
    for sname, docs in docs_by_set.items():
        for gold, text in docs:
            b = engine.label(text)
            a = aware.label(text)
            if a.grade != b.grade or uplift_level(a.grade_scores) != uplift_level(b.grade_scores):
                chg[(sname, "grade" if a.grade != b.grade else "uplift")] += 1
                detail.append((sname, gold, b.grade, a.grade))
    print("  변경 문서:", sum(chg.values()), dict(chg) if chg else "0건")
    for sname, gold, b, a in detail[:20]:
        print(f"   {Path(sname).stem[:24]} 정답{gold}: {b} -> {a}")

    for up in args.unlabeled:
        if not Path(up).exists():
            print(f"  (없음) {up}")
            continue
        drop = {t for t in args.drop.split(",") if t}
        br = blast_radius(load_unlabeled(up), engine, drop)
        print(f"\n[영향 범위 — 정답 없음] {up}\n  {br}")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "sets": {p: len(v) for p, v in docs_by_set.items()},
            "acronyms": rows,
            "negation": {k: v for k, v in neg.items() if k in ("neg_occurrences", "quote_occurrences")},
            "negation_changed_docs": detail,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n보고서 기록: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
