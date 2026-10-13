# 로컬 분류 모델 연결 점검 v1

2026-09-15. 고정된 중립 문장 2개로 체크포인트 연결만 점검하는 별도 경로다.
벤치마크 원고·정답·골든셋은 입력하지 않는다. 학습, 채점, 정확도 측정, 데이터 사용 허가 변경이 아니다.

## 1. 체크포인트와 확인한 범위

대상은 `artifacts/classifier_p1_v5_clean/v-fe4b386b`이다. 이름이 아니라 다음 실제 바이트 지문으로 식별한다.

| 항목 | 실제 확인 |
| --- | --- |
| 구조 | `DebertaV2ForSequenceClassification`, 12층, hidden 768, 어휘 130,000 |
| 입력 한도 | 모델 설정·토크나이저 메타데이터 모두 특수 토큰 포함 512 |
| 라벨 열 | 모델 설정의 `0=TS, 1=S1, 2=S2, 3=S3`, 역방향 매핑 일치 |
| 가중치 | `model.safetensors`, 743,562,232바이트, 202개 F32 텐서 |
| 분류 헤드 | weight `[4,768]`, bias `[4]` |
| 가중치 SHA-256 | `ca3a4b59679ea4c828ba9768701e0e0a5589ffe6a2f027fd086fef95119e5e65` |
| 체크포인트 결합 SHA-256 | `0d0ac8d90bf7d2db2ea2ea0c5a61d16e6e73b5db049349e336a77ed7ba8beab2` |

결합 지문은 config/tokenizer/tokenizer_config/model.safetensors 네 파일의 바이트 지문·크기를 결합한다.
토크나이저 SHA-256은 `f33819f6e8544c27450ebe253b3a882d9c2a7148d4f0ce15129425712a9993be`다.
구조 검사는 가중치 전체 값의 유한성이나 실제 추론 성공을 대신하지 않는다.

저장된 모델 설정은 transformers 5.9.0, 로컬 설치 메타데이터는 transformers 5.13.0이다.
설치된 다른 관련 패키지는 torch 2.12.1+cu130, tokenizers 0.22.2, safetensors 0.8.0이다.
CUDA 패키지가 설치되어 있어도 이 경로는 CPU만 사용한다. 설치 정보만으로 버전 호환 성공을 주장하지 않는다.

## 2. 구현 계약

`customer_model_connection_v1.py`는 다음 두 진입점을 제공한다.

- `inventory_checkpoint(model_dir)`: 파일 지문, 안전한 헤더, 라벨·입력 계약, 고정 문장 토큰 확인. torch/transformers import와 모델 로드는 하지 않는다.
- `run_connection_smoke(model_dir, expected_checkpoint_sha256=...)`: 검토한 결합 지문과 정확히 일치하는 체크포인트에 고정 2문장을 각각 두 번 전달한다.

임의 본문·정답·벤치마크 팩·원고 파일 경로를 받는 인자가 없다. 고정 등록 문장의 교체도 등록 지문 검증으로 차단한다.
두 문장은 회의실 시계/달력, 공책 읽기를 다루는 연결 점검용 짧은 문장이다. 기대 등급·정답이 없으며 특수 토큰 포함 26/29토큰이다.

로더는 기존 운영 inference pipeline과 분리했다.

- 명시적 DebertaV2 클래스만 사용한다. `local_files_only=True`, `trust_remote_code=False`, `use_safetensors=True`, `weights_only=True`를 고정한다.
- 모델·토크나이저 remote/custom code 설정, adapter, 모호한 가중치 파일, 모호한 라벨 매핑을 거절한다.
- 모델 파일은 safetensors 헤더의 shape/dtype/offset/연속성/파일 크기를 검사한다. 로딩 후 누락·초과·불일치 가중치가 있으면 실패한다.
- CPU float32, `eval()`, `inference_mode()`로 실행한다. 암묵적 GPU 선택이나 학습 모드가 없다.
- 토크나이저의 저장된 잘림·패딩을 비활성화한다. 전체 토큰 길이가 한도를 넘으면 실패하며 자르거나 집계하지 않는다.
- 전용 프로세스 안에서 로드·순방향 실행 중 socket 연결과 DNS 함수도 차단한다. 종료/실패 시 원래 함수를 복원한다.
- `temperature.json`, 운영 임계값, 규칙엔진, 검수/확정 로직은 적용하지 않는다. 기존 모델이나 설정을 수정하지 않는다.

socket 차단은 프로세스 전역 함수에 잠시 적용되므로 공유 서버 안에서 호출하지 않고 독립 CLI 프로세스로 실행한다.
이 도구는 운영 배포/평가 실행기가 아니다.

## 3. 결과를 해석하는 방법

실제 연결 점검은 문장 2개×2회, 배치 크기 1로 정확히 4회의 순방향 호출을 요구한다.
각 logit은 유한해야 하며 4개 열이어야 한다. 동일 입력 반복의 최대 절대 차이는 `1e-6` 이하여야 한다.
원시 logit·열 번호·체크포인트의 라벨 매핑·반복 차이·호출 수를 기록한다.
출력된 라벨은 연결 확인용 모델 출력이며 정답이나 문서 보호등급의 객관적 확정이 아니다.

시험 더블을 이용한 단위 테스트는 `test_double_smoke_only`, 실제 모델 호출 0회로 별도 표기한다.
`accuracy_measured=false`, `benchmark_documents_used=0`, 모든 데이터 허가 플래그 `false`를 유지한다.
입력 어댑터를 통과한 미승인 원고에 모델을 실행하거나 고객 성능을 채점하는 다음 단계는 별도 채택 절차가 필요하다.

## 4. 현재 검증과 실행 절차

병렬 작업 C의 직접 수행 범위는 읽기 전용 실제 체크포인트 inventory와 시험 더블/단위 테스트다.
출력 완료 표시를 강화한 최종 신규 테스트 77개가 통과했다(2.04초).
torch/transformers/SQLAlchemy/psycopg import를 차단한 최종 격리 실행도 77개 통과했다(1.97초).
초기 76개 통과 및 격리 76개 통과 기록은 선행 검증으로 보존한다.
실제 모델 순방향 실행은 통합 담당이 소스를 최종 확인한 뒤 독립 프로세스에서 수행했다.
첫 `model/smoke_final/` 실행과 CLI 완료 표시 강화 후의 `model/smoke_final_v2/`가 각각 실제 4회 순방향 호출에 성공했다.
최종 v2 결과는 유한 logit, 두 문장 모두 반복 최대 차이 0.0, CPU 실행, 벤치마크 사용 0개를 기록했다.
결과 파일의 실제 지문과 완료 manifest 지문이 일치하며 최종 CLI/모듈 지문도 일치하는 것을 재확인했다.
총 8회는 동일한 중립 문장 2개를 재실행한 수이지 독립 문서 8개나 새 벤치마크 문서가 아니다.
최종 근거는 `model/smoke_final_v2/result.json`과 완료 manifest다. 이 성공은 해당 연결 실행 범위에 한정되며 고객 분류 정확도가 아니다.

출력 폴더는 새 경로여야 한다. 체크포인트 내부/동일 경로/경로 별칭, 기존 manifest를 조상으로 가진 동결 팩 내부는 거절한다.
입력 파일·소스 모듈·시작 시 CLI 지문은 출력 전후 다시 확인한다.
`result.json`을 먼저 기록하고 마지막 소스 재확인 성공 뒤에만 `manifest.json`을 완료 표시로 기록한다.
마지막 검증에 실패한 디렉터리는 남더라도 완료 manifest가 없으므로 성공 팩이 아니다. 기존 팩과 보고서를 덮어쓰지 않는다.

작업 폴더 `F:\antigravity\rag\poc`에서 통합 담당이 사용할 명령:

```powershell
.\.venv\Scripts\python.exe -B scripts/check_customer_model_connection_v1.py inventory `
  --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b `
  --out reports/CUSTOMER_GUIDE_PARALLEL02_20260915/model/inventory_final_v2

.\.venv\Scripts\python.exe -B scripts/check_customer_model_connection_v1.py smoke `
  --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b `
  --expected-checkpoint-sha256 0d0ac8d90bf7d2db2ea2ea0c5a61d16e6e73b5db049349e336a77ed7ba8beab2 `
  --out reports/CUSTOMER_GUIDE_PARALLEL02_20260915/model/smoke_final_v2
```

이미 존재하는 출력 경로에는 위 명령을 재실행하지 않는다. 재현은 별도의 새 경로를 사용한다.
읽기 전용 선행 inventory는 `model/inventory_agent_v1/`에 보존했다. 후속 CLI 보완이 있으면 이 팩은 당시 소스 지문에 묶인 역사적 검사 기록이다.
