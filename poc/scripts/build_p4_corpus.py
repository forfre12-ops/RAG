"""P4 평가용 합성 문서 30종 코퍼스 생성기.

발주처 실데이터 0건 환경에서도 P4를 돌릴 수 있도록 .txt/.md 문서를 합성.
실제 HWP/DOCX/PDF 추출 평가는 발주처 데이터 확보 후 같은 스크립트로 재실행.

구조:
  datasets/p4_corpus/
    plain/ 10건 (.txt/.md) — 한국어 공문 스타일
    boilerplate/ 10건 — 페이지번호·머리말 노이즈 포함
    multilingual/ 10건 — 한/영 혼재
"""

from __future__ import annotations

import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "datasets" / "p4_corpus"

PLAIN_TEMPLATES = [
    "{title}\n\n1. 개요\n  본 문서는 {topic}에 관한 내부 검토 자료입니다.\n\n"
    "2. 주요 내용\n  {body}\n\n3. 결론\n  {concl}\n",
    "[{title}]\n\n수신: 관계자\n참조: 관리부서\n\n제목: {title}\n\n"
    "{body}\n\n붙임: 별첨 자료 1부. 끝.\n",
    "○ 안건명: {title}\n○ 작성부서: 기술기획팀\n○ 작성일: 2026-05-26\n\n"
    "가. 검토 배경\n{body}\n\n나. 검토 결과\n{concl}\n",
]

BOILERPLATE_TAILS = [
    "\n\n- 3 -\n페이지 1 of 5\n",
    "\n\nPage 2 of 10\n\n - 12 - \n",
    "\n\n<EOP>\n123\n",
]

TITLES = [
    "신규 반도체 공정 기술 검토 보고서",
    "AI 영업비밀 분류 시스템 도입 계획",
    "2026년도 R&D 투자 우선순위 분석",
    "차세대 배터리 음극재 합성 공정",
    "고객 데이터 활용 마케팅 전략 초안",
    "M&A 대상 기업 실사 보고서",
    "공급망 보안 위협 분석",
    "원천 알고리즘 특허 출원 계획",
    "임원 인사 이동 검토안",
    "분기별 매출 실적 요약",
]

TOPICS = ["기술자료", "영업전략", "원가구조", "고객정보", "특허출원", "M&A 계획", "임원 인사", "공정 노하우"]

BODIES = [
    "본 검토는 시장 동향과 내부 자원을 종합 분석하여 도출된 전략적 방향을 제시한다. "
    "특히 경쟁사 대비 우위 확보를 위한 핵심 차별화 요소를 식별하고, 이를 보호하기 위한 관리 체계를 함께 제안한다.",
    "관련 부서 협의 결과 다음과 같은 조치가 필요한 것으로 판단된다. "
    "첫째, 영업비밀 등급 분류 체계의 정비, 둘째, 접근 통제 강화, 셋째, 외부 유출 방지 시스템 도입.",
    "본 자료는 대외비로 분류되며 무단 복제·배포를 금한다. 자료의 활용은 사내 정책에 따른다.",
]

CONCLS = [
    "이상의 검토 결과를 바탕으로 차기 회의에서 최종안을 확정할 예정이다.",
    "본 안건은 임원 회의 의결을 거쳐 시행한다.",
    "필요 시 외부 자문을 추가로 의뢰한다.",
]


def main() -> int:
    random.seed(42)
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "plain").mkdir(exist_ok=True)
    (ROOT / "boilerplate").mkdir(exist_ok=True)
    (ROOT / "multilingual").mkdir(exist_ok=True)

    created = 0
    for i in range(10):
        title = TITLES[i % len(TITLES)]
        topic = random.choice(TOPICS)
        body = random.choice(BODIES)
        concl = random.choice(CONCLS)
        text = random.choice(PLAIN_TEMPLATES).format(title=title, topic=topic, body=body, concl=concl)
        (ROOT / "plain" / f"doc_{i:02d}.txt").write_text(text, encoding="utf-8")
        created += 1

    for i in range(10):
        title = TITLES[i % len(TITLES)]
        topic = random.choice(TOPICS)
        body = random.choice(BODIES)
        concl = random.choice(CONCLS)
        text = random.choice(PLAIN_TEMPLATES).format(title=title, topic=topic, body=body, concl=concl)
        text = text + random.choice(BOILERPLATE_TAILS)
        (ROOT / "boilerplate" / f"doc_{i:02d}.md").write_text(text, encoding="utf-8")
        created += 1

    for i in range(10):
        title = TITLES[i % len(TITLES)]
        en = "This document contains confidential business information. Unauthorized disclosure is prohibited."
        body = random.choice(BODIES)
        text = f"# {title}\n\n## Korean\n{body}\n\n## English\n{en}\n"
        (ROOT / "multilingual" / f"doc_{i:02d}.md").write_text(text, encoding="utf-8")
        created += 1

    print(f"[build_p4_corpus] created {created} files under {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
