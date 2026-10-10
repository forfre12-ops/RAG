# -*- coding: utf-8 -*-
"""모의문서 43건 세트에 지름길(표면 단서)이 남아있는지 통계로 확인 — 2차(가족단위+사전목표치).

사용자가 전달한 "모의문서 생성·검증 절차" 안내문(2차본) 4번의 절차를 구현한다.
1차본(같은 파일명, 이전 버전)과의 차이:
  ① 같은 상황·같은 양식에서 등급만 바꾼 "가족"(대조 문서 쌍)을 학습/시험이 갈리지
     않게 묶어서 교차검증한다(StratifiedGroupKFold) — 이전엔 일반 stratified 3-fold였다.
  ② 목표치를 **결과를 보기 전에** 이 파일에 못박아 둔다(안내문 4-1: "결과를 보고 나서
     목표를 정하면 기준이 계속 느슨해진다"). 아래 PRE_REGISTERED_TARGET 이 그것이다.
  ③ 제목 낱말이 등급에 쏠리는지 별도로 센다(등급 정의 자체에 속한 낱말은 제외).

이 스크립트는 "지름길이 없다"를 확인하는 것이지 "등급 판정이 맞다"를 보증하지 않는다.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline

DATA_DIR = Path(__file__).resolve().parents[1] / "datasets" / "expert_review_mock_guide40_20260929"
OUT_PATH = DATA_DIR / "shortcut_audit_20260929_v2_family.json"

N_SPLITS = 3
N_REPEATS_REAL = 30
N_PERMUTATIONS = 300

# ── 사전 목표치 (측정 전에 고정 — 이 파일을 커밋/실행하기 전에 정함) ──────────────
# 안내문 4-1: "무작위 섞기 기준선과의 차이가 얼마 이내면 통과로 본다"를 결과를 보기 전에 정한다.
PRE_REGISTERED_TARGET = {
    "ngram_classifier": (
        "글자 n-gram 분류기(가족단위 3-fold 교차검증, 30회 평균)의 실제 정확도가 "
        "무작위 라벨 섞기 300회 분포의 95번째 백분위(percentile_rank <= 0.95) 이내면 통과."
        " 초과하면 '잔여 지름길 있음'으로 보고 원인을 더 추적한다."
    ),
    "title_words": (
        "등급 정의와 무관한 낱말이 어떤 등급의 제목에서는 2회 이상 나오고 다른 모든 등급의 "
        "제목에는 0회면 '점검 대상'으로 표시한다(자동 합격/불합격 기준이 아니라 목록만 뽑아 "
        "사람이 등급 정의 관련 낱말인지 판단한다)."
    ),
}

# ── 가족(안내문 3번 "같은 업무 상황·같은 양식에서 등급만 다르게 바꾼 대조 문서") ──
# 실제로 그렇게 설계해 넣은 두 쌍만 가족으로 묶는다(연구재료 재고 메모는 소재만 같을 뿐
# 상황·양식이 다른 별개 문서라 가족으로 묶지 않는다 — 과하게 묶으면 검정력만 준다).
FAMILY_TITLE_GROUPS: list[list[str]] = [
    ["선행개발팀 경력직 채용 진행 현황", "생산라인 현장 인턴 채용 진행 현황"],
    ["9월 커넥터 핀 불량 개선 회의록", "9월 포장 라벨 인쇄 불량 개선 회의록"],
]

# 등급 정의 자체에 속해 써도 되는 낱말(안내문: "공개 자료의 '공개'는 써도 된다").
_GRADE_DEFINITION_WORDS = {"공개"}


def load():
    docs = [json.loads(l) for l in (DATA_DIR / "reviewer_documents.jsonl").open(encoding="utf-8")]
    manifest = {
        r["review_id"]: r
        for r in (json.loads(l) for l in (DATA_DIR / "internal_manifest.jsonl").open(encoding="utf-8"))
    }
    texts = [d["text"] for d in docs]
    grades = [manifest[d["review_id"]]["true_grade"] for d in docs]
    titles = [d["text"].split("\n", 1)[0] for d in docs]
    review_ids = [d["review_id"] for d in docs]

    groups = list(range(len(docs)))  # 기본: 각자 고유 그룹(=싱글턴 가족)
    title_to_idx = {t: i for i, t in enumerate(titles)}
    for gi, fam_titles in enumerate(FAMILY_TITLE_GROUPS):
        idxs = [title_to_idx[t] for t in fam_titles if t in title_to_idx]
        if len(idxs) != len(fam_titles):
            missing = [t for t in fam_titles if t not in title_to_idx]
            raise SystemExit(f"가족 제목을 못 찾음(제목이 바뀌었을 수 있음): {missing}")
        fam_group_id = 10_000 + gi  # 싱글턴 인덱스와 안 겹치게
        for i in idxs:
            groups[i] = fam_group_id

    return texts, grades, titles, groups, review_ids


def make_clf():
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1, max_features=4000)
    clf = LogisticRegression(max_iter=2000, class_weight="balanced")
    return make_pipeline(vec, clf)


def group_cv_accuracy(texts, labels, groups, seed: int) -> float:
    y = np.array(labels)
    g = np.array(groups)
    skf = StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True, random_state=seed)
    correct = total = 0
    for train_idx, test_idx in skf.split(texts, y, groups=g):
        X_train = [texts[i] for i in train_idx]
        X_test = [texts[i] for i in test_idx]
        model = make_clf()
        model.fit(X_train, y[train_idx])
        pred = model.predict(X_test)
        correct += int((pred == y[test_idx]).sum())
        total += len(test_idx)
    return correct / total


_WORD_RE = re.compile(r"[가-힣]{2,}")


def title_word_clustering(titles: list[str], grades: list[str]) -> dict[str, list[str]]:
    """등급별 제목 낱말 빈도 — 한 등급에만 2회 이상 몰리고 다른 등급엔 0인 낱말을 뽑는다."""
    by_grade: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for t, g in zip(titles, grades):
        words = set(_WORD_RE.findall(t)) - _GRADE_DEFINITION_WORDS
        for w in words:
            by_grade[g][w] += 1
    all_grades = list(by_grade.keys())
    flagged: dict[str, list[str]] = collections.defaultdict(list)
    for g in all_grades:
        for w, c in by_grade[g].items():
            if c >= 2 and all(by_grade[og][w] == 0 for og in all_grades if og != g):
                flagged[g].append(f"{w}(x{c})")
    return dict(flagged)


def main():
    print("=== 사전 목표치(측정 전 고정) ===")
    for k, v in PRE_REGISTERED_TARGET.items():
        print(f"- {k}: {v}")
    print()

    texts, grades, titles, groups, review_ids = load()
    n = len(texts)
    class_counts = collections.Counter(grades)
    print(f"문서 {n}건, 등급 분포 {dict(class_counts)}")
    n_families = len(set(groups))
    print(f"가족 수(교차검증 그룹 수): {n_families} (싱글턴 {n - 2*len(FAMILY_TITLE_GROUPS)}개 + 대조쌍 {len(FAMILY_TITLE_GROUPS)}개)")

    # ① 제목 낱말 쏠림
    flagged_words = title_word_clustering(titles, grades)
    print(f"\n[제목 낱말 쏠림] 등급 정의와 무관한데 한 등급에만 2회 이상 몰린 낱말: {flagged_words if flagged_words else '없음'}")

    # ② 가족단위 그룹 교차검증 — 실제 라벨
    real_accs = [group_cv_accuracy(texts, grades, groups, seed=s) for s in range(N_REPEATS_REAL)]
    real_mean = float(np.mean(real_accs))
    print(f"\n[글자 n-gram 분류기 · 가족단위 3-fold x {N_REPEATS_REAL}회 평균] 실제 라벨 정확도: {real_mean:.3f}"
          f" (표준편차 {np.std(real_accs):.3f})")

    # ③ 무작위 섞기 기준선 — 같은 가족단위 절차를 라벨만 섞어 반복
    #    (라벨을 섞을 때도 같은 가족(대조쌍)은 같은 라벨을 받게 하지 않는다 — 이 데이터의
    #    실제 설계가 "가족 안에서 등급이 다르다"이므로, 라벨을 통째로 무작위 재배정하는 것이
    #    올바른 귀무가설이다. 가족의 그룹 구조 자체는 그대로 두고 라벨만 섞는다.)
    rng = np.random.default_rng(20260929)
    perm_accs = []
    for p in range(N_PERMUTATIONS):
        shuffled = list(grades)
        rng.shuffle(shuffled)
        perm_accs.append(group_cv_accuracy(texts, shuffled, groups, seed=p))
    perm_arr = np.array(perm_accs)
    percentile_rank = float((perm_arr < real_mean).mean())

    print(f"[무작위 섞기 기준선] 평균 {perm_arr.mean():.3f} (표준편차 {perm_arr.std():.3f}, 최대 {perm_arr.max():.3f})")
    print(f"실제 정확도({real_mean:.3f})의 순열분포 내 백분위: {percentile_rank:.3f}")

    verdict = "통과" if percentile_rank <= 0.95 else "미달(잔여 지름길 의심)"
    print(f"\n=== 사전 목표치 대비 판정: {verdict} (기준 95백분위, 실측 {percentile_rank:.3f}) ===")

    result = {
        "pre_registered_target": PRE_REGISTERED_TARGET,
        "n_docs": n,
        "class_counts": dict(class_counts),
        "n_families": n_families,
        "title_word_clustering_flagged": flagged_words,
        "ngram_classifier_family_aware": {
            "real_accuracy_mean": real_mean,
            "real_accuracy_std": float(np.std(real_accs)),
            "permutation_mean": float(perm_arr.mean()),
            "permutation_std": float(perm_arr.std()),
            "permutation_max": float(perm_arr.max()),
            "percentile_rank_of_real": percentile_rank,
            "verdict_vs_pre_registered_target": verdict,
        },
        "n_splits": N_SPLITS,
        "n_repeats_real": N_REPEATS_REAL,
        "n_permutations": N_PERMUTATIONS,
        "caveat": (
            "표본 43건(등급당 3~17건, 그중 대조쌍 2개는 가족으로 묶여 사실상 41개 독립 단위)으로 "
            "검정력이 낮다."
        ),
    }
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장 -> {OUT_PATH}")


if __name__ == "__main__":
    main()
