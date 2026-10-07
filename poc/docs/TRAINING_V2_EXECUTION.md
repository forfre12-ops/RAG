# 분류 모델 학습셋 v2 실행 계약

## 목적

기존 v5 모델을 기준선으로 보존한 채, 학습용 3,000건과 평가용 1,000건을
서로 분리한다. 이 계약의 Proxy 문서는 고객사 실문서나 Locked Gold가 아니다.
따라서 결과는 모델 회귀·보수적 탐지 방향의 근거일 뿐 고객사 정확도 주장이 아니다.

## 고정된 데이터 역할

| 자산 | 수량 | 사용 가능 범위 |
|---|---:|---|
| 학습셋 v2 | 3,000 | 학습, validation, calibration만 가능 |
| 개발 평가셋 | 200 | 모델·임계값 설계 반복에만 가능 |
| 최종 동결 평가셋 | 800 | 후보 모델 확정 시 단 한 번 비교 |
| 공개 S3 홀드아웃 | 600 | 공개 문서 과잉경보 측정만 가능 |
| 기존 Proxy Gold 후보 | 1,000 | 사람 검수·회귀 후보만 가능 |

학습셋 v2는 `synthetic` 2,700건과 사용 허가가 확인된 `public_real` S3 300건으로
구성한다. 최종 목표 분포는 TS/S1/S2/S3 각 750건이며, S3의 750건은 synthetic 450건과
public_real 300건으로 나뉜다. 공개 S3는 고등급 재현율의 근거가 아니라 S3 과잉경보를
낮추기 위한 실제 문서 형식 학습 재료다.

## 현재 상태

- 공개 S3 학습 전용 300건: `public-s3-train-300-20260808-v3` 준비됨.
- 공개 S3 평가 전용 600건: v1 300건과 blind v2 300건 준비됨.
- 기존 1,000건 Proxy Gold 후보: 사람 검수 전 `proposed` 상태이며 학습 입력 금지.
- synthetic 학습 후보 2,700건: 아직 준비되지 않음. 이전 Qwen/Gemma 파일럿은 고품질
  후보 0건, uncertain 8건으로 종료됐으므로 대량 생성 시작 조건을 충족하지 못한다.

따라서 현재 상태에서 재학습을 실행하거나 기존 1,000건을 학습셋에 혼입하면 안 된다.

## 생성·검수 순서

1. TS/S1/S2/S3별 직접 작성 고난도 파일럿을 먼저 만든다. 각 사례는 단일 문구가 아니라
   기술·가치·비공개성·접근통제·공개 여부와 반례를 가진 문서여야 한다.
2. 파일럿을 독립 심판과 사실대장 검증에 통과시킨다. TS/S1은 S3로 하향되는 반례,
   S3는 고유 수치가 있어도 공개되어 상향되면 안 되는 반례를 포함한다.
3. 파일럿의 등급별 합격률과 수동 감사가 통과한 뒤에만 10개 family shard로 2,700건
   후보를 생성하고 심판한다. 부족한 등급·시나리오·형식만 top-up한다.
4. `assemble_proxy_training_pool.py`로 2,700 + 300을 조립한다. 이 단계는 기존 Proxy
   Gold 1,000건, 공개 홀드아웃 600건, family, 문서 해시가 조금이라도 겹치면 실패한다.
5. `materialize_proxy_training_set.py`로 train/validation/calibration을 family 단위로
   분리한다. validation은 checkpoint 선택, calibration은 온도·상향 임계값에만 사용한다.
6. 동결 Proxy 평가 1,000건도 품질 게이트 뒤 별도 조립한다. 아래 명령으로 개발 200/
   최종 800을 만든다.

```powershell
python scripts/split_proxy_eval_suite.py `
  --input <quality-gated-frozen-proxy-1000.jsonl> `
  --out-dir datasets/proxy_gold/eval_suites/<new-immutable-run-id>
```

이 명령은 정확히 TS 40/S1 50/S2 50/S3 60의 개발 200건과 TS 160/S1 200/S2 200/S3 240의
최종 800건을 만든다. document family는 어느 쪽에도 동시에 들어갈 수 없다.

## 재학습·교체 판단

1. v5는 변경하지 않고 별도 출력 경로에 v2 후보 모델을 학습·finalize한다.
2. 개발 200건으로 모델 구조·데이터 구성·운영 임계값을 반복 조정한다.
3. 후보가 고정된 뒤에만 최종 800건과 공개 S3 홀드아웃으로 v5와 비교한다.

```powershell
python scripts/compare_proxy_models.py `
  --frozen-corpus datasets/proxy_gold/eval_suites/<run>/final_800.locked.jsonl `
  --final-suite-manifest datasets/proxy_gold/eval_suites/<run>/manifest.json `
  --baseline-model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b `
  --candidate-model-dir <finalized-v2-model-dir> `
  --baseline-legacy-training-attestation <v5-attestation.json> `
  --candidate-training-manifest <v2-training-manifest.json> `
  --public-s3-challenge datasets/proxy_gold/public_s3_challenges/public-s3-300-blind-20260808-v2/records.jsonl `
  --comparison-mode raw_model
```

교체 조건은 다음을 모두 만족해야 한다.

- TS/S1의 하향 오분류가 v5보다 증가하지 않을 것
- 공개 S3의 과잉경보와 심각 과잉경보가 v5보다 증가하지 않을 것
- 최종 800 Proxy 회귀 지표와 근거 제시 품질이 v5보다 개선될 것
- 학습셋, 개발 200, 최종 800, 공개 홀드아웃의 ID·family·본문 해시 교집합이 0일 것

어느 하나라도 충족하지 못하면 v5를 유지하고, 실패한 등급·시나리오만 보완한다.
고객사 사람이 확정한 문서는 이후에만 Locked Gold로 승격하며, 다음 학습 주기의 별도
고품질 입력으로 취급한다.
