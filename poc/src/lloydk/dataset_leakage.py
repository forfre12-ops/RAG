"""학습셋·평가셋 누출 검사 — 등급을 맞히는 데 본문 말고 다른 단서가 섞였는지 본다.

왜 필요한가(2026-08-09 실측):

- `direct_authored_catalog_training.v4_3` 은 **문서 길이만으로 등급을 100% 맞힐 수 있었다**
  (등급별 길이 구간이 겹치지 않음: TS 3,273~3,328 · S1 3,483~3,580 · S2 3,205~3,269 ·
  S3 3,152~3,196). 모델은 내용을 읽을 이유가 없어 길이를 외웠고, 같은 생성기로 만든
  평가셋에서 F1 0.99 를 받았지만 실문서에서는 0.61, S1 미탐 64% 였다.
- 같은 코퍼스의 문서 **전부**에 등급별 고정 문단이 붙어 있었다("접근 제한 … 적용하지 않으며"
  같은 문장이 S3 전건에). 모델도 서빙 가드도 그 문구를 맞추고 있었다.

두 지표 모두 **학습 전에** 30초면 나온다. 사람이 문서를 읽어서는 절대 안 보인다.
빌드 산출물에 이 검사를 걸어 두면 같은 실패가 GPU 시간을 쓰기 전에 걸린다.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable, Sequence

GRADES = ("TS", "S1", "S2", "S3")
_GRADE_TOKEN = re.compile(r"\b(TS|S1|S2|S3)\b")
_SENTENCE = re.compile(r"[^\n.!?]{20,}?(?:다\.|니다\.|[.!?])")

# 등급이 4개이므로 길이만 찍어 맞힐 기대값은 0.25다. 실측 기준선(2026-08-09):
#   v4_3 신규 학습셋      1.000  ← 길이가 곧 등급. 학습하면 안 된다
#   gold_real 정본 777건  0.802  ← 짧으면 고등급/길면 S3. 이 셋으로 잰 수치는 부풀려진다
#   v5 학습셋(배포본 출처) 0.439  ← 약한 상관. 배포 모델이 여기서 나왔다
#   KL 검수 후보 272건    0.301  ← 등급별 길이를 맞춰 뽑은 것
# 게이트는 "확실히 망가진" 구간만 막는다. 0.44 를 막으면 현행 배포본 학습셋도 못 만든다.
# 경계선 값은 막지 않되 지표를 매니페스트에 남겨 사람이 보게 한다.
DEFAULT_MAX_LENGTH_LEAK = 0.55
# 같은 문장이 한 등급에만 반복 등장하면 그 문장이 정답표다. 소수 우연 일치는 허용한다.
DEFAULT_MAX_TELL_COVERAGE = 0.10


def _normalize_sentences(text: str) -> set[str]:
    out: set[str] = set()
    for raw in _SENTENCE.findall(text or ""):
        s = re.sub(r"\s+", " ", raw).strip()
        s = re.sub(r"\d+", "#", s)  # 숫자만 다른 문장은 같은 문형으로 본다
        if len(s) >= 25:
            out.add(s)
    return out


def length_only_accuracy(pairs: Sequence[tuple[int, str]]) -> float:
    """길이만 보고 최근접 이웃의 등급을 따라갔을 때 맞는 비율.

    분류기를 학습시키지 않고도 "길이가 등급을 얼마나 알려주는지"를 잰다.
    """
    rows = sorted(pairs)
    if len(rows) < 2:
        return 0.0
    hit = 0
    for i, (length, grade) in enumerate(rows):
        best, best_gap = None, None
        for j in (i - 1, i + 1):
            if 0 <= j < len(rows):
                gap = abs(rows[j][0] - length)
                if best_gap is None or gap < best_gap:
                    best_gap, best = gap, rows[j][1]
        hit += best == grade
    return hit / len(rows)


def grade_tells(docs: Sequence[tuple[str, str]], *, min_share: float = 0.05) -> dict[str, str]:
    """등급 전용 문장 → 그 등급. 한 등급 문서의 min_share 이상에 나오고 등장의 95% 이상이
    그 등급 하나일 때만 tell 로 본다."""
    per_grade = Counter(g for g, _t in docs)
    sentence_grades: dict[str, Counter] = defaultdict(Counter)
    for grade, text in docs:
        for s in _normalize_sentences(text):
            sentence_grades[s][grade] += 1
    tells: dict[str, str] = {}
    for sentence, counts in sentence_grades.items():
        total = sum(counts.values())
        grade, n = counts.most_common(1)[0]
        if not per_grade[grade]:
            continue
        if n / total >= 0.95 and n / per_grade[grade] >= min_share:
            tells[sentence] = grade
    return tells


def audit(docs: Iterable[tuple[str, str]]) -> dict:
    """(등급, 본문) 목록 → 누출 지표. 판정은 하지 않는다(check_or_raise 가 한다)."""
    rows = [(g, t) for g, t in docs if g in GRADES and (t or "").strip()]
    if not rows:
        return {"documents": 0}
    tells = grade_tells(rows)
    covered = sum(
        1 for g, t in rows if any(tells.get(s) == g for s in _normalize_sentences(t))
    )
    exposed = sum(1 for g, t in rows if _GRADE_TOKEN.search(t))
    by_grade: dict[str, dict[str, int]] = {}
    for g in GRADES:
        lens = sorted(len(t) for gg, t in rows if gg == g)
        if lens:
            by_grade[g] = {
                "n": len(lens), "min": lens[0], "p50": lens[len(lens) // 2], "max": lens[-1]
            }
    return {
        "documents": len(rows),
        "length_only_1nn": round(length_only_accuracy([(len(t), g) for g, t in rows]), 3),
        "length_only_random": round(1 / len(set(g for g, _ in rows)), 3),
        "tell_sentences": len(tells),
        "tell_coverage": round(covered / len(rows), 3),
        "grade_token_exposed": exposed,
        "length_by_grade": by_grade,
    }


class DatasetLeakageError(ValueError):
    """빌드 게이트 위반 — 이 셋으로 학습·평가하면 수치가 부풀려진다."""


def check_or_raise(
    docs: Iterable[tuple[str, str]],
    *,
    label: str,
    max_length_leak: float = DEFAULT_MAX_LENGTH_LEAK,
    max_tell_coverage: float = DEFAULT_MAX_TELL_COVERAGE,
    allow_grade_token: bool = True,
) -> dict:
    """누출이 임계를 넘으면 막는다. 통과해도 지표를 돌려주므로 매니페스트에 실을 수 있다.

    ``allow_grade_token`` 기본 True — 학습셋 본문이 등급을 언급하는 것 자체는 자연스럽다
    (실측 v5 학습셋 2,042건 중 240건). 다만 **사람이 읽고 등급을 정할 검수 후보**에서는
    본문에 답이 적혀 있으면 검수가 검증이 아니라 확인 절차가 되므로 False 로 준다.
    """
    report = audit(docs)
    if not report.get("documents"):
        return report
    problems = []
    if report["length_only_1nn"] > max_length_leak:
        problems.append(
            f"길이만으로 등급 {report['length_only_1nn']:.3f} 적중"
            f"(무작위 {report['length_only_random']}, 한계 {max_length_leak})"
            " — 등급별 길이 분포가 갈려 있다"
        )
    if report["tell_coverage"] > max_tell_coverage:
        problems.append(
            f"등급 전용 문장이 문서 {report['tell_coverage']:.1%} 에 존재"
            f"(tell {report['tell_sentences']}종, 한계 {max_tell_coverage:.0%})"
            " — 본문에 정답이 적혀 있다"
        )
    if not allow_grade_token and report["grade_token_exposed"]:
        problems.append(
            f"본문에 등급 문자열이 남은 문서 {report['grade_token_exposed']}건"
            " — 검수자가 읽기 전에 답을 본다"
        )
    if problems:
        raise DatasetLeakageError(
            f"[{label}] 누출 검사 실패 ({report['documents']}건)\n  · "
            + "\n  · ".join(problems)
        )
    return report
