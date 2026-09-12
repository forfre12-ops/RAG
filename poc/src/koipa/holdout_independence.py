"""홀드아웃이 학습셋과 정말 독립인가 — 비교를 하기 **전에** 센다.

왜 필요한가. 이 사업에서 실문서 골든셋은 구조적으로 존재할 수 없다(실데이터·검수·반출 0
확정, 실 TS 문서 0건). 그래서 모델 비교는 합성 홀드아웃으로 할 수밖에 없고, 그 홀드아웃이
학습셋과 계보상 겹치면 비교 자체가 무의미해진다 — 같은 생성기가 만든 셋에서 F1 0.99 를
받고 실문서에서 0.61 로 무너진 것이 정확히 그 사례다(2026-08-09 v4_3).

이 모듈이 세는 것은 셋이고, **세 가지가 다르다**:

1. **문서 중복**   같은 본문이 양쪽에 있는가. 가장 명백한 오염.
2. **가족 중복**   같은 document_family_id / scenario_id 에서 갈라져 나왔는가.
                   본문이 달라도 같은 시나리오면 사실상 같은 문제를 두 번 푸는 것이다.
3. **생성기 중복** authoring_method / generation_lineage 가 같은가. 여기가 겹치면 본문도
                   가족도 달라도 **같은 작문 습관**을 공유한다 — 모델은 그 습관을 배우고,
                   습관이 다른 실문서에서 무너진다.
4. **문장 공유**   양쪽에 **같은 문장**이 들어 있는가. 위 세 축은 전부 **메타데이터**를
                   비교하므로, 메타데이터를 다르게 붙이고 같은 문장 풀에서 본문을 뽑으면
                   전부 통과한다 — 그런데 그건 모델이 요인이 아니라 그 문장 풀을 외우게
                   만드는, 가장 직접적인 오염이다.

네 축이 모두 0 이어야 "계보 독립"이라고 부를 수 있다.

⚠ 4번은 **이 모듈의 초판에 없었다**(2026-08-12 추가). 초판은 1~3 만 보고 "독립"이라고
답했고, 그 상태로 v6 문장 풀을 평가셋에도 쓰면 학습셋과 평가셋이 같은 문장을 공유하면서도
검사를 통과했을 것이다. 도구가 통과시키는 범위를 도구의 보증 범위로 착각하면 안 된다 —
독립적으로 저술된 두 코퍼스가 25자 이상 문장을 글자 그대로 공유할 이유는 없다.

그리고 독립성만으로는 부족하다. 홀드아웃 **자체**가 길이나 등급 전용 문장으로 답을
알려주면, 독립이어도 그 수치는 분류 능력의 증거가 아니다. 그래서 누출 계량을 같은
보고서에 **함께** 싣는다(koipa.dataset_leakage) — 따로 내면 한쪽만 인용된다.

⚠ 이 보고서가 전부 통과해도 나올 수 있는 주장은 **"합성 내부 일관성 + 안전 무회귀"까지**다.
실 일반화 근거가 아니다 — 자체 실측으로 교차silver→gold_real F1 0.26 이 그 천장을 보여줬고,
그건 이번 검증이 부족해서가 아니라 데이터 정책이 만든 구조적 한계다. 실 일반화 증거는
고객사 현장 운영학습에서만 나온다.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

from koipa.dataset_leakage import _normalize_sentences, audit

# 권고 상한. 게이트가 아니라 판단 기준이다 — 막는 것은 호출부가 정한다.
RECOMMENDED_MAX_LENGTH_LEAK = 0.55
RECOMMENDED_MAX_THEILS_U = 0.25
RECOMMENDED_MAX_TELL_COVERAGE = 0.10
# 문장 공유. 이 축은 **같은 원본이 양쪽에 든 것**과 **문장 풀을 공유하는 것**을 잡으려고
# 2026-08-12 에 더했다. 둘 다 메타데이터로는 안 잡힌다.
#
# [2026-09-05] 임계가 0.02 였는데 그 근거는 이렇게 적혀 있었다:
#
#     "서식 상투어가 우연히 겹칠 수 있고, 그런 **한두 종**까지 막으면 경보가 무뎌진다.
#      문장 풀을 공유하면 커버리지가 **1.0 근처**로 나오므로 이 문턱으로도 충분히 갈린다."
#
# 전제가 "상투어는 한두 종"이었다. **한국어 판결문은 정형 문구가 수십 종이다** — 맺음말
# ("그러므로 상고를 기각하고 … 주문과 같이 판결한다")과 인용 서식("선고 #후# 판결(공#상, #)")이
# 25자를 넘어 정규화를 통과한다. 그래서 판례가 든 홀드아웃은 **어떻게 만들어도** 이 문턱을
# 못 넘었다. 실측:
#
#     셋            문장공유   같은원본        판정 근거
#     hardened42     0.0952     1건    길이축도 실패 → 막힘
#     holdout109     0.1284     2건    길이축도 실패 → 막힘
#     v5 test        0.1875     4건    길이축도 실패 → 막힘
#     길이균형        0.1200     0건    오염 0 · 길이축 통과인데 **이것 하나로 막혔다**
#
# 길이균형 셋의 공유 19종을 **전수 읽었다 — 같은 원본은 0건**이고 전부 정형이었다.
#
# 그래서 두 가지를 한다:
#   ① 판정에 **같은 원본 문서 수**를 넣는다(아래 _same_source_documents). 이 축이 원래
#      잡으려던 것이고, clean_holdout_leakage 가 쓰는 검증된 기준이다.
#   ② 커버리지 임계를 **풀 공유를 가릴 만큼**으로 올린다. 이 주석이 스스로 적은 대로 풀
#      공유는 1.0 근처다. 0.50 은 관측된 상투어 최댓값(0.19)의 2.6배이고 풀 공유의 절반이라
#      둘 사이를 넉넉히 가른다.
#
# ⚠ 느슨하게 푼 것이 아니다 — 이 변경으로 통과하게 되는 셋은 **길이균형 하나뿐**이다.
#   나머지는 같은 원본이 실제로 있고 길이 축도 못 넘어 그대로 막힌다.
# ⚠ **0.50 으로 올려 보았다가 0.02 로 되돌렸다(2026-09-05).** 판정을 "상투어를 뺀
#   커버리지"로 옮기면 판례가 든 셋이 통과하는데, 그렇게 하면 **문장 풀 공유 보호가
#   깨진다** — 풀 문장이 학습셋 여러 문서에 나오면 빈도 기준이 그것을 상투어로 분류한다.
#   시험 test_shared_sentence_pool_breaks_independence_despite_clean_metadata 가 잡았다.
#   빈도로는 정형 문구와 공유 풀을 못 가른다. 다른 기준이 필요하고, 그것은 사람 판단이다.
RECOMMENDED_MAX_SHARED_SENTENCE_COVERAGE = 0.02

# 같은 원본으로 보는 기준 — clean_holdout_leakage 와 같은 값을 쓴다. 눈으로 확인한
# 참 6건·거짓 4건을 여백 80% 대 33% 로 갈랐다.
SAME_SOURCE_MIN_SHARED = 3
SAME_SOURCE_MIN_RATIO = 0.60

_FAMILY_KEYS = ("document_family_id", "scenario_id", "family_profile_id")
_GENERATOR_KEYS = ("authoring_method", "primary_judge_model")


def _text(record: Mapping[str, Any]) -> str:
    return str(record.get("text") or "")


def _text_hash(record: Mapping[str, Any]) -> str:
    normalized = " ".join(_text(record).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _values(records: Sequence[Mapping[str, Any]], key: str) -> set[str]:
    out: set[str] = set()
    for record in records:
        value = record.get(key)
        if isinstance(value, (list, tuple)):
            out.update(str(v) for v in value if v)
        elif value:
            out.add(str(value))
    return out


def _overlap(left: set[str], right: set[str]) -> dict[str, Any]:
    shared = sorted(left & right)
    denominator = min(len(left), len(right)) or 1
    return {
        "train": len(left),
        "holdout": len(right),
        "shared": len(shared),
        "share_of_smaller": round(len(shared) / denominator, 4),
        "examples": shared[:5],
    }


# [2026-09-05] 상투어로 보는 문턱. 학습셋에서 이 수 이상의 **문서**에 나오는 문장은 그
# 코퍼스의 서식이지 특정 원본의 내용이 아니다. 한국어 판결문은 맺음말이 정형이라
# ("그러므로 상고를 기각하고 … 주문과 같이 판결한다") 독립 수집한 두 코퍼스도 반드시 겹친다.
_BOILERPLATE_MIN_TRAIN_DOCS = 3


def _same_source_documents(
    train_rows: Sequence[Mapping[str, Any]],
    holdout_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """홀드아웃 문서 중 학습셋 어느 한 문서와 **같은 원본**인 것 — 이 축이 원래 잡으려던 것.

    커버리지는 정형 문구까지 세므로 한국어 판례가 든 셋에서는 늘 높게 나온다. 오염은
    **문서 짝**으로 봐야 한다 — 홀드아웃 문서 하나가 학습 문서 하나와 문장을 많이 공유하면
    같은 원본이 길이만 달리해 들어간 것이다.

    기준은 clean_holdout_leakage 와 같다(짝과 공유 >= 3 · max(공유/홀드, 공유/학습) >= 0.60).
    눈으로 확인한 참 6건·거짓 4건을 여백 80% 대 33% 로 갈랐다. 실측 예:

        holdout109 #56 = train #1933   두산중공업 경업금지가처분 · 문장 51/51 · 라벨 S1 대 TS
    """
    train_sents = [_normalize_sentences(_text(r)) for r in train_rows]
    inverted: dict[str, list[int]] = {}
    for i, sents in enumerate(train_sents):
        for sentence in sents:
            inverted.setdefault(sentence, []).append(i)

    hits: list[dict[str, Any]] = []
    for row in holdout_rows:
        sents = _normalize_sentences(_text(row))
        if not sents:
            continue
        counts: dict[int, int] = {}
        for sentence in sents:
            for i in inverted.get(sentence, ()):
                counts[i] = counts.get(i, 0) + 1
        if not counts:
            continue
        idx, shared = max(counts.items(), key=lambda kv: kv[1])
        if shared < SAME_SOURCE_MIN_SHARED:
            continue
        ratio = max(shared / len(sents), shared / (len(train_sents[idx]) or 1))
        if ratio < SAME_SOURCE_MIN_RATIO:
            continue
        hits.append({
            "holdout_doc_id": str(row.get("doc_id")),
            "train_doc_id": str(train_rows[idx].get("doc_id")),
            "shared_sentences": shared,
            "ratio": round(ratio, 4),
        })
    return {
        "documents": len(hits),
        "coverage": round(len(hits) / (len(holdout_rows) or 1), 4),
        "examples": hits[:3],
    }


def _shared_sentences(
    train_rows: Sequence[Mapping[str, Any]],
    holdout_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """양쪽에 글자 그대로 들어 있는 문장 — 메타데이터로는 안 잡히는 오염.

    중요한 것은 종 수가 아니라 **커버리지**다. 상투어 두어 종이 겹치는 것과, 홀드아웃
    전 문서가 학습셋 문장을 품고 있는 것은 완전히 다른 상황인데 종 수만 보면 구분이 안 된다.

    [2026-09-05] 커버리지 하나로는 **왜 걸렸는지**를 못 판다. 실측: 길이를 균형 잡은
    홀드아웃(Theil's U 0.074 · 길이-only 0.242 로 무작위 0.25 보다도 낮다)에서도 커버리지가
    0.124 로 나왔다. 공유 20종을 **전부 눈으로 읽었더니 같은 원본은 하나도 없었다** —
    전부 정형 문구다:

        판결문 서식 6종   그러므로 상고를 기각하고 … 주문과 같이 판결한다
                          선고 #구# 판결 【주 문】 상고를 기각한다
        생성기 템플릿 9종  본 문서는 [가상기업A]의 #급 비밀로 분류된 자료로 …
                          This document is material for [Company A] classified as Level # Secret.
        보고서 서식 1종    다음 주에 대한 투자 전략과 주요 이슈를 점검합니다

    한국어 판례가 든 홀드아웃은 어떻게 만들어도 판결문 서식을 공유한다. 생성기 템플릿은
    합성 코퍼스가 같은 머리 문장을 재사용해서 생긴다(그 문장이 등급까지 말한다 —
    rag_corpus_v2 720건 중 50%가 본문에 자기 등급을 노출한다).

    그래서 **학습셋에서 여러 문서에 나오는 문장(상투어)을 뺀 커버리지**를 함께 낸다.

    ⚠ 이 갈래는 **선별 보조이지 판정이 아니다.** 문턱을 3개 문서로 두었는데, 학습셋에
      판례가 적으면 판결문 서식도 1~2개 문서에만 나와 "상투어 아님"으로 남는다 —
      위 실측에서도 20종 중 4종만 상투어로 걸러졌고 나머지 16종은 사람이 읽어야 정형인
      것이 드러났다. 빈도는 정형성을 재지 못하고 그 코퍼스에 판례가 몇 건인지를 잰다.

    ⚠ 판정(lineage_independent · usable_for_comparison)은 **바꾸지 않는다.** 이 축은 초판이
      너무 관대해서 나중에 더한 것이라(2026-08-12), 도구가 스스로 느슨해지면 처음 문제로
      되돌아간다. 문턱을 풀지 말지는 사람이 수치와 실제 문장을 보고 정한다.
    """
    train_doc_freq: dict[str, int] = {}
    for row in train_rows:
        for sentence in _normalize_sentences(_text(row)):
            train_doc_freq[sentence] = train_doc_freq.get(sentence, 0) + 1
    train_sentences = set(train_doc_freq)
    boilerplate = {s for s, n in train_doc_freq.items() if n >= _BOILERPLATE_MIN_TRAIN_DOCS}

    shared_types: set[str] = set()
    distinctive_types: set[str] = set()
    touched = 0
    touched_distinctive = 0
    for row in holdout_rows:
        overlap = _normalize_sentences(_text(row)) & train_sentences
        if not overlap:
            continue
        touched += 1
        shared_types |= overlap
        rare = overlap - boilerplate
        if rare:
            touched_distinctive += 1
            distinctive_types |= rare
    n_holdout = len(holdout_rows) or 1
    return {
        "train_sentence_types": len(train_sentences),
        "shared_types": len(shared_types),
        "holdout_documents_touched": touched,
        "coverage": round(touched / n_holdout, 4),
        # 상투어를 뺀 값 — 판정에는 쓰지 않고 "왜 걸렸나"를 가릴 수 있게 함께 낸다.
        "boilerplate_types": len(shared_types & boilerplate),
        "distinctive_types": len(distinctive_types),
        "distinctive_coverage": round(touched_distinctive / n_holdout, 4),
        "examples": sorted(shared_types)[:3],
        "distinctive_examples": sorted(distinctive_types)[:3],
    }


def assess(
    train: Iterable[Mapping[str, Any]],
    holdout: Iterable[Mapping[str, Any]],
    *,
    label: str = "holdout",
) -> dict[str, Any]:
    """계보 독립성 + 홀드아웃 자체 누출을 한 보고서로 낸다.

    판정하지 않고 사실만 모은다 — 막을지는 호출부가 정한다(check_or_raise 와 같은 분담).
    """
    train_rows = [r for r in train if _text(r)]
    holdout_rows = [r for r in holdout if _text(r)]

    document = _overlap(
        {_text_hash(r) for r in train_rows}, {_text_hash(r) for r in holdout_rows}
    )
    family = {key: _overlap(_values(train_rows, key), _values(holdout_rows, key)) for key in _FAMILY_KEYS}
    generator = {
        key: _overlap(_values(train_rows, key), _values(holdout_rows, key))
        for key in (*_GENERATOR_KEYS, "generation_lineage")
    }
    sentences = _shared_sentences(train_rows, holdout_rows)
    same_source = _same_source_documents(train_rows, holdout_rows)

    holdout_leakage = audit((str(r.get("label") or ""), _text(r)) for r in holdout_rows)
    train_leakage = audit((str(r.get("label") or ""), _text(r)) for r in train_rows)

    independent = (
        document["shared"] == 0
        and all(v["shared"] == 0 for v in family.values())
        and all(v["shared"] == 0 for v in generator.values())
        # [2026-09-05] 오염은 **문서 짝**으로 본다. 커버리지는 정형 문구까지 세므로 한국어
        # 판례가 든 셋에서는 늘 높다(상단 상수 주석의 실측 참조).
        and same_source["documents"] == 0
        # ⚠ 커버리지 축은 **그대로 둔다.** 2026-09-05 에 상투어를 뺀 값으로 옮겨 보았다가
        #   되돌렸다 — 문장 풀 공유 시험(test_shared_sentence_pool_breaks_independence…)이
        #   통과해 버렸다. 풀 문장이 학습셋 여러 문서에 나오면 빈도 기준이 그것을 "상투어"로
        #   분류하기 때문이다. 즉 **빈도로는 정형 문구와 공유 풀을 못 가른다**
        #   (_shared_sentences docstring 에 적어 둔 그 한계다).
        #   판례가 든 셋이 이 문턱을 못 넘는 것은 남은 문제이고, 푸는 것은 사람 판단이다.
        and sentences["coverage"] <= RECOMMENDED_MAX_SHARED_SENTENCE_COVERAGE
    )
    concerns: list[str] = []
    if document["shared"]:
        concerns.append(f"같은 본문 {document['shared']}건이 양쪽에 있다")
    if same_source["documents"]:
        ex = same_source["examples"][0] if same_source["examples"] else {}
        concerns.append(
            "홀드아웃 %d건이 학습셋 문서와 **같은 원본**이다(길이만 달라 본문 해시는 갈린다)"
            " — 예: 홀드 %s ↔ 학습 %s · 문장 %s개 공유 · 비율 %s"
            % (same_source["documents"], ex.get("holdout_doc_id"), ex.get("train_doc_id"),
               ex.get("shared_sentences"), ex.get("ratio"))
        )
    if sentences["coverage"] > RECOMMENDED_MAX_SHARED_SENTENCE_COVERAGE:
        concerns.append(
            f"홀드아웃 {sentences['coverage']:.1%} 가 학습셋과 같은 문장을 품고 있다"
            f"({sentences['shared_types']}종 · 그중 상투어 {sentences['boilerplate_types']}종)"
            f" — 상투어를 빼면 {sentences['distinctive_coverage']:.1%}"
            f"({sentences['distinctive_types']}종). 메타데이터가 달라도 문장 풀을 공유하면"
            " 모델은 요인이 아니라 그 문장을 외운다"
        )
    for key, value in family.items():
        if value["shared"]:
            concerns.append(f"{key} {value['shared']}개가 겹친다 — 같은 시나리오를 두 번 푼다")
    for key, value in generator.items():
        if value["shared"]:
            concerns.append(
                f"{key} {value['shared']}개가 겹친다 — 같은 작문 습관을 공유한다"
                " (본문·가족이 달라도 습관은 배운다)"
            )
    # [2026-09-05] **등급이 하나뿐인 셋에서는 길이 지표가 성립하지 않는다.**
    # 1-NN 은 이웃이 무조건 같은 등급이라 항상 1.000 이 나오고 무작위 기대값도 1.000 이다.
    # 그런데 경고는 무작위 기준선을 보지 않고 1nn > 0.55 만 봐서 **항상 켜졌다.**
    #
    # 실측: datasets/proxy_gold/public_s3_challenges/public-s3-300 — 공개문서 300건이
    # 전부 S3 인 과분류 측정 전용 셋이다. 학습셋과 본문중복 0 · 문장공유 0.0000 으로
    # 완전히 독립인데도 이 경고 하나 때문에 usable_for_comparison=false 가 됐다.
    # 정당한 평가 도구를 쓸 수 없다고 말하는 셈이었다.
    #
    # 등급이 둘 미만이면 길이 축을 판정에서 뺀다. 값은 그대로 보고하고 사유는 notes 에
    # 남긴다 — concerns 에 넣으면 설명이 곧 차단이 된다(usable = not concerns).
    notes: list[str] = []
    grades_present = len(holdout_leakage.get("length_by_grade") or {})
    length_axis_applies = grades_present >= 2
    if holdout_leakage.get("documents") and not length_axis_applies:
        notes.append(
            "등급이 %d종뿐이라 길이 지표를 판정에 쓰지 않았다 — 1-NN 은 이웃이 무조건 같은"
            " 등급이라 항상 1.000 이 나온다(무작위 기대값도 1.000). 과분류 전용 셋처럼 한"
            " 등급만 담은 것은 정상이다. 문장 공유·계보 축은 그대로 봤다." % grades_present
        )

    if holdout_leakage.get("documents") and length_axis_applies:
        if holdout_leakage["length_only_1nn"] > RECOMMENDED_MAX_LENGTH_LEAK:
            concerns.append(
                f"홀드아웃 길이-only {holdout_leakage['length_only_1nn']:.3f}"
                f" > 권고 {RECOMMENDED_MAX_LENGTH_LEAK}"
            )
        if holdout_leakage.get("length_theils_u", 0.0) > RECOMMENDED_MAX_THEILS_U:
            concerns.append(
                f"홀드아웃 Theil's U {holdout_leakage['length_theils_u']:.3f}"
                f" > 권고 {RECOMMENDED_MAX_THEILS_U}"
            )
        if holdout_leakage["tell_coverage"] > RECOMMENDED_MAX_TELL_COVERAGE:
            concerns.append(
                f"홀드아웃 tell 커버 {holdout_leakage['tell_coverage']:.3f}"
                f" > 권고 {RECOMMENDED_MAX_TELL_COVERAGE}"
            )

    return {
        "label": label,
        "train_documents": len(train_rows),
        "holdout_documents": len(holdout_rows),
        "lineage_independent": independent,
        "overlap": {
            "document_text": document,
            "family": family,
            "generator": generator,
            "shared_sentences": sentences,
            # 이 축이 원래 잡으려던 것 — 정형 문구가 아니라 같은 원본 문서.
            "same_source_documents": same_source,
        },
        "holdout_leakage": holdout_leakage,
        "train_leakage": train_leakage,
        "concerns": concerns,
        # 판정을 막지는 않지만 읽는 사람이 알아야 하는 것. concerns 와 갈라 둔다 —
        # usable_for_comparison 이 concerns 의 유무로 정해지기 때문이다.
        "notes": notes,
        "usable_for_comparison": independent and not concerns,
        # 통과해도 이 문장을 넘는 주장은 할 수 없다. 보고서에 박아 둔다 —
        # 지표만 인용되고 한정이 떨어져 나가는 일이 반복됐다.
        "claim_ceiling": (
            "합성 내부 일관성과 안전 무회귀까지. 실문서 일반화 근거가 아니다"
            "(자체 실측: 교차silver→gold_real F1 0.26). 실 일반화 증거는 고객사 현장"
            " 운영학습에서만 나온다."
        ),
    }
