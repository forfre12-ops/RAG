"""Build the first 300-candidate expansion batch after the pilot gate passes."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from build_proxy_gold_pilot_100 import (
    Case,
    _case_specific_appendix,
    _contextualize_standard_sentences,
    _grade_rationale,
    _repair_mojibake,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
TARGET = {"TS": 60, "S1": 75, "S2": 75, "S3": 90}

THEMES = (
    ("차세대 열관리 소재", "배합 조건·열전도 측정값·시편 이력", "연구개발"),
    ("고정밀 조립공정", "치구 형상·공정 순서·검증 로그", "생산기술"),
    ("신규 센서 모듈", "보정 계수·오차 범위·시험 기록", "제품개발"),
    ("현장 품질 대응", "불량 유형·재발 조건·조치 이력", "품질보증"),
    ("협력사 전환 검토", "단가 조건·공급 능력·평가 결과", "구매운영"),
    ("보안 접근 통제", "권한 목록·접속 이력·예외 승인", "정보보호"),
    ("운영 자동화 전환", "처리 기준·예외 흐름·성능 기록", "서비스운영"),
    ("고객지원 지식관리", "문의 유형·처리 기준·개선 이력", "고객성공"),
    ("사업 제안 검토", "원가 가정·수익 조건·의사결정 메모", "사업전략"),
    ("해외 시장 진입", "우선순위·규제 확인·투자 일정", "글로벌전략"),
    ("설비 예방정비", "점검 주기·고장 징후·정비 기록", "설비관리"),
    ("데이터 품질 개선", "오류 패턴·정제 규칙·검증 결과", "데이터운영"),
    ("신규 서비스 출시", "출시 순서·운영 기준·위험 검토", "서비스기획"),
    ("대외 협력 과제", "역할 분담·성과 기준·협의 경과", "대외협력"),
    ("규정 개정 검토", "적용 범위·예외 기준·개정 사유", "정책운영"),
)

# [2026-09-12] 등급 전용 어휘를 없앴다.
#
# 종전에는 문서종류 목록이 등급마다 완전히 달랐고, '확인 문제' 문장도 등급마다 하나로 고정이라
# **그 한 줄만 보고 등급을 맞힐 수 있었다.** 실측(후보 1,055건 · scripts/measure_grade_phrase_leak.py):
#     고정 문구만으로 등급 적중 85.3% · 문서종류만으로 85.4% · 되풀이 단서 하나로 91.4%
#     (기준선 32.4% = 가장 흔한 등급만 찍기)
# 정답 누출 세척을 통과한 판인데도 그랬다 — 세척은 아는 형태('## 등급 제안 사유' 절)만 지운다.
#
# 그래서 두 목록을 **등급 사이에 겹치게** 만든다. 문서종류는 인접한 두 등급, 확인 문제는 세 등급에
# 걸친다. ⚠ 상관을 0 으로 만들지는 않는다 — 실제로도 '공개 안내문'은 공개 등급에 많다. 목표는
# **단일 단서로 등급이 결정되는 상태를 없애는 것**이고, 그 상태는 시험이 지킨다
# (tests/test_golden_generator_no_grade_tell.py).
_GRADE_ORDER = ("TS", "S1", "S2", "S3")

_FORM_POOL = (
    "핵심조건 교차검증 기록", "비공개 시험결과 보고", "접근통제 영향평가서", "재현성 분석 노트",
    "사업성 사전검토 메모", "협상조건 검토서", "우선순위 조정안", "내부 의사결정 기록",
    "전환비용 분석서", "운영개선 결과서", "현장 조치 기록", "지원절차 검토서",
    "변경영향 분석서", "품질점검 보고", "공개 안내문", "이용절차 안내",
    "정기 공지 초안", "자주 묻는 질문 정리", "서비스 소개자료", "행사 안내문",
)

_ISSUE_POOL = (
    "검증 조건의 조합이 외부 재현 또는 우회 경로로 연결될 수 있는지 확인",
    "보관 위치와 접근 권한이 실제로 제한되어 있는지 확인",
    "내부 선택 기준이 협상력 또는 경쟁상 위치에 영향을 주는지 확인",
    "외부 제공 이력과 재공유 경로가 문서에 남아 있는지 확인",
    "운영 기준의 제한 공유 범위와 예외 처리 필요성을 확인",
    "예외 승인 절차가 기록으로 남아 있는지 확인",
    "공개 안내 범위를 넘어서는 내부 정보가 섞이지 않았는지 확인",
    "기록된 수치·조건이 공개 자료와 겹치는 범위를 확인",
)

# 창(window)을 등급마다 조금씩 밀어 겹치게 만든다. 둘 다 **세 등급**에 걸친다.
#   문서종류 20개 · 등급마다 15개  → 한 종류가 세 등급에 나온다
#   확인 문제  8개 · 등급마다  6개  → 한 문장이 세 등급에 나온다
# ⚠ 처음에는 문서종류를 두 등급(창 10개)으로 뒀는데, 뽑는 위치가 목록 앞쪽에만 머물러
#   실제 생성물에서는 등급별로 거의 안 겹쳤다 — 시험이 "문서종류만으로 95% 적중"으로 잡았다.
DOCUMENT_FORMS = {
    g: tuple(_FORM_POOL[(5 * i + k) % len(_FORM_POOL)] for k in range(15))
    for i, g in enumerate(_GRADE_ORDER)
}
ISSUES = {
    g: tuple(_ISSUE_POOL[(2 * i + k) % len(_ISSUE_POOL)] for k in range(6))
    for i, g in enumerate(_GRADE_ORDER)
}


def make_cases(batch_number: int) -> list[Case]:
    cases: list[Case] = []
    for grade, per_theme in (("TS", 4), ("S1", 5), ("S2", 5), ("S3", 6)):
        forms = DOCUMENT_FORMS[grade]
        for theme_index, (subject, evidence, owner) in enumerate(THEMES, start=1):
            for variant in range(per_theme):
                # 주제 번호까지 섞어 목록 전체에 퍼지게 한다. variant 만 쓰면 목록 앞쪽 몇 개만
                # 쓰여 등급 사이 겹침이 생기지 않는다(2026-09-12 시험이 잡은 결함).
                form = forms[(theme_index * 7 + variant + batch_number - 1) % len(forms)]
                phase = ("초기 확인", "변경 검토", "예외 확인", "재검토")[
                    (theme_index + variant + batch_number - 1) % 4
                ]
                title = f"{subject} {form} {phase} B{batch_number}-{theme_index:02d}-{variant + 1:02d}"
                issues = ISSUES[grade]
                cases.append(Case(
                    grade=grade,
                    kind=form,
                    title=title,
                    subject=subject,
                    # 등급마다 하나로 고정하지 않는다 — 고정하면 그 한 줄이 등급을 알려 준다.
                    issue=issues[(theme_index + variant + batch_number) % len(issues)],
                    evidence=evidence,
                    owner=owner,
                ))
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--replace-batch", action="store_true", help="rewrite only unreviewed batch files")
    args = parser.parse_args()
    existing = list(OUT.glob("*.metadata.json"))
    if args.replace_batch:
        if len(existing) not in {400, 700, 1000}:
            raise RuntimeError(f"unexpected candidate count for batch replacement: {len(existing)}")
    else:
        expected_existing = 100 + (args.batch - 1) * 300
        if len(existing) != expected_existing:
            raise RuntimeError(f"batch {args.batch} assumes {expected_existing} candidates; found {len(existing)}")
    cases = make_cases(args.batch)
    planned = Counter(case.grade for case in cases)
    if dict(planned) != TARGET or len(cases) != 300:
        raise RuntimeError(f"invalid batch composition: {planned}")
    prepared = []
    serials = Counter()
    for case in cases:
        serials[case.grade] += 1
        doc_id = f"GOLD-B{args.batch}-{case.grade}-{serials[case.grade]:03d}"
        document = _repair_mojibake(_contextualize_standard_sentences(
            _case_specific_appendix(case).strip() + "\n" + _grade_rationale(case).strip() + "\n",
            case,
        ))
        if len(document) < 3200:
            raise RuntimeError(f"document too short: {doc_id} {len(document)}")
        metadata = {
            "doc_id": doc_id,
            "intended_label": case.grade,
            "document_origin": "synthetic",
            "document_type": _repair_mojibake(case.kind),
            "authoring_method": "codex_direct_case_expansion_v1",
            "requires_manual_audit": True,
            "candidate_status": "proposed",
            "claim_scope": "fictional Proxy Gold candidate only; not human-reviewed gold and not customer-real evidence",
            "expansion_batch": f"batch_{args.batch:03d}_of_003",
            "quality_contract": {
                "minimum_characters": 3200,
                "required_elements": ["case context", "evidence", "exception", "grade rationale", "next step"],
            },
        }
        prepared.append((OUT / f"{doc_id}.md", OUT / f"{doc_id}.metadata.json", document, metadata))
    collisions = [str(path) for doc, meta, _, _ in prepared for path in (doc, meta) if path.exists()]
    if collisions and not args.replace_batch:
        raise FileExistsError("candidate collision: " + ", ".join(collisions))
    if args.replace_batch:
        decisions = OUT / "candidate_decisions.jsonl"
        if decisions.exists() and any(f"GOLD-B{args.batch}-" in line for line in decisions.read_text(encoding="utf-8").splitlines()):
            raise RuntimeError("refusing to replace a batch with review history")
    for doc, meta, document, metadata in prepared:
        doc.write_text(document, encoding="utf-8")
        meta.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rewritten" if args.replace_batch else "created": len(prepared), "by_grade": dict(planned)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
