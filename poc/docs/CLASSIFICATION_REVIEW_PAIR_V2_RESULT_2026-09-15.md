# 실제 문서 검수 v2 비교·조정 제안 결과 — 2026-09-15

**R3 비교·불일치 기록·조정 제안 검증을 구현했다.**
R1 입력 검사 → R2 제출 검사 → R3 두 제출 비교/조정 제안의 로컬 도구 준비가 완료됐다.
실제 고객 문서 검수·공식 조정·GOLD 확정이나 분류 정확도 개선 측정의 완료는 아니다.

## 구현 결과

- `src/koipa/review_pair_v2.py`: 같은 판본/배정의 두 제출 비교, 분모 구분, 조정 제안 결합 검증.
- `scripts/compare_document_reviews.py`: 비교/조정 검사 CLI, 빈 조정 양식, 가상 데모/스키마, 입력 지문.
- `tests/data_quality/test_review_pair_v2.py`: 신규101개 회귀.
- [상세 계약](REAL_DOCUMENT_REVIEW_PAIR_V2_CONTRACT_DRAFT.md): 비교 규칙·집계 분모·원인 주장·조정 조건·권위 한계.

중요한 보완:

1. 서로 다른 정책/view/문서판본의 제출이나 같은 slot 두 개를 비교 자료로 받지 않는다.
   두 배정 범위가 다르면 교집합만 골라 성공률을 계산하지 않고 거절한다.
2. 등급, 보류 상태, 규칙, 인용 근거, 이유의 차이를 별도로 기록한다.
   보류 null끼리의 일치를 4등급 일치로 세지 않고 미제출도 별도 집계한다.
3. 동일인/노출/AI 보조 신고를 남기며 전체 완료 쌍과 자기신고상 조건 충족 쌍의 통계를 구분한다.
4. 두 검수자가 같은 오답을 내도 정책 불일치 경고는 유지한다. 일치율은 정답 정확도가 아니다.
5. 조정 제안은 양쪽 제출 배치/행 해시에 결합된다. 설명만 바뀌어도 기존 제안 재사용을 거절한다.
6. 모든 쟁점에 조정자의 응답을 요구하되 원인이 미확정이면 undetermined로 둘 수 있다.
   입력/정책을 바꿔야 한다고 요청하면 기존 판본에 등급 후보를 확정적으로 제안하지 못한다.
7. 새 제안이 있어도 원래 불일치·노출 이력을 지우지 않는다. GOLD/학습/자동확정 허가는 항상 false다.

## 현재 검증 결과

- 전용 회귀 **101 passed**.
- 확대 회귀 **993 passed**, 실패/오류/skip0. 이전892개+신규101개이며 과거 수와 합산하지 않는다.
- SQLAlchemy/psycopg/torch/transformers import 차단 독립 회귀 **635 passed**. 위993개에 포함된다.
- 새 Python3개 Ruff 통과, Git diff 공백 검사 통과(기존 CRLF 정규화 경고는 별도).
- CLI 빈 양식→비교→제안 검사, 같은 정책 규칙ID의 가상 S1/S2 의미 차이, 입력 변경/덮어쓰기 방지 시험 통과.
- 기존 소스/설계/원장26개와 원본JSONL406개 불변, 원본 추가0.
- 기존 참조팩 manifest 대상13/91개 파일 지문 유지.
- D01~D08 미정8개, 준비도 HOLD/종료2 유지.

처음991/독립633개 통과 후 원인 미확정·부분 조정 제출 시험2개를 추가했다.
최종 결과는 `*-final` 파일이다. 중간 결과를 삭제하지 않았고 최종과 중복 합산하지 않는다.
과거 9/14 기준의 policy_engine.py 변경은 여전히 CHANGED_REQUIRES_REVIEW/종료1이며,
이번 시작점 대비 변경은 없다. 과거 기준선을 다시 만들거나 보존 오류를 성공으로 바꾸지 않았다.

실제 문서0·사람 제출0·공식 조정0·GOLD0·재학습0. 가상 테스트 제출은 실제 서명이 아니다.
전체 저장소/원격 CI/실제 인증·조직 격리·운영 저장/부하 검증은 이번 범위에 포함되지 않는다.

## 파일과 재현

`poc/reports/CLASSIFICATION_REVIEW_PAIR_V2_20260915/` (Git ignore 대상):

- tests-final.xml / isolated-tests-final.xml: 최종 회귀.
- demo-final.json / pair-final.schema.json: 가상 미제출2정책 검사/입력 구조. 모든 일치율 분모0/rate=null.
- workspace_preservation.json: 과거 원본/기준 대조.
- evidence.json: 소스·산출물 지문과 보존/실행 범위.
- tests.xml / isolated-tests.xml / demo.json / pair.schema.json: 이번 턴의 중간 검사 이력.

`F:\antigravity\rag\poc`에서:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B scripts/compare_document_reviews.py --demo
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_review_pair_v2.py -q
```

확대 회귀는 [R1/R2 결과의 명령](CLASSIFICATION_REVIEW_V2_RESULT_2026-09-15.md)과 같은 대상이다.
`tests/data_quality`에 새101개가 포함된다. 종료0 데모와 회귀 통과를 고객 정확도/승인으로 해석하지 않는다.

## 다음 작업

실제 완성도를 검증할 다음 핵심은 **승인된 정책·사용 허가 문서·검수자 배정을 받아 소수 실제 문서로
입력→두 제출→조정 제안 흐름을 점검하는 것**이다. D01~D08과 공급18항목 확인 없이 실제 정답지를 만들지 않는다.

그 입력과 독립적으로 구현 가능한 기술 묶음은 실제 추출 결과의 원본/추출/정규화 판본·완전성 receipt 연결부다.
앞선 설계대로 저장 전 오프라인 연결 검증부터 진행할 수 있다. 실제 DB/조직 인증/R5 확정 연결은 별도다.
추가 합성 정답 대량 제작·재학습·운영 모델 교체는 재개하지 않았다.

워크스페이스 MEMORY/인계에 저장했다. 기존 R1/R2·합성40건·운영/API/DB/모델/결정 원장 변경 없음.
이번 변경은 미커밋이며 푸시·사용자 홈 전역 메모리 변경은 하지 않았다.
