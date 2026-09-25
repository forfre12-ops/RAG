# 교차 작성 원고 32개 추가 결과

2026-09-15. 병렬 집필 작업 A. 내부 합성 조건부 참조 후보이며 고객 GOLD, 운영 정책 승인, 모델 정확도 보고가 아니다.

## 1. 완료한 것과 범위

[교차 작성 명세](CUSTOMER_GUIDE_AUTHORING_CROSS_DESIGN_V0_1.md)의 4분야×2말투×4사례를 독립 집필했다.
동일 문서의 말투·제목·숫자만 바꾼 사본이 아니다. 본문을 먼저 작성하고 명시된 가상 공개·투입·관리 맥락을 기존 정책0.1로 판정한다.
원고 소스에는 목표 등급 인자를 넣지 않았다. 분야·말투·형식·독립 집필 설명은 모델 입력이 아닌 작성 메타데이터다.

| 집계 | TS | S1 | S2 | S3 | 합계 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 기존 | 30 | 48 | 41 | 45 | 164 |
| 새 원고 | 8 | 8 | 8 | 8 | 32 |
| 누적 | 38 | 56 | 49 | 53 | 196 |
| 등급별250개 목표까지 부족 | 212 | 194 | 201 | 197 | 804 |

기존 원고의 본문·맥락·답안·근거164개와 정책은 그대로 보존했다. 부족804개는 기존196개가 나중에 모두 채택된다는 가정이다.
학습800개 중 남은 제작량은604개, 별도 최종 평가200개는 아직 제작되지 않았다. 현재 학습/평가 **채택은 모두0개**다.

## 2. 새 원고 구성

각 분야·말투 조합에 네 등급이 각각1개씩 있다. 메모·일지·문답·절차 형식도 각 등급에2개씩 교차했다.
등급 균형은 작성 목표의 결과 확인이지 정답을 수량에 맞춰 변경하는 장치가 아니다.

| 분야 | 평서체4개 | 정중체4개 |
| --- | --- | --- |
| 측정·실험 | 절연막 반복 굽힘, 분말 영점, 암전류 처리, 그림자 관찰 | 유로 용질 회수, 개구 손실, 전지 휴지 기록, 염도 표시 |
| 소프트웨어·시스템 | 캐시 퇴출, 시각 변환, 설정 상속, 압축 목록 | 지연 사건 마감창, 타일 원점, 재시도 횟수, 키보드 초점 |
| 영업·거래·조사 | 선적 대기 선택권, 견적 범위, 면담 코딩, 용기 회수 조사 | 물량 예약, 정산 경계, 반품 자료, 공동구매 안내 |
| 운영·물류·시설 | 냉장 제상 회복, 차단 위치 인계, 입고 대기, 빗물 유입 | 야적장 교차, 필터 차압, 승강기 호출, 회의 음향 |

본문당2개의 인용문을 해시·문자 위치로 결합했다. 맥락14개 사실 항목도 별도 출처 위치와 묶었다.
공개 여부와 해당 정보 취득에 귀속된 비용·인시, 실제 접근 제한은 **가상 조건**이다. 본문에 없는 현실 사실을 관측했다고 주장하지 않는다.
견적·운송료·재고·상품 가액 등을 정보 취득 비용으로 대신 쓰지 않았다. S3인 사내 공용 안내3개도 외부 공개 승인은 없는 조건이다.

## 3. 자동 검증 결과

- 새 원고32개의 산술 표현32개를 Decimal 계산으로 대조했다. 각 결과를1만큼 바꾸면 실패하는 회귀시험도 통과했다.
- 기존 산술 근거를 별도 경로에 보존했으며 누적 나열된 산술 검사는143개다. 전체 문장의 의미·현실 진실성 인증은 아니다.
- 누적392개의 본문 인용문과2,744개의 맥락 항목 결합을 검증했다.
- 누적196개의 정확 중복·숫자만 변경 중복·기존 검사 기준의 유사 본문·알려진 fixture 일치가0쌍이다.
- 기존4개의 의미 계열 제안은 유지했다. 누적 자동 구성요소196개를 해당 제안으로 합치면182개다. 새로운 의미 계열의 독립성까지 인증한 결과는 아니다.
- 로컬 tokenizer로 본문/본문+맥락392뷰 모두512토큰 이내다. 누적 최댓값491, 새32개의 본문122~142/본문+맥락388~407토큰이다.
- 신규 테스트98개 통과(16.06초), 신규 Python3개 파일 ruff 통과. 부모팩 검증 후 생성하고 새 팩의 소스/본문/답안/메타 재생 검증을 통과했다.

신규 테스트는 원본 보존, 교차 집계, 모든 새 본문의 산술 변조, 모든 근거 결합, 권한 거절,
빈 입력·미확정 조건, 재해시된 본문/답안/맥락/정책/메타/산술 변조, 소스 변경, 경로 이탈,
누락/추가 파일, 토큰 허위 주장, 부모팩 불일치, 빈/미지원/중복 외부 입력을 포함한다.
전체 프로젝트 테스트 결과는 통합 담당의 별도 실행 결과로 보고한다.

외부 전체 데이터 풀 및 n-gram 진단은 이 집필 작업에서는 실행하지 않았다.
팩의 `audit/external_pool.json`은 `null`이며 과거 외부 검사 결과를 최신 결과로 복사하지 않았다.
토큰 적합은 의미 근거 보존, 운영 모델 연결, 고객 정확도 또는 장문 지원의 증명이 아니다.

## 4. 파일·재현

- 집필: `poc/scripts/customer_guide_batch05.py`
- 패키지 생성·재생 검증: `poc/scripts/build_customer_guide_batch05.py`
- 테스트: `poc/tests/data_quality/test_customer_guide_batch05.py`
- 결과: `poc/reports/CUSTOMER_GUIDE_PARALLEL_20260915/authoring/reference_v0_5/`
- 테스트 XML: `poc/reports/CUSTOMER_GUIDE_PARALLEL_20260915/authoring/tests-batch05.xml`

결과 팩은 payload217개와 manifest1개다. manifest SHA256:

`5ccb4a57935794e8b813213c307ab33895b27e12b36983e3778fd0ae7a0d068e`

부모는 `CUSTOMER_GUIDE_BATCH04_20260915/reference_v0_4`, manifest SHA256:

`dcc8ea80ad4e3ff6fadf21a4debe20212ac015a2892ebd444cdee73917a07f41`

정책 SHA256은 `e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9`로 불변이다.
사용 tokenizer는 `artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json`, SHA256은
`f33819f6e8544c27450ebe253b3a882d9c2a7148d4f0ce15129425712a9993be`다.

아래 명령의 작업 위치는 `F:\antigravity\rag\poc`이다. 생성 출력은 미존재 경로여야 한다.

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_customer_guide_batch05.py -q
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_batch05.py verify --pack reports/CUSTOMER_GUIDE_PARALLEL_20260915/authoring/reference_v0_5
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_batch05.py prepare --out reports/CUSTOMER_GUIDE_PARALLEL_20260915/authoring/reproduce_v0_5 --parent-pack reports/CUSTOMER_GUIDE_BATCH04_20260915/reference_v0_4 --tokenizer artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json
```

기존 API와 호환되는 진입점은 `build_new_cases()`의 `(record, answer, evidence)`32튜플,
`core_payload()`의 `(documents, answers, payload)`196개, `prepare(out, *, parent_pack=None, corpus_root=None, tokenizer=None)`, `verify(root)`다.
CLI 생성에는 부모팩이 필수이며 단위시험용 함수에서 생략한 경우 manifest에 부모 미검증을 명시한다.

## 5. 다음 통합과 제한

통합 담당은 독립 정책 검산, 입력 어댑터 사전 확인, 개발 노출 이력과 새32개의 의미 계열·외부 중복 결과를 결합한다.
새32개는 개발 방향에 따라 집필됐으므로 최종 블라인드200개로 쓰지 않는다. 승인 플래그를 직접 켜거나 모델 학습·운영 API를 바꾸지 않았다.
현재196개와 별도로 새로운 계열의 최종200개를 제작·봉인해야 한다.

이번 교차 구성은 문체·주제 쏠림을 줄이려는 작성 방식이다. 문체 변화에 예측이 불변이라는 결과나 고객 정확도 향상을 측정한 것이 아니다.
여러 AI가 같은 답에 동의했다는 이유로 확정하지 않는다. 가상 사실의 충족, 정책 버전, 독립 검산과 반례를 함께 검증해야 한다.
현재 고정 가능한 것은 **명시된 사실 조건과 내부 기준0.1 아래 조건부 답안**이며, 실제 문서의 관리등급을 맞췄다는 주장이 아니다.
