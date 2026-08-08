# KL 프록시 골든셋·학습 코퍼스 장기 상태

> 기준일: 2026-08-08
>
> 상태: **PILOT_FAILED / REDESIGN_IN_PROGRESS / CUSTOMER_ACCURACY_UNVERIFIED**
>
> 이 문서는 고객사 실문서를 받기 전 KL 테스트 환경에서 수행할 프록시 데이터 구축의 장기 기준이다.
> 상태를 바꿀 때는 완료된 run의 manifest와 SHA-256을 함께 기록한다. 계획이나 dry-run만으로 완료 처리하지 않는다.

## 1. 목적과 용어

목표는 고객사 실문서를 사용할 수 없는 단계에서 실제 업무문서와 최대한 유사한 문서 구조를 만들고,
모델 학습·회귀 차단·임계값 보정 절차를 먼저 완성하는 것이다. 고객사 사람 검수는 그 다음 단계다.

이 문서에서 사용하는 두 산출물은 역할이 다르다.

- **프록시 골든 1,000건**: 고정 평가셋이다. 학습에 절대 사용하지 않는다.
- **학습 풀 3,000건**: 모델 학습·검증·보정용이다. 프록시 골든이나 공개 평가셋과 겹치면 안 된다.
- **고객사 골든**: 추후 고객사 실문서에 사람 검수를 붙여 만드는 별도 정답지다. 위 두 프록시 산출물과
  합치거나 같은 정확도 근거로 취급하지 않는다.

따라서 여기서 말하는 “골든 1,000건”은 납품 수량을 맞춘 **동결 프록시 평가셋**이지,
고객사 실문서에 전문가가 서명한 최종 정답지라는 뜻이 아니다.

### 1.1 결정 배경 스냅샷

다음 수치는 2026-08-08 대화에서 제공된 기존 KL 조사 스냅샷이며, 이 저장소의 immutable artifact로
재검증한 현재 수치가 아니다. 새 run의 완료 수량에 합산하지 않는다.

- 기존 정본 777건 중 LLM 단독·합의 라벨은 591건(약 76%), 사람 서명은 1건으로 보고되었다.
- 등급 분포는 TS 55, S1 56, S2 89, S3 577로 보고되었다.
- 기존 합성 run의 uncertain bucket은 1,329건이며 모두 synthetic으로 보고되었다.
- 별도 capstone 관측에서 합성-only 학습의 실문서 F1 0.26, 실데이터 학습의 F1 0.736이 보고되었다.

이 스냅샷은 “라벨을 더 많이 만드는 것”과 “실문서 일반화”가 같은 문제가 아니라는 결정 근거다.
정확한 재인용이 필요하면 당시 KL run과 capstone 원본 아티팩트를 다시 확인한다.

## 2. 주장할 수 있는 것과 없는 것

### 허용되는 주장

- 같은 동결 프록시 1,000건에서 모델 변경 전후의 회귀 여부를 비교했다.
- 공개 실문서 S3 challenge에서 과대분류·오탐 경계를 측정했다.
- 합성 문서의 구조 다양성, 근거 카드, 라벨 계약과 재현 가능한 데이터 계보를 확보했다.
- 학습·평가·공개문서 출처가 ID, family, 공백 제거 정규화 본문 SHA-1 기준으로 분리되었다.

### 금지되는 주장

- 프록시 평가 통과를 고객사 실운영 정확도, 고객사 TS/S1 미탐률 또는 90% 정확도 보증으로 표현하지 않는다.
- 합성 문서에 LLM 심판을 붙인 결과를 사람 검수 골든 또는 전문가 합의로 표현하지 않는다.
- 공개 S3 문서 300건으로 비공개 TS/S1 문서 성능이 검증되었다고 주장하지 않는다.
- 프록시 골든 1,000건을 고객사 실문서 기반 골든 1,000건과 동일시하지 않는다.
- 고객사 실문서·사람 검수 전에는 `customer-real`, `production accuracy`, `human-reviewed` 수치를 만들지 않는다.

프록시는 실질적인 개선 수단이지만, “기계 생성 문서 + 기계 심판”의 한계를 없애지는 못한다.
최종 고객사 정확도 주장은 반드시 고객사 실문서와 사람 검수 단계에서 새로 측정한다.

## 3. 고정 수량 계약

### 3.1 동결 프록시 골든 1,000건 — 평가 전용

| 등급 | 문서 출처 | 건수 |
|---|---|---:|
| TS | synthetic | 200 |
| S1 | synthetic | 250 |
| S2 | synthetic | 250 |
| S3 | public_real (KOGL-1 공개 원문) | 300 |
| **합계** | **synthetic 700 + public_real 300** | **1,000** |

근거 카탈로그는
[`datasets/proxy_gold/scenario_catalog.v1.json`](../datasets/proxy_gold/scenario_catalog.v1.json)이며,
`catalog_split_role=frozen_proxy_eval_only`, `training_use_permitted=false`,
`evaluation_use_permitted=true` 계약을 따른다.

합성 문서가 "문서 전체가 실제로 공개됨"을 사실대로 충족할 수 없으므로, S3 300건은 생성하지 않고
라이선스가 확인된 공개 원문으로 고정한다. TS/S1/S2 700건에는 시나리오·프로필 quota를 적용하고,
S3에는 출처·라이선스·family 다양성 계약을 적용한다. 이 1,000건은 **고등급 합성 회귀/보정 + 공개문서
S3 오탐 경계**용 프록시이며, 고객사 실문서 정확도 근거는 아니다.

### 3.2 최종 학습 풀 3,000건 — 학습 전용

| 등급 | synthetic | public_real | 최종 합계 |
|---|---:|---:|---:|
| TS | 750 | 0 | 750 |
| S1 | 750 | 0 | 750 |
| S2 | 750 | 0 | 750 |
| S3 | 450 | 300 | 750 |
| **합계** | **2,700** | **300** | **3,000** |

근거 카탈로그는
[`datasets/proxy_gold/training_scenario_catalog.v1.json`](../datasets/proxy_gold/training_scenario_catalog.v1.json)이다.
최종 합성 subset에 대해서만 225개 family, 12개 문서 shape, 3개 길이 profile의 균형을 계산한다.
공개 실문서에는 합성 shape를 억지로 부여하지 않는다.

두 합성 카탈로그는 `svm-boundary-profile-v1`의 **현실적으로 성립 가능한 21개 S/V/M
프로필**만 사용한다. 수학적 27개 조합 중 문서 전체가 공개(`S=0`)인데 문서 자체에
접근통제(`M>0`)가 있다고 가정하는 6개 조합은 의미상 모순으로 제외한다. 평가 1,000건과
합성 학습 2,700건 모두 시나리오별 quota를 먼저 고정하며, 프로필별 quota는 그 합으로
결정된다. 대표 4개 프로필만으로 전체 run을 대체하지 않는다.

- 평가 프로필 quota: TS 각 10, S1 25, 대표 S2 5·나머지 S2 각 4, 공개 S3 각 3,
  S3는 합성 quota에서 제외하고 공개 원문 300건의 출처·family quota로 고정한다.
- 학습 프로필 quota: TS 각 25, S1 50, 대표 S2 10·나머지 S2 각 8, S3는 평가와 같은
  아키타입별 quota(15개 아키타입, 총 2,700).
- 최종 학습 조립은 시나리오·프로필·등급·family·shape를 한 번의 정수 제약으로 동시에
  만족하는 경우에만 성공한다. 어느 축도 자동 완화하지 않는다.

훈련 카탈로그의 base 목표 자체가 합성 2,700건(TS/S1/S2 각 750, S3 450)이다. quality-v2
탈락 여유분은 base 목표에 섞지 않고 10-shard 생성기의 candidate-buffer/oversample 배수로만 만든다.
공개 실문서 S3 300건은 별도 provenance 경로에서 결합해 최종 3,000건을 완성한다.
현재 정본은 `public-s3-train-300-20260808-v3`이다. 정책브리핑의 실제 정책기사 90건과
보도자료 210건을 문서유형별 exact quota로 고정했으며, 49개 기관·300개 고유 family를 포함한다.
모두 KOGL-1이고 게시일 범위는 2026-06-28~2026-08-07이다. 공개문서 문체와 S3 과대분류 경계를
보강하지만, 고객사 내부 기술보고서·검토서의 실제 레이아웃이나 TS/S1 분포 근거로 해석하지 않는다.

### 3.3 공개 S3 평가 경계

| 아티팩트 | 용도 | 건수 | records SHA-256 |
|---|---|---:|---|
| `public-s3-300-20260808-v1` | 동결 프록시의 공개원문 S3 slice | 300 | `a03885ef776db07648bf53a91684f63b5009f0880998136ddfeb2d6243fe4ed4` |
| `public-s3-300-blind-20260808-v2` | 봉인된 최종 blind 평가 | 300 | `dd4f94e1533fea607c72455f1d1861cc2d0667a5ebb1853df8846f03ee49b802` |

두 artifact는 모두 `evaluation_only`이며 학습 금지다. v1은 동결 1,000에 포함되므로 별도 독립
challenge나 튜닝 입력으로 다시 쓰지 않는다. v2는 튜닝 중 반복 조회하지 않고 모델·운영점 lock 뒤
최종 후보에 대해 한 번만 사용한다.

## 4. 모델·출처·용도 격리

### 4.1 모델 격리

모델 잠금은 [`deploy/proxy_gold_runtime/model-lock.json`](../deploy/proxy_gold_runtime/model-lock.json)을
정본으로 사용한다.

| 역할 | 모델 | 계약 |
|---|---|---|
| 생성기 | `qwen3:14b` | 합성 문서 생성 전용 |
| 1차 독립 심판 | `gemma3:12b` | 생성기와 다른 canonical model이어야 함 |
| shadow 심판 | 기본 `qwen3:14b` | 보조 일관성 신호이며 사람 검수나 완전 독립 합의로 주장하지 않음 |
| 후보 생성기 | `qwen3:4b` | 동일 시나리오 품질 비교를 통과하기 전 pilot-only |

모든 실행은 모델 manifest digest를 고정한다. `noop`, `unknown`, 미고정 revision, 생성기와 동일한
1차 심판은 production 데이터 run에서 거부한다. 런타임은 한 번에 모델 하나만 적재하며 제품 API를
재시작하거나 제품 네트워크에 공개 포트를 열지 않는다.

실행 시 생성기와 심판은 Ollama `/api/tags`에서 모델 alias를 해석하고 live digest가 고정 digest와
같은지 자동 검증한 뒤 attestation을 run envelope에 기록하며, commit/resume 직전에도 다시 검증한다.
불일치·누락·접속 실패는 fail-closed다. `--dry-run`은 네트워크에 접속하지 않으므로
`pending_live_verification`만 기록하며 실제 모델 검증이나 실행 성공 근거가 아니다.
정확한 이름 또는 정확한 `:latest` alias 하나만 허용하며, 중복 alias는 거부한다. 공개·클라우드
엔드포인트와 credential이 포함된 URL도 거부한다. 생성 row, manifest, `COMPLETE.json`, 생성·심판
샤드 controller가 동일한 stable binding과 endpoint identity를 가리켜야 하며 controller는 시작·재개와
최종 shard 재검증 때 live inventory를 독립적으로 다시 확인한다.

KL 기준 모델은 제품 컨테이너를 수정하지 않고 읽기 전용 복제원으로만 사용한다. 현재 확인한 경로는
`/home/kopia/poc/artifacts/classifier_p1_v5_clean/v-fe4b386b`이고, 파일 트리 SHA-256은
`7ff4c78156002f857121e6ddd724d40c0d38a59d91b9489ab1af9ab1c4d02036`이다. 비교 시 원본 경로를
직접 쓰지 않고 이 해시를 검증한 격리 복제본을 사용한다.

### 4.2 용도 및 출처 격리

- 합성 평가 카탈로그: `frozen_proxy_eval_only`; 학습 불가.
- 합성 학습 카탈로그: `train_pool_only`; 평가 근거로 사용 불가.
- 공개 challenge v1/v2: `public_real`, `evaluation_only`; 학습 불가.
- 공개 학습 300: `public_real`, `training_only`; 평가·골든 점수·모델 선택에 사용 불가.
- 고객사 실문서: 현재 범위에 없음. 추후 별도 intake와 별도 사람 검수 계보를 사용한다.
- AI Hub 71813: 승인 receipt와 허용 범위가 확정되기 전 현재 3,000건에 포함하지 않는다.
- DART 등 학습 허용이 확정되지 않은 출처는 포함하지 않는다.

모든 조립 단계는 다음 세 식별자의 교집합이 0인지 검사한다.

1. `doc_id`
2. `document_family_id`
3. 공백 제거 정규화 본문 SHA-1

## 5. 현재 상태

2026-08-08 1차 생성·심판 파일럿은 `gold 0 / uncertain 8`로 production 차단 판정을 받았다.
실행 해시와 상세 원인은
[`KL_PROXY_GOLD_PILOT_20260808.md`](KL_PROXY_GOLD_PILOT_20260808.md)에 기록한다.

### 완료

- [x] 평가 전용 및 학습 전용 카탈로그를 분리하고 permission 계약을 코드에서 fail-closed로 검증한다.
- [x] 합성 학습 카탈로그에 225 families, 12 shapes, 3 length profiles를 고정했다.
- [x] 두 합성 카탈로그를 현실적으로 성립 가능한 21개 S/V/M 경계 프로필로 확장하고,
  시나리오·프로필별 exact quota와 계보를 생성→심판→조립→materialize 경로에 연결했다.
- [x] 학습 조립기는 2배 후보 5,400건에서 합성 2,700건을 시나리오·프로필·등급·family·shape
  동시 제약으로 결정적으로 선택하며, 부족하거나 공동 제약이 불가능하면 상세 부족분과 함께 실패한다.
- [x] 생성 run의 oversample과 보존 candidate buffer를 분리하고 family 단위 결정적 sharding/resume을 구현했다.
- [x] 심판 경로에 `intended_use=training|evaluation`, 생성기/1차 심판 독립성, semantic/evidence/document-quality v2를 연결했다.
- [x] 공개 평가 v1 300건과 blind v2 300건을 immutable artifact로 고정했다.
- [x] 공개 학습용 S3 정본 `public-s3-train-300-20260808-v3`를 실제 공개문서에서 만들었다.
  정책기사 90건·보도자료 210건, 49개 기관, 300 unique families이며 모두 KOGL-1이고 귀속정보를
  보존한다. records SHA-256은
  `a2429dc0b3ba3a0165a13a932105662f430def21f8502743e43408588c13694a`, manifest SHA-256은
  `eef289d54e844d8d85acb583f7b5b68200eff009386370d5d3563190fc40fe6d`다.
- [x] 공개 학습 300과 공개 평가 v1/v2의 ID/family/text overlap이 모두 0임을 검증했다.
- [x] 최종 조립기는 정확히 3,000건과 고정 origin×label 분포만 허용하고 immutable manifest/COMPLETE를 만든다.
- [x] materializer는 조립기의 count/bytes/SHA-256/run ID/code contract envelope를 재검증하며 production 3,000의 우회를 금지한다.
- [x] 관련 회귀 테스트와 Ruff/format 검사를 통과했다.

### 진행 전 또는 미완료

- [~] 결정적 fact ledger·표 헤더 해석·생성 후 불변 검사는 구현했다. 대표 8 재파일럿은 TS/S1/S2 후보 6건만 만들고 합성 S3 2건을 구조적으로 거부했으며, 생성 지시문/판정용어 누출을 차단한 새 프롬프트로 재파일럿해야 한다.
- [x] `proxy-fact-ledger-v2`는 모델이 프롬프트에 없는 숫자를 새로 쓰면 거부한다. 코드가 붙이는 검산 부록은 유일한 정량 사실 출처이며, 허용 숫자는 scenario·instance·문서계열·공개범위·위험 설명의 명시 입력에서만 값 기준으로 비교한다.
- [ ] 동결 프록시 골든 1,000건의 실제 생성·quality-v2 심판·최종 조립 아티팩트가 아직 없다.
- [ ] 합성 학습 후보의 production generation run과 quality-v2 judging run이 아직 없다.
- [ ] 최종 학습 풀 3,000건과 materialized train/validation/calibration run이 아직 없다.
- [ ] 새 3,000건으로 분류기 학습·온도 보정·모델 아티팩트 생성이 아직 없다.
- [x] proxy candidate mode는 validation을 epoch checkpoint 후보 생성에만 쓰고 test/frozen 추론,
  `val_logits.jsonl`, validation 기반 `temperature.json`, deployable `v-*` 산출을 금지한다. 각 checkpoint는
  materialization manifest와 네 artifact(train documents/chunks, validation, calibration), base-model
  revision/state hash, args/seed/source SHA에 `TRAINING_EXECUTION.json`으로 결합된다.
- [x] finalizer는 validation에서 모든 epoch checkpoint를 실제 M5와 같은 character chunk + fast-tokenizer
  overflow + 문서단위 확률 집계로 다시 선택한다. 선택된 모델은 독립 calibration의 raw window logits를
  한 번 캐시하고, 각 후보 T마다 window softmax→길이 가중평균→TS/S1 max→재정규화를 다시 계산한 뒤
  T를 적합하고 같은 문서점수로 τ를 고정한다. 보정 경계 최적값·fast overflow fallback·degenerate τ는
  fail-closed이며, 실패 시 COMPLETE/model candidate가 생기지 않는다.
- [ ] 동결 1,000, 공개 v1, blind v2에 대한 새 모델 평가와 기존 모델 A/B 비교가 아직 없다.
- [ ] 고객사 실문서 및 고객사 사람 검수는 후속 단계이며 현재 정확도 근거에 포함되지 않는다.

## 6. 다음 실행 순서

1. **사전 점검**: 제품 API `http://localhost:8000/api/v1/healthz/live`, `docker ps`, `nvidia-smi`,
   여유 GPU 메모리, 모델 manifest digest를 기록한다. 로컬 release archive는
   `artifacts/proxy_runtime/proxy-runtime.tar.gz`, KL release/run 루트는 각각
   `/home/kopia/proxy_gold_runtime/releases`와 `/home/kopia/proxy_gold_runtime/runs/runner`다. 공개 immutable
   artifact 루트는 `/home/kopia/proxy_gold_runtime/artifacts`이고 runner에는 `/proxy-artifacts:ro`로
   mount한다.
   archive root가 `proxy-runtime/`이므로 새 staging 디렉터리에 반드시
   `tar --strip-components=1`로 풀고 SHA-256과 핵심 entrypoint를 검증한 뒤 최종 release 디렉터리로
   원자적으로 이동한다. 고정 API 이미지 안에서 `numpy`, `scipy`, `openai` import와
   `scipy.optimize.milp` callable smoke를 통과시킨다. 제품 API/worker는 재시작하지 않는다.
   release 디렉터리명=archive SHA-256, exact runner·제품 image ID, shipped model-lock와 live
   Ollama `/api/tags`, 제품 health, GPU 상태를 `proxy-runtime-preflight-receipt-v1` JSON과
   SHA-256 sidecar로 먼저 고정하고, 그 hash 앞 12자를 파일럿/controller `BATCH_ID`에 쓴다.
   현재 run envelope가 receipt를 직접 내장하지 않으므로 operator record의 receipt SHA 참조는
   필수 provenance bridge다.
2. **동결 평가 후보 생성**: 평가 카탈로그로 별도 generation run을 만들고 `intended_use=evaluation`으로
   quality-v2 심판을 실행한다. 먼저 대표 4등급 산술 파일럿을 통과시키고, 이어서 S/V/M 경계조합
   파일럿을 통과시킨 뒤에만 전체 수량을 시작한다. 두 파일럿 모두 controller가 아니라
   `build_proxy_scenarios.py`와 `judge_proxy_candidates.py` direct CLI로 실행한다. 대표 파일럿 exact 8은
   `process-optimization`, `pricing-policy` × `ts-s2-v2-m2`, `s1-s2-v2-m0`,
   `s2-s1-v1-m1`, `s3-s0-v0-m0`이고, 경계 파일럿 exact 21은
   `process-optimization` × 고정 21개 factor profile이다. 각 scenario 최종 목표는 1개다.
   quality reject 여유를 위해 direct build에는
   `--per-scenario 1 --oversample-factor 2.5 --candidate-buffer-factor 1.0`을 사용한다. 즉 scenario당
   2~3회 시도하고(대표 8은 총 24회, 경계 21은 결정적 배분으로 총 53회) committed candidate는
   각각 1개이며 exact 8/21 게이트를 완화하지 않는다.
   반복 가능한 전체 명령은
   [`deploy/proxy_gold_runtime/README.md`](../deploy/proxy_gold_runtime/README.md)의
   “Direct pilot gate”를 따른다. 누락·reject·uncertain profile만 새 run ID로 재실행한다.
3. **프록시 골든 1,000 조립·동결**: `scripts/assemble_proxy_gold.py`로 200/250/250/300을 맞추고
   아티팩트 hash를 기록한다. 이 시점부터 학습 입력으로 사용하지 않는다.
4. **학습 후보 생성**: 학습 카탈로그를 family shard로 나눠 생성한다. 최초 production pass는
   `--candidate-buffer-factor 2 --oversample-factor 2.5`로 합성 후보 5,400건과 계획 시도 6,750건을
   사용한다. 이는 고정 최종 수량이 아니라 quality reject를 흡수하는 buffer다. 전체 run을 무작정
   확대하지 않고 1차 심판 후 부족한 scenario/factor profile만 direct CLI로 targeted top-up한다.
   품질 게이트, 최종 2,700건, 등급·scenario·profile·family·shape exact 제약은 완화하지 않는다.
   최초 run과 모든 top-up은 서로 다른 generation namespace를 사용한다. 10-shard controller는 각
   namespace를 shard run ID로 고정하고, direct CLI는 새 `--run-id`에서 namespace를 유도하거나 같은 값을
   `--generation-namespace`로 명시한다. namespace가 다르면 `doc_id`와 resume key가 겹치지 않지만,
   의미상 같은 계열의 `document_family_id`는 의도적으로 유지하여 family 단위 누수를 계속 차단한다.
   중단된 generation controller를 명시적으로 재개할 때 완료 child는 전체 envelope 재검증 후 건너뛰고,
   미완료 regular child는 같은 디렉터리의 journal과 정확한 원계약을 `--resume-run`으로 이어간다.
   symlink·계약 불일치·손상 journal은 실패로 남기며 완료 controller는 재개하지 않는다.
5. **학습 후보 심판**: 각 committed generation run을
   `scripts/judge_proxy_candidates.py --intended-use training`으로 판정한다. 1차 심판은 `gemma3:12b`,
   생성기는 `qwen3:14b`로 고정한다.
6. **부족 셀만 재생성**: 등급·시나리오·factor profile·family·shape별 eligible 수량을 보고 부족한
   shard/프로필만 재실행한다. 초기 buffer 2에서는 모든 shard가 끝났어도 일부 부족 셀 때문에 심판
   controller가 `COMPLETE.json`을 커밋한 채 exit 1, `target_met=false`를 반환할 수 있으며 이는 정상적인
   targeted top-up 분기다. orchestration은 `set -e`로 즉시 버리지 말고 exit code, COMPLETE,
   `stats.json`의 `gold_shortfall_by_scenario`와 `gold_shortfall_by_factor_profile`을 읽는다. 완료 controller는
   resume하지 않는다. positive 부족 셀만 새 direct generation/judging run ID로 실행하며 production
   top-up에는 `--candidate-buffer-factor 2 --oversample-factor 2.5`를 사용하고, 아직 부족하면 같은 셀만
   다시 반복한다. 최초 10개 shard와 모든 top-up의 `gold_candidate.jsonl`을 조립기의 반복 가능한
   `--input`으로 함께 전달한다. 두 shortfall map이 모두 비어야 정확 조립을 시도하며, 이미 충분한
   family를 무작정 다시 생성하거나 품질·quota를 완화하지 않는다.
   중단된 judging controller를 명시적으로 재개할 때 완료 child는 재검증 후 건너뛴다. judge child에는
   부분 재개 계약이 없으므로 미완료 디렉터리를 덮어쓰지 않고 보존하며, 원 shard와 controller 계약에
   결합된 결정적 recovery run ID로 새 child를 실행한다. recovery도 미완료이면 다음 재개에서 그것을
   보존하고 다음 recovery ID를 사용한다. 조립 입력에는 controller가 검증한 최종 committed child만 쓴다.
7. **정확한 3,000 조립**: `scripts/assemble_proxy_training_pool.py`에 judged gold 후보, 공개 학습 300,
   동결 1,000, 공개 v1/v2를 입력한다. 합성 2,700의 315개 시나리오와 21개 프로필 quota,
   final grade/origin 분포, family/shape/length 균형 및 세 종류 overlap=0을 모두 확인한다. 공개 학습 v3는
   KL의 `/home/kopia/proxy_gold_runtime/artifacts/public_s3_training/public-s3-train-300-20260808-v3`를
   사용한다. 공개 v1/blind v2도 artifacts 하위 별도 immutable 디렉터리로 올리되, blind v2는 최종
   모델·temperature·임계값 lock 전에는 본문 열람·평가·튜닝을 하지 않고 이 단계에서는 envelope/hash와
   leakage-block 경계로만 취급한다.
8. **학습셋 materialize**: `scripts/materialize_proxy_training_set.py`로 family 단위
   train/validation/calibration 분리와 train-only evidence-aware chunk를 만든다.
9. **학습·보정**: validation은 best checkpoint 선택에만, calibration은 temperature 적합에만 사용한다.
   두 단계 모두 실제 서빙과 같은 chunk/window 문서단위 집계를 사용한다. 두 입력과 logits의
   SHA-256을 기록하고 `temperature.json`까지 같은 model run에 고정한다. severity escalation
   operating point도 calibration에서만 고른다. 동결 1,000이나 blind v2를 임계값 튜닝에 쓰지 않는다.
   학습은 `p1_train_classifier.py --mode full --proxy-candidate-mode
   --proxy-training-run-dir <materialized-run> --output-dir <new-checkpoint-root>`로 실행하고, 이어서
   `finalize_proxy_classifier.py --training-run-dir <materialized-run> --checkpoint-root
   <new-checkpoint-root>`를 실행한다. finalizer 결과의 역할은 `proxy_deployment_candidate`이며
   `production_eligible=false`, `customer_document_deployment_approved=false`다.
   bundle 비교 시에는 finalization manifest와 COMPLETE뿐 아니라 최종화 시점의
   Hugging Face 모델 파일 트리까지 재해시하여, 정상 메타데이터를 다른 가중치에
   붙인 혼합 bundle을 거부한다.
   학습 직전에는 별도 proxy Ollama 컨테이너만 중지하고 제품 API/worker는 계속 유지한다. 생성·심판
   `runner`는 GPU를 직접 받지 않으며 Ollama endpoint를 사용하고, 학습용 `training_runner`만
   `--gpus all`을 받는다. 현재 baseline을 제품 경로에서 직접 mount하지 않고 별도 격리 복사하여
   tree SHA-256
   `7ff4c78156002f857121e6ddd724d40c0d38a59d91b9489ab1af9ab1c4d02036`을 재검증한 뒤
   `/base-model/v-fe4b386b:ro`로 mount한다. 학습 컨테이너는 `--network none`, Hugging Face offline
   환경변수, 명시적 `--base-model /base-model/v-fe4b386b`, `--no-mlflow`를 사용한다. 정확한 runner와
   copy/hash 명령은 runtime README의 “Offline GPU training runner contract”를 따른다.
10. **평가·비교**: 동결 1,000으로 모델 구성요소 회귀와 고정 operating point의 서빙 경로를 각각
    평가하고, 공개 v1으로 S3 오탐을 진단한다. 모델·temperature·임계값을 고정한 뒤 blind v2를
    한 번 최종 평가한다. `raw_model`은 양쪽 모두 T=1/argmax, `bundle_operating_point`는 양쪽 모두
    독립 calibration에 결합된 T/τ와 finalization COMPLETE가 있을 때만 허용한다. 현재 legacy baseline은
    τ provenance가 없으므로 raw 비교만 가능하며, 같은 calibration으로 별도 finalize하기 전 full-bundle
    A/B는 N/A다. 운영 실행에서는 기본값에 기대지 말고 반드시
    `--comparison-mode raw_model` 또는 `--comparison-mode bundle_operating_point`를 명시한다.
    결과에는 항상 proxy/public claim scope를 붙인다.
11. **후속 고객사 검수**: 고객사 실문서가 들어오면 별도 corpus와 별도 metric으로 진행한다.
    현재 운영 방향은 검수자 1명과 일부 표본의 시간차 blind self-recheck 기준이다. 따라서 결과는
    단일 사람 서명과 동일 검수자의 재현성 점검이지, 검수자 간 일치도나 이중 전문가 합의가 아니다.

## 7. 중단·실패 기준

- 제품 API health 악화, 제품 컨테이너 재시작, GPU 여유 메모리 2 GiB 미만이면 batch를 중단한다.
- 모델 manifest 불일치, generation/judge 동일 1차 모델, permission 불일치, envelope/hash 불일치는 실패다.
- frozen/public 평가셋과 세 식별자 중 하나라도 겹치면 최종 조립하지 않는다.
- 정확한 등급·origin·315개 시나리오·21개 factor profile 수량 또는 합성 family/shape/length 균형을
  만족하지 못하면 상세 부족분을 보고하고 재생성한다. 조립기는 어떤 quota도 묵시적으로 완화하지 않는다.
- partial run, raw JSONL, `allow-unattested-legacy-input` 결과는 production 조립 입력으로 승격하지 않는다.
- blind v2 결과를 본 뒤 같은 모델·임계값을 다시 튜닝하고 같은 v2를 “최종 blind”로 재사용하지 않는다.

## 8. 핵심 파일

- 평가 카탈로그: [`datasets/proxy_gold/scenario_catalog.v1.json`](../datasets/proxy_gold/scenario_catalog.v1.json)
- 학습 카탈로그: [`datasets/proxy_gold/training_scenario_catalog.v1.json`](../datasets/proxy_gold/training_scenario_catalog.v1.json)
- 공개 출처 정책: [`datasets/proxy_gold/PUBLIC_REAL_TRAINING_POLICY.md`](../datasets/proxy_gold/PUBLIC_REAL_TRAINING_POLICY.md)
- 공개 학습 300 manifest: [`datasets/proxy_gold/public_s3_training/public-s3-train-300-20260808-v3/manifest.json`](../datasets/proxy_gold/public_s3_training/public-s3-train-300-20260808-v3/manifest.json)
- 모델 잠금: [`deploy/proxy_gold_runtime/model-lock.json`](../deploy/proxy_gold_runtime/model-lock.json)
- 로컬 LLM 런타임: [`deploy/proxy_gold_runtime/README.md`](../deploy/proxy_gold_runtime/README.md)
- 생성기: [`scripts/build_proxy_scenarios.py`](../scripts/build_proxy_scenarios.py)
- 심판: [`scripts/judge_proxy_candidates.py`](../scripts/judge_proxy_candidates.py)
- 프록시 골든 조립: [`scripts/assemble_proxy_gold.py`](../scripts/assemble_proxy_gold.py)
- 학습 풀 조립: [`scripts/assemble_proxy_training_pool.py`](../scripts/assemble_proxy_training_pool.py)
- 학습셋 materialize: [`scripts/materialize_proxy_training_set.py`](../scripts/materialize_proxy_training_set.py)
- proxy checkpoint 후보 학습: [`scripts/p1_train_classifier.py`](../scripts/p1_train_classifier.py)
- serving-faithful 선택·T/τ 보정: [`scripts/finalize_proxy_classifier.py`](../scripts/finalize_proxy_classifier.py)
- 문서 window logits/보정 core: [`src/lloydk/proxy_training_finalization.py`](../src/lloydk/proxy_training_finalization.py)
- raw/bundle 비교: [`scripts/compare_proxy_models.py`](../scripts/compare_proxy_models.py)
