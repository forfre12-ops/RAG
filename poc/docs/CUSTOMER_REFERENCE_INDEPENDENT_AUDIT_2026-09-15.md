# 조건부 참조 답안 독립 구현 검산 — 2026-09-15

## 결과와 경계

기존 누적 164개의 입력·답안·근거를 **생성기 판정 함수를 사용하지 않는 별도 구현**으로 대조했다.
164개 모두 현행 내부 정책 0.1 아래 일치했다. TS 30 / S1 48 / S2 41 / S3 45이며 원고·정답·정책은 수정하지 않았다.

이는 같은 내부 기준의 구현 일치와 입력/근거 결합 검산이다. 별도의 현실 정답 권위가 생긴 것이 아니며,
가상 맥락이 실제 관측 사실이라고 인증하지 않는다. 모델 호출·학습·고객 정확도 측정·GOLD 승격은 없다.
본문 전체의 의미·경제적 가정의 현실성·세 등급 배제 설명의 자연어 의미는 이번 검산의 증명 범위 밖이다.

## 기존 검증과 다른 점

- 모델에 제시되는 `input.context`를 별도 parser로 해석한다. 답안 sidecar의 S/V/M을 읽어 기대 답을 만들지 않는다.
- S의 8개 불리언 조합, M의 32개 불리언 조합을 독립 표와 비교한다.
- 등급 기대값은 27개 S/V/M 조합을 전부 적은 literal table에서 가져온다. 기존 곱 매핑이나 2의 개수 계산을 재사용하지 않는다.
- V는 문서화된 내부 앵커를 별도 구현한다. 비용 100만원/3,000만원, 인시 40/480의 경계·직전/직후·OR/AND 조건을 검산한다.
- 필수 근거 12개 중 하나라도 없으면 HOLD이다. 알려진 0이 있어도 미수신을 0으로 대체하지 않는다.
- 공개 허가/기타 보호 사유는 등급과 별도다. S3 산출만으로 공개 권한을 부여하지 않는다.
- 기존 문서/답안의 typed schema와 해시 helper는 결합 검증에 재사용한다. 그 재사용이 독립적인 의미 증명을 뜻하지 않는다.

고정 정책 SHA256: `e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9`.
명세 출처는 `CUSTOMER_GUIDE_REFERENCE_V0_1.md`이다. 이번 작업에서 PDF를 다시 판독하거나 정책을 승인한 것은 아니다.

## 변조·반례 시험

신규 테스트 **137개 통과**, 선택적 DB/모델 런타임 import를 차단한 격리 실행도 **137개 통과**했다.
두 실행은 같은 테스트 집합이며 합산하지 않는다. Ruff 검사도 통과했다.

확인한 실패 조건:

- 답안 등급, 요소 점수, 곱, 정책 해시를 바꾸면 실패.
- 원래 입력과 sidecar의 가상 사실 또는 인용 근거를 다르게 바꾸면 실패.
- 본문/입력 해시 불일치, 근거 중복·누락, 권한 플래그 승격은 실패.
- 입력을 다시 해시하더라도 공개 여부가 취득 경로와 충돌하면 고정 답안을 거부.
- 생성기 `decide`, `_factor_levels`, `grade_from_levels`, `decode_context`, `validate_reference`를 호출 시 강제 실패시켜도 164개 검산이 통과.
- CLI 입력 0건은 오류이며 성공 보고서를 생성하지 않음. 기존 출력 디렉터리나 입력 pack 내부 출력은 거부.

모든 가상 사실과 답안을 함께 일관되게 바꾸면 그것이 현실의 거짓인지 이 도구만으로 판별할 수 없다.
이 도구는 승인·외부 관측·문서 진위를 대신하지 않는다.

## 추가 의미 계열 후보

기존 4개 계열 제안과 별도로 다음 **4개 후보, 8개 문서**를 기록했다. 등급이나 덧셈/뺄셈 연산자로 일괄 묶은 것이 아니라,
선택한 본문의 업무 흐름을 읽고 인용·위치·본문 해시를 결합했다. 아직 분할 규칙에 자동 반영하지 않는다.

| 후보 | 문서 계열 | 공통 구조 |
|---|---|---|
| 식별자와 재시도 횟수 | retry-report, event-sequence | 원 사건/문서와 재전송·재시도 횟수 구분 |
| 페이지 대응 확인 | translation-page, page-fold-check | 빈 쪽을 포함하는 원본 페이지 연결표 대조 |
| 스캔 변환과 내용 누락 | scan-rotate, scanner-crop | 방향/영역 수정과 잘린 내용 복구의 구별 |
| 입고와 사용 가능 상태 | receiving-shortfall, tray-verification | 실물·외관·사용 가능 수량을 분리하고 후속 입고 이력 보존 |

이는 전수 의미 독립성 검토가 아니다. 다른 관련 계열이 더 있을 수 있다.
통합 시 기존 연결요소를 보존하면서 이 후보를 채택할지 별도 기록하고, 평가 제외 전파에 적용해야 한다.

## 파일과 실행

- 구현: `poc/src/koipa/customer_reference_audit_v1.py`
- CLI: `poc/scripts/audit_customer_reference_v1.py`
- 회귀: `poc/tests/data_quality/test_customer_reference_audit_v1.py`
- 결과: `poc/reports/CUSTOMER_GUIDE_PARALLEL_20260915/independent/reference164_v1/`
- 실행 로그: 같은 상위 폴더의 `tests.xml`, `tests-isolated.xml`
- 결과 manifest SHA256: `59361c4aefc41e283945eb79a471fa2f094c8f229d01d17d1162fbcd7e5971bf`

`poc`에서 실행하며 출력은 매번 새 경로를 지정한다.

```powershell
.\.venv\Scripts\python.exe -B scripts/audit_customer_reference_v1.py `
  --pack reports/CUSTOMER_GUIDE_BATCH04_20260915/reference_v0_4 `
  --out reports/CUSTOMER_GUIDE_PARALLEL_20260915/independent/reference164_replay

$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality `
  tests/data_quality/test_customer_reference_audit_v1.py -q
```

CLI는 입력 세 파일과 소스 지문을 기록하고 전후 입력 바이트 불변을 확인한다. pack 전체 manifest의 모든 파일을 검증하는
기존 verifier를 대체하지 않으므로 `pack_manifest_validation_performed=false`를 명시한다. 통합 실행자는 기존 pack verifier도 실행한다.

다음 신규 묶음은 raw record/answer/detail 목록을 `audit_reference(records, answers, details)`에 넘겨 똑같이 검사할 수 있다.
이 API는 164개 수량을 하드코딩하지 않는다. 의미 계열 후보 API는 `semantic_family_candidates(records)`이다.
현재 보고서는 기존 164개만 포함하며, 병렬 집필의 새 원고를 검사했다고 주장하지 않는다.
