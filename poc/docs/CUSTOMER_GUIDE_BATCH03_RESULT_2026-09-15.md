# 고객사 합성 문서 제작 — 세 번째 묶음 결과

작성일: 2026-09-15. 신규 원고36건, 기존80건을 그대로 보존해 누적116건이다.
이는 **본문+명시한 가상 맥락+내부 기준0.1 아래의 조건부 참조 답안**이다.
실제 고객사 정답, 사람 서명, GOLD200 동결, 학습800건 완료를 뜻하지 않는다.
외부 검수자 확보를 내부 집필·답안 작성의 선행조건으로 두지 않는다.

## 1. 수량과 범위

| 등급 | 기존 | 신규 | 누적 | 목표250까지 부족 |
|---|---:|---:|---:|---:|
| TS | 9 | 9 | 18 | 232 |
| S1 | 27 | 9 | 36 | 214 |
| S2 | 20 | 9 | 29 | 221 |
| S3 | 24 | 9 | 33 | 217 |
| 합계 | 80 | 36 | 116 | 884 |

- 동일 문서의 숫자 바꾸기·맥락 변형본을 신규 수량으로 세지 않았다.
- 이메일12·문답12·관측/실험일지12를 별도로 집필했다. 각 형식에는 네 등급이3건씩 있다.
- 고등급 이메일/문답/일지에 역세 조건, 공급 보정, 반사 분리, 면담·유통 실험 등 실질 정보를 담았다.
- 공개된 고비용 기술 결과, 저비용 개별 승인 문서, 제3자 취득 경로가 있는 문서를 함께 작성했다.
- TS 비기술 사례는 계약면담·유통제안 실험의 직접 조사 투입이며 예상 매출/계약액을 투자액으로 계산하지 않았다.
- 공개·관리·귀속 투입은 원고와 함께 작성한 **합성 전제**이지 실제 문서에서 관측하거나 확인한 사실이 아니다.
  실제 자료의 없는 사실을 보완한 것처럼 표시하지 않는다. 기존80건의 본문/맥락/답/근거는 모두 불변이다.
- 신규 본문 길이는252~286자다. 아직 고객사 장문·복합문서의 길이/난이도 분포를 대표하지 않는다.

정책 ID/버전과 V 앵커는 이전과 같다. 팩 버전만0.3이다.
원 가이드의 곱4→S1을 사용하며 운영 v22는 수정하지 않았다.
V의 비용/인시 임계치는 내부 작성용 앵커이지 원 가이드의 수치나 고객 승인 기준이 아니다.

## 2. 근거와 검산

- 본문 근거232곳, 가상 맥락 사실 결합1,624곳, ID/본문·입력 해시/정책/근거 위치 검증.
- 새36건마다 본문에서 피연산자와 기재 결과를 읽어 산술 확인: 차이·합계·중복 제외·비율 등36항목.
  이전27항목과 합쳐63항목이며,36개 결과값을 각각 바꾸면 실패하는 회귀검사가 있다.
- 산술이 맞다는 사실은 실험 설계의 타당성, 모든 문장의 의미, 현실의 비공개성·투입·ACL을 인증하지 않는다.
- 산식27조합 검산, HOLD4검사, 같은 본문에 다른 맥락을 넣어 답이 달라지는116반례를 유지했다.
  이 진단들은 추가 문서나 별도 골든셋이 아니다.
- 실제 이메일 말머리·문답 표지·시간/요일 흐름 검사36건을 추가했다.
  이 표지 검사는 장르 형식을 확인할 뿐 계열 독립성·실제 업무 자연스러움을 보증하지 않는다.
- 입력에는 답, S/V/M 점수, 규칙, 해설, 형식 태그, 계열 ID를 넣지 않는다. 본문과 사실 맥락만 포함한다.
- 맥락은 판정 대상 입력이다. 본문 단독으로는116건 전부 등급 채점에서 제외한다.
- `training_allowed/model_evaluation_allowed/gold_eligible/customer_accuracy_measured/human_signoff_created=false`.
  S3도 외부 배포 허가와 별개다. 전 직원용3건은 S3이지만 `release_authorized=false`로 남겼다.

## 3. 형식 쏠림 진단 — 성능 수치가 아님

새36건은 각 형식의 최빈 등급을 같은 원고에 대입해도9/36=25%이며 전체 최빈 등급 기준선도25%다.
형식 태그가 있는 누적96건은 다음과 같다. 초기20건에 새 형식 태그를 소급 부여하지 않았다.

| 형식 | TS | S1 | S2 | S3 |
|---|---:|---:|---:|---:|
| email | 3 | 7 | 5 | 6 |
| qa | 3 | 5 | 3 | 7 |
| log | 3 | 6 | 9 | 3 |
| table | 3 | 4 | 2 | 3 |
| memo | 2 | 8 | 0 | 0 |
| procedure | 0 | 2 | 1 | 3 |
| notice | 0 | 0 | 3 | 5 |

이 누적96건의 형식별 최빈 등급 대입은43/96=44.79%, 전체 최빈 등급 기준선33.33%다.
이전60건의34/60=56.67%와는 모집단/등급 분포가 다르므로 통제된 개선 비교가 아니다.
메모/안내/절차의 빈 등급, 업무 주제·문체·길이의 연관성은 남아 있다. `format_bias_resolved=false`.
이번 원고를 네 등급으로 균형 작성했다는 사실 자체도 실제 고객 모집단 분포를 입증하지 않는다.

char2~5 n-gram TF-IDF/LinearSVC,5seed(0~4),5fold로116건의 입력 뷰 연관성을 진단했다.
어휘/IDF는 각 학습 fold에서만 적합했고, 계열 CV는 현재 선언 계열/근접 중복 연결 성분을 사용했다.

| 입력 뷰 | 층화 CV | 라벨 섞기 | 계열 CV | 계열 라벨 섞기 | 차이20%p 초과 경고 |
|---|---:|---:|---:|---:|---|
| 본문 | 46.03% | 30.52% | 44.14% | 30.69% | 아니오 |
| 제목 | 40.86% | 30.69% | 40.52% | 30.00% | 아니오 |
| 맥락 | 67.24% | 31.90% | 67.59% | 32.07% | 예 |
| 본문+맥락 | 60.00% | 33.45% | 59.31% | 31.72% | 예 |

이 값은 고객 분류 모델의 성능평가도800/200 예상 정확도도 아니다.
맥락은 필요한 정책 사실을 담으므로 높은 연관성이 곧 금지된 답 누설이라는 뜻은 아니다.
본문/제목 경고 미초과 역시 누설 없음 증명이 아니다. 현재 선언한116계열이 업무 의미까지 독립임을 인증하지 않는다.
이전80건과 분모/등급 분포가 달라 수치 하락을 통제된 편향 개선으로 발표하지 않는다.

## 4. 데이터 검사와 실행 검증

- 누적116건 내부 정확/숫자 정규화/문자5-gram Jaccard≥0.85 중복0, 알려진 fixture 본문 해시 일치0, 현재 선언 연결 계열116개.
- 기존 `datasets`404JSONL의 지원 본문451,663행과 정확/숫자 정규화 일치0.
  이 행 수는 고유 문서 수나 학습 가능한 수가 아니다.
- 미지원 본문1,850행, JSON객체 파싱 실패14행으로 `coverage_complete=false` 유지.
  14행은 기존 `datasets/gold_real/uncertain_cases.jsonl`에 있다. 전체 의미/외부 풀 유사중복 검증은 아니다.
- 빈 외부 풀·지원 본문0·외부 풀 일치·512토큰 초과는 팩 생성 전에 실패한다.
- 현재 저장된 토크나이저의 본문104~225, 본문+맥락368~491토큰.116/116이512안에 들어간다.
  원본 입력을 자르지 않았으며 실제 활성 모델/서빙 입력 경로는 검증하지 않았다.
- 신규72개, 관련1,575개, DB/torch/transformers import를 차단한 독립1,217개 통과. 서로 부분집합이므로 합산하지 않는다.
- 원본406JSONL 불변. 과거9/14 baseline 대비 `policy_engine.py` 변경이 여전히 있어 보존 검사는 exit1이다.
  이번 작업의 변경으로 숨기거나 과거 baseline을 다시 설정하지 않는다.

## 5. 산출물과 재현

원고: `poc/scripts/customer_guide_batch03.py`.
제작/검증/진단: `poc/scripts/build_customer_guide_batch03.py`.
회귀검사: `poc/tests/data_quality/test_customer_guide_batch03.py`.
누적 팩: `poc/reports/CUSTOMER_GUIDE_BATCH03_20260915/reference_v0_3/` — 135payload+manifest.

팩 내부에는 `documents/*.txt`, `inputs/body_context.jsonl`, `authoring/documents.jsonl`,
`answers/answers.candidate.jsonl`, `answers/evidence.jsonl`, 사람이 읽는 `answers/REFERENCE_ANSWERS.md`, 각 진단을 분리했다.

- 정책 SHA256: `e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9`.
- 팩 manifest SHA256: `23936f6dcfb274f88eed0da23e9032cc3b703631876165313b819b177a84dfaf`.
- 토크나이저 SHA256: `f33819f6e8544c27450ebe253b3a882d9c2a7148d4f0ce15129425712a9993be`.
- HEAD: `83c88e9f5833c61d01d36276e415be5bc24987df`. 추가 커밋/푸시 없음.

다음 명령은 `F:\antigravity\rag\poc`에서 실행한다. 팩과 보고서는 덮어쓰지 않으므로 재생성 시 새 출력 경로를 쓴다.

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_batch03.py prepare --out reports/CUSTOMER_GUIDE_BATCH03_20260915/replay_new --parent-pack reports/CUSTOMER_GUIDE_BATCH02_20260915/reference_v0_2 --corpus-root datasets --tokenizer artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_batch03.py verify --pack reports/CUSTOMER_GUIDE_BATCH03_20260915/reference_v0_3
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_batch03.py audit --pack reports/CUSTOMER_GUIDE_BATCH03_20260915/reference_v0_3 --out reports/CUSTOMER_GUIDE_BATCH03_20260915/shortcut_replay_new.json --seeds 5
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_customer_guide_batch03.py -q
.\.venv\Scripts\python.exe -B -m ruff check scripts/customer_guide_batch03.py scripts/build_customer_guide_batch03.py tests/data_quality/test_customer_guide_batch03.py
```

관련 검증은 다음 범위에서 실행했다. JUnit 결과는 상위 결과 폴더의 `tests-new.xml`, `tests-final.xml`, `isolated-tests-final.xml`에 있다.

```powershell
$env:TESTING='1'
$env:DATABASE_URL='postgresql+psycopg://test:test@127.0.0.1:9/test'
.\.venv\Scripts\python.exe -B -m pytest tests/data_quality tests/test_content_reference.py tests/test_content_reference_revision.py tests/test_content_reference_review.py tests/test_classification_review_workflow.py tests/test_classification_pilot_intake.py tests/test_trainer_sample_weights.py tests/test_trainer_fnr_metrics.py tests/test_eval_p1_model_gold_source_prior.py tests/test_proxy_training_finalization.py tests/test_proxy_model_comparison.py tests/test_serving_eval.py tests/test_policy_engine.py tests/test_policy_engine_defects.py tests/test_policy_rule_checker.py tests/test_org_mapping.py -q --tb=short
```

전체 저장소/운영 E2E 통과가 아니다. `reports/`는 ignore 대상이며 로컬 생성은 외부 백업이 아니다.
검증 해시, 이전 팩 보존, 테스트 XML 요약은 `evidence-final.json`에 기록한다.
같은 폴더의 `collect_evidence.py`는 이미 존재하는 실행 결과를 읽어 수집하며 결과 파일을 덮어쓰지 않는다.

## 6. 다음 작업

1. 부족884개 집필·선별: TS232/S1 214/S2 221/S3 217. 기존 답/기준을 수량에 맞춰 변경하지 않는다.
2. 메모/안내/절차의 등급 빈칸을 실제 내용이 있는 새 사례로 보완하고, 주제·문체·길이 쏠림과 의미 중복을 점검한다.
3. 실제 고객 시험 입력이 본문만인지 본문+관리 맥락인지 일치시키는 별도 시험 연결을 준비한다.
   현 운영 본문 전용 경로로 이 조건부 정답을 채점하지 않는다. 장문 도입 시 모델 길이 한도와 근거 보존을 먼저 검증한다.
4. 1,000개 원고의 등급·근거 검산과 연결 계열 검사를 마친 뒤 등급별200/50, 총800/200을 한 번 분리·동결한다.
   현재 자료를 여러 번 재분할해 유리한200건을 선택하지 않는다.
5. 분류 결과와 정답을 대조할 때 전체200건 분모, 등급별 재현율, 심각한 하향분류, 보류/누락/오류를 분리 보고한다.
   합성 내부 기준 일치율과 실제 고객 문서 정확도를 구분하며 운영 교체는 별도 결정이다.
