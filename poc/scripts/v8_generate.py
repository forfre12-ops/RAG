"""v8 생성기 — 형태 × 요소경계 × 근거언어 세 축을 교차한다.

설계 요지 세 가지. 나머지는 전부 이 셋을 지키기 위한 장치다.

  1. 형태와 등급을 **독립 배정**한다. 특정 형태가 특정 등급에 몰리면 형태가 새 tell 이 된다.
     무작위로 뿌리면 표본이 작을 때 쏠린다 - 그래서 라운드로빈으로 결정적으로 배정한다.

  2. 예산은 **결정 경계**에 쓴다. 27조합 균등 표집을 하면 18/27 이 S3 라 학습셋이 S3 67%
     가 되고, 1차 목표인 고등급 미탐 최소화와 정반대로 간다.

  3. counterfactual 쌍은 **한 요소만** 다르다. 같은 사실 · 같은 형태 · 같은 채움 서술 ·
     같은 길이대에서 한 요소의 근거 문장만 바꿔 등급이 경계를 넘게 한다.

⚠ 홀드아웃 2종(`FORM_HOLDOUT`)은 학습 산출물에 넣지 않는다. 한 번이라도 새면 판정면이 사라진다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from v8_document_forms import (  # noqa: E402
    FORM_BY_ID, HOLDOUT2_FORMS, HOLDOUT_FORMS, NO_FACTOR_FORMS, TRAIN_FORMS,
    sanity_check,
)
from v8_factor_labels import (  # noqa: E402
    FACTORS,
    PRESENT,
    PROVEN_ABSENT,
    UNKNOWN,
    DocumentLabel,
    FactorLabel,
    adjacent_boundaries,
)
# 1차 판정에서 어휘 연상 실패가 확인돼 프레임 기반 극성 최소쌍으로 교체했다
# (V8_RESULT_2026-08-13.md §4~6). 문장 단위 분할이 같은 어휘의 긍정형과 부정형을
# 서로 다른 분할에 넣어 어휘 연상을 최적해로 만들었다.
from v8_factor_frames import audit as sentence_audit  # noqa: E402
from v8_factor_frames import near_miss_for, sentences_for  # noqa: E402
from v8_inline_forms import embed as embed_inline  # noqa: E402
from v8_registers import LINEAGES  # noqa: E402
from v8_registers import render as render_register  # noqa: E402

# ── 채움 서술 — 요소와 무관한 본문. 형태마다 섞여야 문체가 tell 이 되지 않는다 ──────
FILLER = {
    "intro": [
        "적용 범위와 목적을 아래와 같이 정한다.",
        "본 건의 배경과 검토 사유를 정리한다.",
        "해당 업무의 대상과 기준 시점을 밝힌다.",
    ],
    "observe": [
        "확인된 사항을 항목별로 정리하면 아래와 같다.",
        "현장 확인 결과 특이사항은 다음과 같이 나타났다.",
        "검토 과정에서 반복적으로 지적된 내용을 모았다.",
    ],
    "procedure": [
        "진행 순서는 접수, 확인, 판정, 통보의 네 단계로 한다.",
        "담당자는 각 단계마다 결과를 기록하고 다음 단계로 넘긴다.",
        "예외가 발생하면 상위 승인을 받은 뒤 진행한다.",
    ],
    "numbers": [
        "주요 수치는 기준값 대비 편차와 함께 아래 표에 적었다.",
        "측정 결과는 3회 평균이며 반올림 없이 그대로 기재한다.",
        "구간별 산출 근거와 적용 계수를 나란히 둔다.",
    ],
    "exception": [
        "긴급 처리분은 별도 절차를 따르며 사후 확인 대상이 된다.",
        "아래 조건에 해당하면 본 절차를 적용하지 않는다.",
        "판단이 어려운 경우 보류하고 담당 부서에 회신한다.",
    ],
    "closing": [
        "이상의 내용을 반영해 후속 조치를 진행한다.",
        "추가 검토가 필요한 항목은 별도 회신한다.",
        "본 문서는 관련 부서 회람 후 확정한다.",
    ],
    "memo": [
        "개정 시 변경 항목과 사유를 이 절에 누적한다.",
        "관련 문서와 상호 참조 번호를 남긴다.",
        "담당자 변경 이력을 함께 기록한다.",
    ],
}

# 실질 내용 문장. 채움 상투어와 달리 **구체적 사실**을 담는다.
# 왜(실측 2026-08-14). 무요소 문서를 넣었는데 unknown 이 늘기는커녕 줄었다. 원인은 요소
# 섹션만 빼고 그 자리를 절차 상투어로 채운 것이었다 — 고유어휘비 0.630 으로 학습셋에서
# 가장 빈약했고, 모델에게 그것은 "빈 문서" 였다. 빈 문서는 이미 S3(absent) 로 배웠다.
# 실문서는 "내용이 가득한데 요소만 없는" 문서다. 그 조합을 만들려면 실질 내용이 필요하다.
CONTENT = [
    "소성 최고온도 875도에서 42분 유지하고 승온은 3구간으로 나눈다.",
    "적층 매수 18매, 그린시트 두께 42마이크로미터로 관리한다.",
    "입고 로트당 시료 5점을 채취해 외관과 치수를 대조한다.",
    "가압은 610도 도달 시점에 시작해 유지 종료 12분 전에 해제한다.",
    "바인더 제거 구간은 분당 0.8도로 낮춰 잔류를 억제한다.",
    "부도 예측 모델은 20개 변수와 부문별 가중치로 산출한다.",
    "세그먼트 A는 구매 이력 3년치 분석에서 미충족 수요가 확인됐다.",
    "인수 대상의 재무·기술·인력·법률 리스크를 항목별로 평가했다.",
    "협력사별 중점 관리 항목은 입고 이력과 불량률을 함께 본다.",
    "시험은 조건을 바꿔 가며 400회 반복했고 실패 원인을 분류했다.",
    "전력 소비는 승온 구간에서 전체의 절반 이상을 차지한다.",
    "설비 2호기 이후 라인에 적용하고 1호기는 별도 사양을 따른다.",
    "단가는 물량 구간별로 나누고 예외 승인선을 따로 둔다.",
    "수율은 최근 3분기 평균 대비 상승했고 편차가 줄었다.",
    "조달 경로는 주 경로와 예비 경로를 나눠 관리한다.",
    "인증 준비는 서류·시험·실사 순으로 진행하고 일정을 맞춘다.",
    "회수 대상은 로트 번호로 특정하고 유통 단계까지 추적한다.",
    "개정 3차에서 승온 곡선과 가압 시점이 이전 차수와 달라졌다.",
]

NL = chr(10)
DEPTS = ["소재개발팀", "품질팀", "생산기술팀", "구매팀", "영업기획팀", "설비운영팀"]
STYLE_BULLET = {"bullet", "numbered"}

# 요소 섹션이 **주제는 언급하되 존재도 부재도 단언하지 않는** 문장. unknown 의 재료다.
# 회원사 실문서 다수가 이 모습이고, S1/TS 미탐이 숨는 자리도 여기다. 이 유형이 없으면
# 게이트는 '입증된 S3' 와 '아무 말 없는 S3' 를 구별하는 법을 배우지 못한다.
NEUTRAL_FACTOR = {
    "secrecy": [
        "자료의 취급 범위는 관련 부서 협의에 따른다.",
        "배포와 관련한 사항은 담당자가 별도로 안내한다.",
        "문서 성격은 업무 진행 상황에 따라 달라질 수 있다.",
    ],
    "value": [
        "업무상 활용 방안은 후속 회의에서 논의한다.",
        "본 내용의 쓰임은 부서별 상황에 따라 다르다.",
        "활용 범위에 대해서는 추가 협의가 필요하다.",
        "적용 대상은 담당 부서가 판단한다.",
        "관련 효과는 운영 결과를 보고 정리한다.",
        "활용 사례는 별도 문서로 취합한다.",
        "쓰임새는 사업 계획과 함께 검토한다.",
    ],
    "management": [
        "보관 방식은 부서 관행에 따른다.",
        "문서 취급에 관한 사항은 담당자가 정한다.",
        "관리 방법은 별도 안내를 참고한다.",
    ],
}


def _rng(seed: str) -> random.Random:
    """문자열 시드 -> 결정적 난수. 재실행 시 같은 산출물이 나와야 한다."""
    return random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16))


def _factor_label(factor: str, code: int | None, rng: random.Random, *, near_miss: bool,
                  rot: int = 0, split: str = "train") -> FactorLabel:
    """요소 코드 -> 3상태 라벨. None 이면 unknown(주제만 언급, 단언 없음).

    문장은 **문서별 rng** 로 뽑는다. 슬롯 번호로 순환 배정해 봤더니 오히려 나빴다 —
    S1 은 조합이 220 하나뿐이라 그 등급 문서가 한 위상에 정렬되고, 특정 문장이 S1 에만
    실려 tell 이 됐다(실측: 순환 tell 3종/0.035 vs 무작위 1종/0.008). rot 은 위상만 흔든다.
    """
    if code is None:
        return FactorLabel(state=UNKNOWN, direction="불명", reason="no_claim_in_body")
    if code == 0:
        pool = sentences_for(factor, PROVEN_ABSENT, None, split)
        span = rng.choice(pool)
        if near_miss:
            # 함정: 부재처럼 보이는 문장을 **추가로** 넣되 상태는 바꾸지 않는다.
            # 모델이 어휘가 아니라 문장 전체를 읽어야 정답에 닿는다.
            trap, kind = rng.choice(near_miss_for(factor, split))
            span = f"{span} {trap}"
            return FactorLabel(state=PROVEN_ABSENT, span=span, direction="부재",
                               reason=f"absence_proven_with_{kind}_trap")
        return FactorLabel(state=PROVEN_ABSENT, span=span, direction="부재",
                           reason="absence_proven")
    return FactorLabel(state=PRESENT, level=code, span=rng.choice(sentences_for(factor, PRESENT, code, split)),
                       direction="존재", reason=f"present_{code}")


def _render(form: dict, labels: dict[str, FactorLabel], rng: random.Random, idx: int,
            lineage: str = "prose", inline: bool = False, split: str = "train") -> str:
    """형태 정의에 따라 본문을 조립한다. 섹션 제목은 형태에서 오고 등급을 말하지 않는다."""
    bullet = form["style"] in STYLE_BULLET
    # 분량은 문서마다 다르고 **등급과 무관**하게 뽑는다. rng 는 doc_id 에서만 오므로
    # 등급 정보가 들어갈 경로가 없다.
    # 분량 값이 몇 개뿐이면 문서 길이가 이산적으로 뭉쳐 같은 길이 문서가 대량 생기고,
    # 길이-only 1NN 이 그 뭉치를 타고 등급을 맞힌다(실측: 규모 확대 시 0.310 -> 0.434).
    # 값을 연속에 가깝게 흩고 섹션마다 다시 뽑아 정확히 같은 길이가 나오기 어렵게 한다.
    verbosity = rng.randint(1, 3)
    padding = rng.randint(0, 8)
    head = form["header"].format(
        docnum=f"{form['id'][:2].upper()}-{2600 + idx % 90:04d}",
        rev=rng.randint(1, 4), dept=rng.choice(DEPTS),
        date=f"2026-{rng.randint(1,9):02d}-{rng.randint(1,28):02d}",
        owners=rng.randint(2, 9),
    )
    out = [f"# {form['title']}", "", head, ""]
    for num, (title, kind) in enumerate(form["sections"], 1):
        out.append(f"{num}. {title}" if bullet else f"## {title}")
        out.append("")
        fkey: str | None = None
        if kind.startswith("factor:"):
            factor = kind.split(":", 1)[1]
            fkey = factor
            lab = labels[factor]
            # unknown 은 span 이 없다(스키마 계약). 주제만 언급하는 중립 문장을 넣는다 —
            # 섹션을 비우면 '섹션 부재'가 곧 tell 이 된다.
            if lab.state == UNKNOWN and rng.random() < 0.6:
                # [무언급 unknown] 실측 2026-08-14. 지금 unknown 은 "배포와 관련한 사항은
                # 담당자가 별도로 안내함" 같은 **중립 문구**로 학습된다. 그런데 실문서
                # (M&A 실사 보고서 등)에는 중립 문구조차 없다 — 내용만 있다. 모델은
                # "중립 문구 = unknown" 을 배웠으므로 그 문구가 없으면 unknown 을 못 내고
                # absent 로 답한다. 그것이 곱셈 규칙에서 등급을 무너뜨린다.
                # 그래서 unknown 의 60% 는 요소 섹션에 **아무 언급도 없이** 내용만 넣는다.
                body = " ".join(rng.sample(CONTENT, k=min(2 + verbosity, len(CONTENT))))
            elif lab.span and inline:
                # [삽입형] 실문서는 요소를 별도 문장으로 쓰지 않고 내용 문장 안에 짧게
                # 끼워 넣는다("… 모델 (외부 미공개)"). 경화42 고등급 26건을 100%
                # 과소분류한 원인이 이 형태를 학습에서 못 봤기 때문이다.
                # 요소 자리에 채움 서술을 깔고 그 위에 짧은 진술을 붙인다.
                base = " ".join(rng.sample(CONTENT, k=2))
                body = embed_inline(base, factor, lab.state, lab.level, rng, split)
            else:
                body = lab.span or rng.choice(NEUTRAL_FACTOR[factor])
            # 중립 문장이 unknown 에만 나오면 그 자체가 등급 tell 이 된다(실측: tell 4종).
            # 상태와 무관하게 섞어 넣어 '중립 문장 존재 = unknown' 이라는 신호를 끊는다.
            if lab.span and rng.random() < 0.35:
                body = f"{body} {rng.choice(NEUTRAL_FACTOR[factor])}"
        else:
            if kind in ("numbers", "observe", "procedure"):
                # 실질 내용을 넣는다. 절차 상투어만 깔면 "빈 문서" 가 되고, 모델은 그것을
                # 이미 S3 로 배웠다(무요소 투입이 실패한 원인).
                body = " ".join(rng.sample(CONTENT, k=min(2 + verbosity, len(CONTENT))))
            else:
                body = " ".join(rng.sample(FILLER[kind], k=min(verbosity, len(FILLER[kind]))))
            # 길이를 등급과 무관하게 흔든다. 고정 길이면 같은 경계·같은 쪽 문서끼리
            # 뭉쳐 1NN 이 등급을 맞힌다(실측: 길이-only 0.637).
            for _ in range(max(0, padding + rng.randint(-2, 2))):
                pool = FILLER[rng.choice(list(FILLER))]
                body = f"{body} {rng.choice(pool)}"
        # 계보 문체를 입힌다. 사실은 그대로이고 어투·구조만 바뀐다.
        body = render_register(body, lineage, factor=fkey, seq=num)
        out.append(f"- {body}" if bullet and lineage == "prose" else body)
        out.append("")
    return "\n".join(out).strip() + "\n"


def _build(doc_id: str, form: dict, svm, *,
           near_miss: bool, idx: int, pair_id: str | None = None,
           varied: str | None = None, rot: int = 0, split: str = "train",
           lineage: str = "prose", inline: bool = False) -> dict:
    rng = _rng(doc_id)
    labels = {f: _factor_label(f, c, rng, near_miss=near_miss, rot=rot, split=split)
              for f, c in zip(FACTORS, svm)}
    doc = DocumentLabel(secrecy=labels["secrecy"], value=labels["value"],
                        management=labels["management"])
    return {
        "doc_id": doc_id,
        "form_id": form["id"],
        "document_type": form["title"],
        "text": _render(form, labels, rng, idx, lineage, inline, split),
        "label": doc.grade(),
        "label_source": "derived_from_factor_states",
        "factor_labels": {f: {k: v for k, v in vars(labels[f]).items() if v not in (None, "")}
                          for f in FACTORS},
        "svm": list(svm),
        "svm_worst_case": list(doc._triple(worst=True)),
        "s3_kind": doc.s3_kind(),
        "pair_id": pair_id,
        "varied_factor": varied,
        "has_language_trap": near_miss,
        "inline_form": inline,
        "lineage": lineage,
        "schema": "v8-3state-1",
    }


def generate_no_factor(forms: list[dict], *, count: int, split: str) -> list[dict]:
    """**요소 섹션이 아예 없는** 문서 — 세 요소가 전부 unknown 이다.

    왜(실측 2026-08-14). 요소 모델을 실데이터로 재니 400건 중 399건이 S3 로 떨어졌고
    원인은 secrecy=absent 369건이었다. 근거가 없는데 "공개되었음이 입증됨" 이라고 단언한다.

    우리 학습셋은 unknown 문서조차 '자료 성격'·'공유 범위' 같은 요소 섹션을 갖고 있어서,
    모델이 본 것은 전부 "요소 얘기가 나오는 문서" 였다. 실제 업무문서는 기술 내용만 쓰고
    요소를 언급하지 않는다. 그 유형이 학습에 없으니 모델이 absent 로 답할 수밖에 없었다.

    이 문서들은 요소 자리가 없다. 정답은 세 요소 전부 unknown 이고, 서빙 등급은 보수적
    완성으로 TS 가 된다 — **모르면 높게 보고 검수로 보낸다.** 자동확정 후보에서는 빠진다.
    """
    rows: list[dict] = []
    for i in range(count):
        doc_id = f"v8-{split}-nf-{i:04d}"
        prng = _rng(f"assign-{doc_id}")
        rows.append(_build(doc_id, prng.choice(forms), (None, None, None),
                           near_miss=False, idx=i, rot=i,
                           lineage=prng.choice(LINEAGES)))
    return rows


def generate_unsignaled(forms: list[dict], *, count: int, split: str,
                        pool_split: str = "train") -> list[dict]:
    """S3-무신호형 — 요소 일부 또는 전부가 unknown 인 문서.

    등급은 S3 로 떨어지지만(unknown 을 0 으로 보므로) 보수적 완성에서는 S3 가 아니다.
    따라서 자동확정 후보가 아니며 **반드시 검수로 가야 한다.** 학습셋에 이 유형이 없으면
    게이트는 '부재가 입증된 S3' 와 '아무 말 없는 S3' 를 구별할 수 없고, 회원사 실문서
    다수가 후자라 미탐이 그대로 열린다.

    unknown 조합은 세 요소 중 1~3개다. 나머지 요소는 낮은 수준(0~1)으로 둬서 등급이 S3 에
    머물게 한다 - 고등급이 섞이면 라벨 노이즈가 된다.
    """
    rows: list[dict] = []
    patterns = [
        (None, None, None), (None, None, 1), (None, 1, None), (1, None, None),
        (None, 0, 1), (0, None, 1), (1, 0, None), (None, None, 0),
        # unknown 이 S3 에만 몰리면 '중립 문장 = S3' 가 된다. s=v=2 이고 m 이 unknown 이면
        # (2,2,0) -> S1 이라 고등급에도 unknown 이 들어간다. 이 패턴이 tell 을 끊는다.
        (2, 2, None), (2, 2, None), (2, 1, None), (1, 2, None),
    ]
    for i in range(count):
        doc_id = f"v8-{split}-un-{i:04d}"
        prng = _rng(f"assign-{doc_id}")
        form = prng.choice(forms)
        svm = patterns[i % len(patterns)]
        row = _build(doc_id, form, svm, near_miss=(i % 3 == 0), idx=i, rot=i,
                     split=pool_split, lineage=prng.choice(LINEAGES))
        rows.append(row)
    return rows


def generate_provable_s3(forms: list[dict], *, count: int, split: str,
                         pool_split: str = "train") -> list[dict]:
    """S3-입증형 보강 — 부재가 입증된 문서.

    경계 표집만으로는 proven_absent 가 6~8% 에 그친다. 그런데 S3 자동확정의 **유일한**
    근거가 이것이라(§보수적 완성 규칙) 얇으면 게이트가 열리지 않는다. secrecy 또는 value
    가 0 인 조합만 골라 채운다 - management 단독 0 은 s·v 가 2 미만일 때만 S3 라 별도다.
    """
    patterns = [
        (0, 1, 1), (0, 2, 2), (0, 0, 1), (1, 0, 1), (2, 0, 2), (0, 1, 2),
        (1, 0, 2), (0, 2, 0), (2, 0, 0), (0, 0, 0), (0, 2, 1), (1, 0, 0),
    ]
    rows: list[dict] = []
    for i in range(count):
        doc_id = f"v8-{split}-pv-{i:04d}"
        prng = _rng(f"assign-{doc_id}")
        rows.append(_build(doc_id, prng.choice(forms), patterns[i % len(patterns)],
                           near_miss=(i % 2 == 0), idx=i, rot=i,
                           split=pool_split, lineage=prng.choice(LINEAGES)))
    return rows


def generate(forms: list[dict], *, per_boundary: int, split: str,
             pool_split: str = "train") -> list[dict]:
    """경계마다 counterfactual 쌍을 만들고 형태를 라운드로빈으로 독립 배정한다."""
    edges = adjacent_boundaries()
    high = {"TS", "S1"}
    # 고등급이 걸린 경계에 예산을 더 준다. 1차 목표가 미탐 최소화이고, S1 은 조합이 220
    # 하나뿐이라 균등 배분하면 구조적으로 과소표집된다(기존 진단: S1 약점은 데이터 양).
    # 가중 4배. S3-입증형 보강(자동확정 근거)이 전부 S3 라 고등급이 희석되는데,
    # 1차 목표가 미탐 최소화라 고등급 표본이 줄면 안 된다(실측: 보강 후 고등급 34% -> 28.5%).
    quota = [per_boundary * (4 if ({g1, g2} & high) else 1) for _, _, g1, g2 in edges]

    # 경계를 **교대로** 돈다. 한 경계가 연속 슬롯을 차지하면 문장 순환이 그 경계의
    # 등급에 정렬돼 문장 자체가 tell 이 된다(실측: 연속 배치 시 tell 3종, 교대 시 감소).
    rows: list[dict] = []
    slot = 0
    for k in range(max(quota)):
        for e_i, (a, b, _, _) in enumerate(edges):
            if k >= quota[e_i]:
                continue
            sa = tuple(int(c) for c in a)
            sb = tuple(int(c) for c in b)
            varied = FACTORS[next(i for i in range(3) if sa[i] != sb[i])]
            # 형태·계보는 **쌍 단위**로 정한다. 쌍의 양쪽이 다른 형태나 다른 문체를 받으면
            # "한 요소만 다르다"는 counterfactual 계약이 깨져 경계 검정이 오염된다.
            # 슬롯 나머지로 정하면 경계 구조와 정렬된다(실측 Cramer V 형태 0.16 · 계보 0.12).
            # 쌍 식별자 해시로 뽑아 경계와 무관하게 만든다.
            pair = f"{split}-p{e_i:02d}-{k:03d}"
            prng = _rng(f"assign-{pair}")
            form = prng.choice(forms)
            lineage = prng.choice(LINEAGES)
            slot += 1
            trap = (k % 2 == 1)               # 절반에 언어 함정
            for side, svm in (("a", sa), ("b", sb)):
                rows.append(_build(f"v8-{split}-{e_i:02d}-{k:03d}{side}", form, svm,
                                   near_miss=trap, idx=slot, pair_id=pair,
                                   varied=varied, rot=slot, split=pool_split,
                                   lineage=lineage, inline=(k % 2 == 0)))
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="v8 데이터 생성 — 형태 × 경계 × 언어")
    ap.add_argument("--per-boundary", type=int, default=8, help="경계당 쌍 수(문서는 x2)")
    ap.add_argument("--holdout-per-boundary", type=int, default=0,
                    help="0 이면 홀드아웃 미생성. 판정면 만들 때만 켠다")
    # 조기종료 검증면. v6 val 은 학습셋과 같은 코퍼스라 1 epoch 만에 정확도 1.000 이 나와
    # 아무 신호도 주지 못했다. 학습 형태 중 하나를 통째로 떼어 **미관측 형태**로 만든다.
    # 최종 판정면(holdout_forms)은 형태와 표현이 둘 다 새로우므로 여기에 쓰지 않는다.
    ap.add_argument("--dev-form", default="work_manual",
                    help="이 형태는 학습에서 빼고 dev.jsonl 로 낸다. 빈 문자열이면 미분리")
    # 2차 판정면 — 1차에서 조건을 통과한 뒤 **여기서 다시 재는 것**이 과적합 여부를 가른다.
    # 형태 6종이 전부 학습·1차판정 어디에도 안 나온 것이고 골격도 더 크게 흔들었다.
    ap.add_argument("--holdout2-per-boundary", type=int, default=0,
                    help="0 이면 2차 판정면 미생성")
    ap.add_argument("--no-factor", type=int, default=600,
                    help="요소 섹션이 없는 문서 수 — 실문서 다수가 이 모습이다")
    ap.add_argument("--provable", type=int, default=240,
                    help="S3-입증형 보강 건수 — 자동확정 후보의 유일한 근거라 얇으면 안 된다")
    ap.add_argument("--unsignaled", type=int, default=96,
                    help="S3-무신호형 건수 — 회원사 실문서 다수가 이 유형이다")
    ap.add_argument("--out-dir", default="datasets/v8")
    args = ap.parse_args()

    sanity_check()
    probs = sentence_audit()
    if probs:
        print("문장 풀 감사 실패 - 생성 중단")
        for p in probs:
            print(f"  - {p}")
        return 1

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    fit_forms = TRAIN_FORMS
    dev_forms: list[dict] = []
    if args.dev_form:
        dev_forms = [f for f in TRAIN_FORMS if f["id"] == args.dev_form]
        if not dev_forms:
            print(f"--dev-form '{args.dev_form}' 가 학습 형태에 없다")
            return 1
        fit_forms = [f for f in TRAIN_FORMS if f["id"] != args.dev_form]

    train = generate(fit_forms, per_boundary=args.per_boundary, split="tr")
    train += generate_unsignaled(fit_forms, count=args.unsignaled, split="tr")
    train += generate_provable_s3(fit_forms, count=args.provable, split="tr")
    # 요소 섹션이 없는 문서 — "근거 없으면 unknown" 을 배우게 한다(실데이터 진단 대응)
    train += generate_no_factor(NO_FACTOR_FORMS, count=args.no_factor, split="tr")
    (out / "train.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in train) + "\n", encoding="utf-8")
    print(f"학습 {len(train)}건 (형태 {len(fit_forms)}종 · 경계 {len(adjacent_boundaries())}개)")

    if dev_forms:
        dev = generate(dev_forms, per_boundary=max(2, args.per_boundary // 4), split="dv")
        dev += generate_unsignaled(dev_forms, count=max(8, args.unsignaled // 4), split="dv")
        dev += generate_provable_s3(dev_forms, count=max(8, args.provable // 4), split="dv")
        (out / "dev.jsonl").write_text(
            NL.join(json.dumps(r, ensure_ascii=False) for r in dev) + NL, encoding="utf-8")
        print(f"dev {len(dev)}건 (형태 {args.dev_form} — 학습에서 제외)")

    if args.holdout2_per_boundary:
        # ⚠ pool_split 은 반드시 "holdout2" 다. 처음에 "holdout" 을 그대로 써서 1차 판정면과
        # 근거문장이 108/109 겹쳤다 — 형태만 새롭고 표현은 같아 2차가 새 증거가 되지 못했다.
        h2 = generate(HOLDOUT2_FORMS, per_boundary=args.holdout2_per_boundary, split="h2",
                      pool_split="holdout2")
        h2 += generate_provable_s3(HOLDOUT2_FORMS, count=max(64, args.provable // 3),
                                   split="h2", pool_split="holdout2")
        h2 += generate_unsignaled(HOLDOUT2_FORMS, count=max(64, args.holdout2_per_boundary * 10),
                                  split="h2", pool_split="holdout2")
        (out / "holdout2_forms.jsonl").write_text(
            NL.join(json.dumps(r, ensure_ascii=False) for r in h2) + NL, encoding="utf-8")
        print(f"2차 판정면 {len(h2)}건 (형태 {[f['id'] for f in HOLDOUT2_FORMS]})")

    if args.holdout_per_boundary:
        # 보정면 — 판정면과 **프레임이 겹치지 않는** 미관측 프레임으로 만든다.
        # 온도는 스칼라 3개뿐이라 표본이 크지 않아도 되지만, 오차가 있어야 추정이 된다.
        cal = generate(HOLDOUT_FORMS, per_boundary=max(4, args.holdout_per_boundary // 3),
                       split="ca", pool_split="calib")
        cal += generate_provable_s3(HOLDOUT_FORMS, count=max(32, args.provable // 8),
                                    split="ca", pool_split="calib")
        cal += generate_unsignaled(HOLDOUT_FORMS, count=max(24, args.unsignaled // 8),
                                   split="ca", pool_split="calib")
        (out / "calib.jsonl").write_text(
            NL.join(json.dumps(r, ensure_ascii=False) for r in cal) + NL, encoding="utf-8")
        print(f"보정 {len(cal)}건 (판정면과 프레임 겹침 0)")

    if args.holdout_per_boundary:
        hold = generate(HOLDOUT_FORMS, per_boundary=args.holdout_per_boundary, split="ho",
                        pool_split="holdout")
        # 판정면에도 S3-무신호형이 있어야 한다. 없으면 "부재가 입증된 S3"와 "아무 말 없는
        # S3"를 구별하는 능력을 **측정할 수 없고**, S3 자동확정 정책 전체가 검증 없이 나간다.
        hold += generate_unsignaled(HOLDOUT_FORMS, count=max(64, args.holdout_per_boundary * 10),
                                    split="ho", pool_split="holdout")
        hold += generate_provable_s3(HOLDOUT_FORMS, count=max(64, args.provable // 3),
                                     split="ho", pool_split="holdout")
        (out / "holdout_forms.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in hold) + "\n", encoding="utf-8")
        print(f"홀드아웃 {len(hold)}건 (형태 {[f['id'] for f in HOLDOUT_FORMS]})")

    import collections
    for name, rows in (("학습", train),):
        g = collections.Counter(r["label"] for r in rows)
        f = collections.Counter(r["form_id"] for r in rows)
        print(f"\n{name} 등급 {dict(g)}")
        print(f"{name} 형태 {dict(f)}")
        s3 = collections.Counter(r["s3_kind"] for r in rows if r["s3_kind"])
        print(f"{name} S3 구성 {dict(s3)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
