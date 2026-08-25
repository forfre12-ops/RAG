"""룰 연산부를 고치기 전에 "고치면 무엇이 바뀌는지"를 먼저 센다.

왜 필요한가(실측 2026-08-26). 룰 감사에서 연산부 결함 셋을 찾았다.

    B2  exact 매칭이 부분 문자열이라 짧은 시드가 긴 시드 안에서 딸려 매칭된다
        ("기밀" 이 "특급기밀" 안에서 같이 걸려 TS·S1 양쪽에 점수가 들어간다)
    C   정본 §4.5 는 S1 을 s=2·v=2·m=0 으로 정의하는데, 코드는 관리성 시드가 뜨면
        m=2 를 줘서 (2,2,2)=TS 로 밀어낸다
    B1  FNR-safe 상향 임계가 **정규화되지 않은 누산 점수**와 비교된다
        (같은 문장을 3회 반복하면 내용 밀도가 같아도 임계를 넘는다)

셋 다 판정면을 움직이므로 고치고 나서 재면 늦다. 이 스크립트는 배포 코드를 **수정하지
않고** 같은 입력에 현행 로직과 변형 로직을 나란히 태워 델타만 낸다.

⚠ 평가셋 선택이 결론을 뒤집는다 — 이것이 이 스크립트의 존재 이유다.

`holdout109` 는 600자 미만 41건에 S3 가 **0건**, 600자 이상 68건에 S3 가 **67건**이다
(길이만으로 1-NN 0.826 · Theil's U 0.611 > 권고 상한 0.55). 길이를 벌하는 어떤 변경이든
이 셋에서는 좋아 보인다. 실제로 B1(길이 정규화)은 holdout109 에서 "S3 과분류 7건 제거"로
보였지만, 길이 중립 셋(`labeled_v7_diverse/val` 1-NN 0.246)에서 다시 재니 방향이 뒤집혀
`labeled_oss_v1/test` 에서 **TS 15건이 상향에서 빠져 미탐이 됐다.**

그래서 이 스크립트는 순서를 강제한다.
    1) 셋의 길이 교락을 먼저 재서 출력한다(koipa.dataset_leakage.audit)
    2) 재현 로직이 실제 LabelRuleEngine.label() 과 같은 등급을 내는지 검증한다
       — 불일치가 1건이라도 있으면 그 셋의 델타는 신뢰할 수 없다
    3) 그 다음에야 변형 델타를 낸다

측정 결과(2026-08-26, 1,481건): B2 변경 0 · C 변경 0 · B1 은 중립 셋에서 미탐 증가.
셋 다 고치지 않기로 했다. 시드나 임계를 다시 건드릴 때 같은 명령으로 재현하면 된다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from koipa.dataset_leakage import audit
from koipa.holdout_independence import RECOMMENDED_MAX_LENGTH_LEAK
from koipa.modules.m3_labeling.rule_engine import (
    LabelRuleEngine,
    _apply_high_risk_overrides,
    grade_from_svm,
)
from koipa.modules.m3_labeling.seeds import (
    GRADE_ORDER,
    KEYWORD_SEEDS,
    to_canonical_factor,
)

# pipeline.py 의 FNR-safe 상향 임계 기본값(config.fnr_rule_*_threshold).
# 여기서 다시 적는 이유는 이 스크립트가 **임계 형태 자체**를 비교하기 때문이다.
THRESHOLDS = {"TS": 3.0, "S1": 2.2, "S2": 1.6}

# 기본 측정면. 길이 교락이 없는 것부터 둔다 — 교락 셋만 보고 판단하면 뒤집힌다.
DEFAULT_SETS = [
    "datasets/labeled_v7_diverse/val.jsonl",
    "datasets/labeled_oss_v1/test.jsonl",
    "datasets/proxy_eval/direct_authored_proxy_eval_split.v3/development_200.jsonl",
    "datasets/gold_real/holdout_eval.hardened.jsonl",
    "datasets/gold_real/_rejudge_claude/holdout109_provenance_corrected.jsonl",
]

GRADES = ("TS", "S1", "S2", "S3")

# 짧은 시드가 어느 긴 시드 안에 몇 번 들어있는가. B2 변형이 뺄 몫이다.
_CONTAINMENT: dict[str, list[tuple[str, int]]] = {}
for _a in KEYWORD_SEEDS:
    _inner = [
        (_b["keyword"], _b["keyword"].count(_a["keyword"]))
        for _b in KEYWORD_SEEDS
        if _b["keyword"] != _a["keyword"] and _a["keyword"] in _b["keyword"]
    ]
    if _inner:
        _CONTAINMENT[_a["keyword"]] = _inner


def load_set(path: str) -> list[tuple[str, str]]:
    """(등급, 본문) 목록. 등급 키·본문 키는 셋마다 달라 알려진 이름을 모두 본다."""
    out: list[tuple[str, str]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        grade = row.get("grade") or row.get("label") or row.get("gold_grade")
        text = row.get("content") or row.get("text") or row.get("body") or ""
        if grade in GRADES and isinstance(text, str) and text.strip():
            out.append((grade, text))
    return out


def grade_scores(text: str, *, suppress_containment: bool):
    """등급별 누산 점수 + 매치 목록. suppress_containment 가 B2 변형이다.

    억제 방식: `text.count(짧은시드)` 의 과계수분은 정확히
    `sum(count(긴시드) * 긴시드.count(짧은시드))` 다. 그만큼 뺀다.
    """
    counts = {s["keyword"]: text.count(s["keyword"]) for s in KEYWORD_SEEDS}
    if suppress_containment:
        counts = {
            kw: max(0, n - sum(counts[long] * k for long, k in _CONTAINMENT.get(kw, [])))
            for kw, n in counts.items()
        }
    scores = {g: 0.0 for g in GRADE_ORDER}
    matches: list[tuple[str, str, str | None]] = []
    for seed in KEYWORD_SEEDS:
        n = counts[seed["keyword"]]
        if not n:
            continue
        scores[seed["grade"]] = scores.get(seed["grade"], 0.0) + n * float(seed["weight"])
        matches.append((seed["keyword"], seed["grade"], seed.get("factor")))
    # 영어 약어 부스트는 포함관계와 무관하므로 두 변형에 동일하게 건다.
    _apply_high_risk_overrides(text, scores, {}, [], weight_multiplier=1.0)
    return scores, matches


def content_grade(scores: dict[str, float]) -> str:
    """키워드 argmax 등급. 동점이면 FNR-safe 로 상위 등급."""
    if sum(scores.values()) == 0:
        return "S3"
    top = max(scores.values())
    return min(
        [g for g, v in scores.items() if v == top], key=lambda g: GRADE_ORDER.get(g, 999)
    )


def final_grade(cg: str, matches, *, fix_c: bool) -> str:
    """label() 의 곱셈 블록 재현. fix_c=True 면 S1 에서 has_mgmt 상향을 막는다."""
    public = any(
        g == "S3" and to_canonical_factor(f or "") == "SECRECY" for _, g, f in matches
    )
    strong = cg in ("TS", "S1")
    has_mgmt = any(
        to_canonical_factor(f or "") == "MANAGEMENT" and g in ("TS", "S1")
        for _, g, f in matches
    )
    s = 0 if (public or cg == "S3") else (2 if strong else 1)
    v = 2 if strong else (0 if cg == "S3" else 1)
    if fix_c and cg == "S1":
        m = 0  # 정본 §4.5 의 S1 전형은 m=0 이다
    else:
        m = 2 if has_mgmt else (0 if cg in ("S3", "S1") else 1)
    return min([grade_from_svm(s, v, m), cg], key=lambda g: GRADE_ORDER.get(g, 999))


def fires(scores: dict[str, float], *, divisor: float = 1.0, scale: float = 1.0) -> str | None:
    """FNR-safe 상향이 어느 등급으로 발동하는가. divisor 가 B1 의 길이 정규화다."""
    for g in ("TS", "S1", "S2"):
        if (scores.get(g, 0.0) / divisor) >= THRESHOLDS[g] * scale:
            return g
    return None


def _is_miss(gold: str, pred: str) -> bool:
    """미탐 = 정답보다 낮은 등급으로 예측."""
    return GRADE_ORDER[pred] > GRADE_ORDER[gold]


def measure(path: str, engine: LabelRuleEngine) -> dict:
    docs = load_set(path)
    if not docs:
        return {"path": path, "documents": 0}

    leak = audit(docs)
    cur, b2, fix_c, all_scores, lengths = [], [], [], [], []
    repro_mismatch = 0
    for gold, text in docs:
        sc, ms = grade_scores(text, suppress_containment=False)
        sc_b2, ms_b2 = grade_scores(text, suppress_containment=True)
        cg = content_grade(sc)
        p_cur = final_grade(cg, ms, fix_c=False)
        if engine.label(text).grade != p_cur:
            repro_mismatch += 1
        cur.append(p_cur)
        b2.append(final_grade(content_grade(sc_b2), ms_b2, fix_c=False))
        fix_c.append(final_grade(cg, ms, fix_c=True))
        all_scores.append(sc)
        lengths.append(len(text))

    n = len(docs)

    def accuracy(preds):
        return sum(1 for (g, _), p in zip(docs, preds) if g == p) / n

    def miss_rate(preds):
        return sum(1 for (g, _), p in zip(docs, preds) if _is_miss(g, p)) / n

    # B1 — 발동 **물량을 같게 맞춘 뒤** 어떤 문서가 갈리는지만 본다.
    # 물량이 다르면 "더 많이 잡아서 좋아 보이는 것"과 구분되지 않는다.
    current_fire = [fires(s) for s in all_scores]
    n_fire = sum(1 for f in current_fire if f)
    best = None
    for i in range(1, 600):
        scale = i / 100.0
        normed = [
            fires(s, divisor=max(length, 1) / 1000.0, scale=scale)
            for s, length in zip(all_scores, lengths)
        ]
        k = sum(1 for f in normed if f)
        if best is None or abs(k - n_fire) < abs(best[0] - n_fire):
            best = (k, scale, normed)
    matched_count, scale, normed_fire = best
    dropped = [docs[i][0] for i in range(n) if current_fire[i] and not normed_fire[i]]
    added = [docs[i][0] for i in range(n) if normed_fire[i] and not current_fire[i]]

    return {
        "path": path,
        "documents": n,
        "grades": dict(Counter(g for g, _ in docs)),
        "length_only_1nn": leak.get("length_only_1nn"),
        "length_theils_u": leak.get("length_theils_u"),
        "tell_coverage": leak.get("tell_coverage"),
        "repro_mismatch": repro_mismatch,
        "current": {"accuracy": round(accuracy(cur), 4), "miss_rate": round(miss_rate(cur), 4)},
        "b2": {
            "accuracy": round(accuracy(b2), 4),
            "miss_rate": round(miss_rate(b2), 4),
            "changed": sum(1 for a, b in zip(cur, b2) if a != b),
        },
        "c": {
            "accuracy": round(accuracy(fix_c), 4),
            "miss_rate": round(miss_rate(fix_c), 4),
            "changed": sum(1 for a, b in zip(cur, fix_c) if a != b),
        },
        "b1": {
            "current_firings": n_fire,
            "normalized_firings": matched_count,
            "scale": scale,
            "dropped": dict(Counter(dropped)),
            "added": dict(Counter(added)),
        },
    }


def render(report: dict) -> None:
    if not report.get("documents"):
        print(f"  (건너뜀 — 등급 붙은 문서 없음) {report['path']}")
        return
    r = report
    print(f"\n{'=' * 78}")
    print(f"{r['path']}  n={r['documents']}  {r['grades']}")
    cap = RECOMMENDED_MAX_LENGTH_LEAK
    flag = "  <= 교락. 길이 관련 변경의 판단 근거로 쓰지 말 것" if (
        r["length_theils_u"] or 0) > cap else ""
    print(f"  길이 1-NN {r['length_only_1nn']} (랜덤 0.25) · Theil's U "
          f"{r['length_theils_u']} (상한 {cap}) · tell커버 {r['tell_coverage']}{flag}")
    print(f"  재현 불일치 {r['repro_mismatch']}/{r['documents']}"
          + ("   <= 0 이 아니면 아래 델타는 신뢰 불가" if r["repro_mismatch"] else ""))
    print(f"{'=' * 78}")
    c = r["current"]
    print(f"  [현행] 정확도 {c['accuracy']:.3f} · 미탐율 {c['miss_rate']:.3f}")
    for key, title in (("b2", "B2 부분문자열 억제"), ("c", "C  §4.5 S1 조건")):
        d = r[key]
        print(f"  [{title}] 정확도 {d['accuracy']:.3f} · 미탐율 {d['miss_rate']:.3f}"
              f" · 등급변경 {d['changed']}건")
    b1 = r["b1"]
    print(f"  [B1 길이정규화] 발동 {b1['current_firings']} -> {b1['normalized_firings']}건"
          f" (배율 {b1['scale']})")
    print(f"      상향에서 빠지는 문서 정답분포 {b1['dropped'] or '없음'}")
    print(f"      새로 드는 문서 정답분포     {b1['added'] or '없음'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="룰 연산 변형(B2/C/B1)의 판정면 델타 측정 — 고치기 전에 센다."
    )
    parser.add_argument("--set", dest="sets", action="append", default=None,
                        help="측정할 jsonl 경로. 반복 지정 가능. 미지정 시 기본 5종")
    parser.add_argument("--out", default=None, help="JSON 보고서 경로. 미지정 시 표준출력만")
    args = parser.parse_args()

    paths = args.sets or DEFAULT_SETS
    engine = LabelRuleEngine(seeds=KEYWORD_SEEDS)
    reports = []
    for path in paths:
        if not Path(path).exists():
            print(f"  (없음) {path}")
            continue
        report = measure(path, engine)
        reports.append(report)
        render(report)

    total = sum(r.get("documents", 0) for r in reports)
    b2_changed = sum(r.get("b2", {}).get("changed", 0) for r in reports)
    c_changed = sum(r.get("c", {}).get("changed", 0) for r in reports)
    print(f"\n{'=' * 78}")
    print(f"합계 {total}건 · B2 등급변경 {b2_changed}건 · C 등급변경 {c_changed}건")
    print("B1 은 셋마다 방향이 갈리므로 합산하지 않는다 — 셋별 분포를 직접 볼 것.")

    if args.out:
        Path(args.out).write_text(
            json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"보고서 기록: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
