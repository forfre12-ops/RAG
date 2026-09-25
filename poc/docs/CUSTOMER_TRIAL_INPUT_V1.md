# 고객사 합성 시험 입력 사전 검사 v1

2026-09-15. 병렬 작업 C의 **오프라인 입력 계약**이다. 운영 API, 분류 경로, 모델, 정책과 기존 동결 자료를 변경하지 않는다.
학습/평가 배포 허가나 모델 성능 측정이 아니다. 모든 기존 사용 허가 플래그는 `false`다.

## 1. 이번에 구현한 경계

`customer_trial_input_v1.py`의 `prepare_trial_inputs`는 공개 입력 투영만 받는다.
허용 필드는 `doc_id/text/context/input_sha256` 및 사용 금지 플래그 다섯 개다.
답안, S/V/M 점수, 규칙 번호, 근거 해설, 작성자·계열·말투 태그가 추가되면 거절한다.
정답 파일이나 원고 집필 메타데이터를 읽지 않으며 기존 `score_predictions`를 호출하지 않는다.

본문+맥락은 기존 `customer_benchmark.presented_text`를 그대로 호출한다. 별도의 프롬프트나 라벨 힌트를 붙이지 않는다.
본문과 맥락의 해시, 공개 입력 해시, 실제 제시 문자열의 해시·글자 수, 토큰 페이로드 해시,
정책·토크나이저·소스 코드 지문을 결합한다. 모델 입력 토큰의 필드는 `input_ids/attention_mask/token_type_ids`다.
입력 점검용 메타데이터와 토큰 페이로드는 구별한다. 외부 모델로 전달하는 코드는 아직 없다.

동일한 renderer와 토큰화 계약을 추후 학습/추론 양쪽에서 사용할 수 있게 준비한 것이며,
현재 학습기·추론기가 이 어댑터를 사용하도록 연결되었다고 주장하지 않는다.

## 2. 상태와 실패 처리

| 조건 | 결과 | 수행하지 않는 일 |
| --- | --- | --- |
| 정규 맥락·필수 사실 수신 및 특수 토큰 포함 512 이하 | `input_ready_only` | 등급 승인·학습/평가 허가·고객 정확도 주장 |
| 맥락 그룹 누락 | `needs_review`, `required_context_group_missing` | 누락을 false/0으로 보충 |
| 필수 사실 미수신 | `needs_review`, `required_context_fact_unknown` | 알려진 일부 사실만으로 답 확정 |
| 본문만 제시 | `needs_review`, `body_only_context_not_presented` | 별도 맥락의 조건부 라벨로 본문 분류를 채점 |
| 특수 토큰 포함 512 초과 | `needs_review`, `input_exceeds_512_tokens` | 자동 잘라내기·청크 투표·임의 집계 |
| 입력 해시·정책·토크나이저 지문 불일치, 잘못된 형식 | 실패 | 다른 자료로 조용히 대체 |

토크나이저 파일을 한 번 읽은 바이트의 지문을 확인한 뒤 같은 바이트를 해석한다.
파일에 저장된 padding/truncation 설정을 비활성화하고 특수 토큰을 포함한 전체 길이를 측정한다.
긴 문서는 진단 페이로드에 전체 토큰을 보존하지만 준비 완료로 표시하지 않는다.
512는 이번 사전 검사 계약의 한도다. 실제 실행 모델의 설정·패딩·배치·순방향 실행은 검증하지 않았다.

`input_ready_only`는 **기술적 입력 수신·길이 점검 통과**다. 입력 사실이 실제 참인지, 본문과 의미상 모순이 없는지,
정책 판정에서 충돌이 없는지, 공개 허가가 있는지까지 인증하지 않는다.
본문+맥락 자료는 제공된 가상 사실에 대한 시험이며 본문에서 그 사실을 추출했다는 증거가 아니다.
`model_forward_executed=false`, `grade_scoring_allowed=false`, `model_artifact_verified=false`를 유지한다.

## 3. 예측 결과 결합 계약

`validate_diagnostic_predictions`는 별도로 주어진 진단 결과만 검증한다. 모델이나 주입된 predictor를 실행하지 않는다.
호출자가 선언한 모델 지문·run ID와 입력/제시 문자열/토크나이저/토큰/정책 지문이 모두 맞아야 한다.
모델 지문 일치는 선언값 대조일 뿐 실제 모델 파일이나 실행을 인증하지 않는다.

- 준비되지 않은 문서의 예측, 알 수 없는 ID, 중복 결과, 위조된 지문은 실패한다.
- 준비된 문서의 미제출 결과는 `error/diagnostic_prediction_missing`으로 분모에 남긴다.
- 입력 HOLD는 `needs_review/preflight_hold`로 남긴다. 누락 응답 ERROR와 구분한다.
- `ok`는 등급을 요구하고, `error`는 등급을 금지한다. `needs_review`의 잠정 등급은 검토 상태를 유지한다.
- 답안과 비교하거나 일치율을 산출하지 않는다. 허가 플래그를 바꾸거나 채점기에 자동 전달하지 않는다.

## 4. 기존 164개 실제 사전 검사 결과

정답을 읽지 않고 기존 공개 입력 파일과 지정된 로컬 토크나이저만 사용했다.

| 관점 | 문서 | 기술적 입력 준비 | HOLD | 특수 토큰 포함 길이 |
| --- | ---: | ---: | ---: | --- |
| 본문+맥락 | 164 | 164 | 0 | 368~491 |
| 본문만 | 164 | 0 | 164 | 104~225 |

새 문서 생성 0개, 모델 순방향 실행 0회, 채점 0회, 학습/평가 채택 0개다.
본문 관점의 HOLD는 길이가 아니라 필요한 맥락을 모델에 제시하지 않았기 때문이다.
기존 본문 문자 분류기 진단과 달리 이 계약은 조건부 정답을 사용한 본문 등급 채점을 허용하지 않는다.

산출물:

- `reports/CUSTOMER_GUIDE_PARALLEL_20260915/input/164_body_context_v1/`
- `reports/CUSTOMER_GUIDE_PARALLEL_20260915/input/164_body_only_v1/`
- `reports/CUSTOMER_GUIDE_PARALLEL_20260915/input/tests-input.xml`: 신규 테스트 86개 통과

각 팩은 입력 원본 투영, 제시 문자열·토큰·HOLD 이유, 계약, 요약 및 manifest로 구성된다.
CLI는 새 폴더만 생성한다. 재현은 새 폴더로 실행하고 이미 있는 팩은 `verify`로 검사한다.
검증은 명시적으로 공급한 입력·토크나이저와 현재 소스로 재계산하며, 보고서에 적힌 임의 외부 경로를 따라가지 않는다.
실패 중 생성된 폴더가 남는 경우 성공 팩으로 사용하지 말고 원인을 해결해 별도 버전으로 생성한다.

## 5. 재현 명령과 지문

작업 폴더: `F:\antigravity\rag\poc`. 아래 `--out`은 아직 존재하지 않는 경로로 지정한다.

```powershell
.\.venv\Scripts\python.exe -B scripts/prepare_customer_trial_input_v1.py prepare `
  --inputs reports/CUSTOMER_GUIDE_BATCH04_20260915/reference_v0_4/inputs/body_context.jsonl `
  --expected-input-sha256 16f5b3fc5f885dbb46a4076189f4bc3235f04141a15ae72fc1bb1c0c758b3d7b `
  --tokenizer artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json `
  --expected-tokenizer-sha256 f33819f6e8544c27450ebe253b3a882d9c2a7148d4f0ce15129425712a9993be `
  --expected-policy-sha256 e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9 `
  --profile body_context --out reports/CUSTOMER_GUIDE_PARALLEL_20260915/input/replay-new
```

검증 시 `prepare --out`을 `verify --pack`으로 바꾸고 `--profile`은 생략한다.
본문만의 별도 사전 검사에는 생성 시 `--profile body_only`를 사용한다.

| 지문 | SHA-256 |
| --- | --- |
| 본문+맥락 팩 manifest | `af61361af749f61e04ac25980986680a7bed52d37d3e9deb211835ac1a22bdb7` |
| 본문 전용 팩 manifest | `2e3b23390684ef6d7a4e76acfb74cb7b6f44fe69bc0a71da1161d7928780fef0` |

추가 남은 작업은 새 32개에 같은 검사를 적용하고, 의미·계열 검산을 별도로 결합하며,
장문 근거 보존·청크 집계 계약을 검증한 다음 허용된 시험 전용 학습/추론 실행기로 연결하는 것이다.
현재 어댑터 통과를 기존 제한 자료의 사용 허가 해제 근거로 쓰지 않는다.
