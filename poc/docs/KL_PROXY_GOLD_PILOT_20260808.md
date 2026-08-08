# KL 프록시 골든 생성·심판 파일럿 기록 — 2026-08-08

> 결론: **FAIL / PRODUCTION BLOCKED / REDESIGN REQUIRED**
>
> 이 기록은 합성 프록시의 생성·심판 품질을 검증한 것이며 고객사 실문서 정확도 근거가 아니다.

## 1. 실행 경계

- 제품 API·worker·beat·Redis·PostgreSQL은 재시작하거나 변경하지 않았다.
- 별도 내부 Docker network와 호스트 포트가 없는 Ollama runtime만 사용했다.
- 생성기: `qwen3:14b`
  - model manifest SHA-256:
    `bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8`
- 독립 1차 심판: `gemma3:12b`
  - model manifest SHA-256:
    `f4031aab637d1ffa37b42570452ae0e4fad0314754d17ded67322e4b95836f8a`
- shadow 심판: 사용하지 않음
- 카탈로그 역할: `frozen_proxy_eval_only`
- intended use: `evaluation`

## 2. 생성 run

- run ID: `smoke-matched4-v7-qwen14-001`
- 실행 시각(UTC): `2026-08-07T21:24:35Z` ~ `2026-08-07T21:40:21Z`
- 계획: 12 attempts
- 보존 목표: 등급별 2건, 총 8건
- 결과: 후보 8건, 생성단계 rejected 0건, unused plan item 4건
- 등급: TS 2, S1 2, S2 2, S3 2
- `target_met=true`, `allow_partial=false`
- candidates SHA-256:
  `ac5accfc71dc000d1ead430a078720f12069996ccfeb69435c97f1ab9c8b47bd`
- generation COMPLETE SHA-256:
  `58eaa660ef60fa0d0200d0e5117a7f2b0965f8f01806b9886465f4957cdb6b6e`

생성기의 길이·JSON 완결·표 종료·일부 산술 게이트는 통과했다. 그러나 본문 수동 감사에서 다음
종류의 문제가 확인되었다.

- 정상범위 안의 실측값을 실패 조건으로 설명
- 변경 전·후 차이 또는 비율을 다른 기준값과 혼동
- 표의 정상범위·실패경계와 원인 서술 불일치
- S1 문서에서 내부 비공지성과 일반 공개 접근을 혼동
- S3 문서에 재현 가능한 세부 경계·공정값을 넣어 `value=0` 근거 훼손
- 기준연도·시험 순서·후속조치 기한 불일치

따라서 생성단계 통과를 골든 통과로 해석하지 않는다.

## 3. 심판 run

첫 심판 시도 `judge-matched4-v7-gemma12-001`은 최소 runtime release에서 구형 보조 CLI 의존성이
누락되어 데이터 처리 전에 exit 1 했다. 출력 아티팩트는 만들어지지 않았다. 심판이
`LocalOpenAIProvider`를 직접 생성하도록 의존성을 제거하고 단위·로컬 컨테이너·KL 컨테이너 import
smoke를 통과한 hotfix로 새 run을 시작했다.

- hotfix release SHA-256:
  `020f1b0451f827b2b8d82557d951ef52e61a7cbcc008fc20fe94d118aa804f57`
- hotfix judge source SHA-256:
  `609a3416efd7a7d9f4ef327caeff223b10406c58ef14fa29f269658b5e946234`
- 유효 run ID: `judge-matched4-v7-gemma12-002`
- 실행 시각(UTC): `2026-08-07T21:47:20Z` ~ `2026-08-07T21:54:13Z`
- 입력: 8건
- 결과: `gold_candidate=0`, `uncertain=8`
- judge error: 0
- judge parse failure: 0
- label/factor/quality 판정은 모두 실행 완료
- run manifest SHA-256:
  `838156a207dd38d6d1619aed885d76e4c67243632baf273f66933d4c1215dad4`
- gold candidate SHA-256(빈 파일):
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- uncertain SHA-256:
  `26d17cbd18e5969cb5fd89026c253ffc5409ca1d78d5c61f25fb5e587026fa67`
- judge COMPLETE SHA-256:
  `8bd0de6941467cdd69a55c4aafb4ff312d2d33160b71e86c08e1dbf93b91c376`

| 보류 사유 | 건수 |
|---|---:|
| intended label과 Gemma 등급 불일치 | 3 |
| intended S/V/M과 Gemma 요소 판단 불일치 | 3 |
| document-quality issue 증거 형식 불완전 | 2 |
| **합계** | **8** |

모든 문서에서 정량 일관성 또는 불필요 반복 문제가 함께 관찰되었다. 일부 반복 판정에는 정상적인
표-본문 해설까지 과도하게 잡는 경향도 있어, 생성 품질과 별개로 `non_repetitive` 정의를 더 정확히
제한해야 한다.

## 4. 결정

이 run의 후보는 프록시 골든 1,000건이나 학습 풀에 한 건도 승격하지 않는다. 수량 확대도 시작하지
않는다. 다음 파일럿 전 선결조건은 다음과 같다.

1. 코드가 날짜 순서·기초값·before/after·차이·정상범위·실패경계를 먼저 계산한 결정적 fact ledger를 만든다.
2. Qwen은 ledger를 바꾸지 않고 문서 형식으로 서술하며, 생성 후 코드가 ledger의 불변식을 다시 검사한다.
3. S/V/M 근거는 문서 안의 서로 다른 문장으로 명시하되 등급명 자체는 쓰지 않는다.
4. S1은 외부 비공개와 내부 관리 미공식화를 구분하고, S3는 재현 가능한 고유 경계·레시피를 넣지 않는다.
5. `non_repetitive`는 표의 수치를 해설하거나 결론에서 조치를 요약하는 정상 구조를 실패로 보지 않도록 좁힌다.
6. 동일한 TS/S1/S2/S3 각 2건 파일럿을 다시 실행한다.
7. 등급별 최소 1건 이상이 독립 심판을 통과하고, 통과본 전건 수동 감사에서 새 모순이 없을 때만
   production sharding을 시작한다.

## 5. KL L4 처리량 관측

- 생성: 약 946초 / 보존 후보 8건 = 후보 1건당 약 118초
- 심판: 약 414초 / 입력 8건 = 후보 1건당 약 52초

모델 적재와 소표본 오버헤드를 포함한 1회 관측이라 확정 성능값은 아니다. 다만 같은 단일 L4·동시성 1
조건을 단순 외삽하면 candidate buffer 2배만 사용해도 평가 후보 2,000건은 생성+심판 약 94시간,
학습 후보 5,400건은 약 255시간이다. 두 작업을 합치면 약 14.5일이며 재시도·부족 셀 재생성·모델
학습 시간은 빠져 있다. buffer 3배이면 약 22일까지 늘어난다.

따라서 KL L4는 품질·재개·격리 파일럿에는 적합하지만, 전체 production은 사실 원장 적용 후 새 수율을
다시 측정하고 A100 실행 환경 또는 안전하게 검증된 병렬도 2를 우선 검토한다. 처리량을 이유로 품질
게이트를 완화하지 않는다.

## 6. 대표 8 재파일럿과 후속 설계 — 2026-08-08

- run ID: `pilot-representative8-qwen14b-pf-515a40a3bc67`
- 결과: 후보 6건, rejected 10건, 미사용 8건, `target_met=false` (exit 2)
- 후보·거부·manifest SHA-256은 각각
  `977b29f8e7d5ae3b22b5446cafbf7f48325cd17acc8b2e676a6c1f59457a92e5`,
  `3f1a37da5911146e3e48abe04a7868437ac795a2c6d88a33e353c69316db27ec`,
  `00168e81c0f1d8720611fc5c4ce9781cec6098c77c9281909884b77e35bd5ac1`이다.

제품 서비스나 GPU OOM은 발생하지 않았다. 다만 합성 S3는 실제 공개문서 조건에서 전부 거부되었고,
일부 통과 문서에는 생성 지시문 태그·평가 용어·불필요한 새 수치가 남아 수동 감사 승격을 금지한다.
따라서 다음 release는 모델이 검산 부록을 쓰지 못하게 하고 코드를 유일한 부록 작성자로 고정하며,
S3 300건은 라이선스 공개 원문 v1으로 조립한다. 이 변경 뒤 TS/S1/S2만 별도 소표본으로 재파일럿하고,
전건 수동 감사와 독립 심판을 통과하기 전에는 대량 생성으로 진행하지 않는다.

이후 `proxy-fact-ledger-v2`를 추가했다. 모델 응답의 모든 아라비아 숫자는 scenario·instance·문서계열·
공개범위·위험 설명에 명시된 숫자와 값 기준으로 대조한다. 입력에 없는 숫자·금액·범위·비율·날짜는
후보 거부 및 재생성 사유가 된다. 이는 검산 원장과 직접 충돌하지 않는 임의 공정 수치까지 차단한다.
