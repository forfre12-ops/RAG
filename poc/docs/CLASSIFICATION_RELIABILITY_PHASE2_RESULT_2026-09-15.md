# 분류 품질 재발 방지 2차 보완 결과 — 2026-09-15

결론: **다른 필드명을 통한 사용 제한 누락, 직접 평가 호출의 검사 누락, 불완전한 strict 검사의
성공 처리를 보완했다.** 확대 회귀409개 통과. 업무용 모델의 분류 품질 향상을 측정한 것은 아니다.

사업 방향은 [D01/D02 검토안](CLASSIFICATION_D01_D02_PROPOSAL_2026-09-15.md)으로 제안했다.
주 결과는 ‘현재 확인된 증거를 선택된 정책에 적용한 보안등급 후보’를 권고한다.
고객사의 기존 저장등급 재현 및 내용 보호필요도와 구분하며, 아직 어떤 안도 승인하지 않았다.
원 가이드 guide는 정책 검토 출발점 제안일 뿐, 현행 운영 v22를 변경하지 않았다.

## 이번에 고친 재발 경로

| 발견 사항 | 보완 | 검증 범위 |
|---|---|---|
| 일부 기존 데이터가 `document_family_id` 등 다른 필드명 사용 | 공통 검사에 계열/원문 ID 별칭과 앞뒤 공백 처리 추가 | 이름만 달리 쓴 알려진 정책 시험자료 차단 |
| `training_use_permitted`/`evaluation_use_permitted` 사용 금지가 누락될 수 있음 | 목적별 허가 별칭 검사; false 또는 비불리언이면 거절 | 다른 true 필드로 금지를 덮을 수 없음 |
| 학습용/평가용 허가를 동시에 검사해 정상 평가전용 입력도 거절 | A/B·요소 학습기의 로더에 실제 목적 전달 | 학습과 평가의 허가를 각각 적용 |
| 상위 로더를 거치지 않는 평가 호출 | 모델 비교·윈도우 로짓·서빙 평가 함수 시작점에서 검사 | 모델/파이프라인 실행 전 금지 입력 거절 |
| 평가 로더가 계보 필드를 먼저 버림 | 두 서빙 평가 스크립트가 원래 행 검사 후 필드 추출 | 계열 ID에 의한 거절이 보존됨 |
| `--strict`에서 CV 옵션을 생략하면 성공할 수 있음 | 경고와 미실행 검사를 분리하고 둘 다 strict 실패 | 문자/계열 CV 미실행이면 종료3 |
| 파일 파싱 이후 달라진 파일의 해시를 결과로 남길 가능성 | 실제 읽은 바이트의 해시로 파싱 결과 결합; 로딩/측정 후 재대조 | 측정 중 선택 입력 변경 시 실패 |

금지 자료의 ID·계열·정책·본문 지문 레지스트리는 유지했다. 원본140건/v1.1 40건의 답안이나
위치를 바꾸지 않았다. 등록 금지가 없다는 것만으로 다른 입력의 학습 허가·GOLD 자격을 부여하지 않는다.

## 확인한 결과

- 새 회귀25개 중 수정 전24개가 실패했고 패키지 설정 확인1개가 통과했다.
  이는 재현 테스트 조건 수이지 운영 장애24건을 발견했다는 뜻이 아니다.
- 최종 확대 회귀 **409 passed / failed0 / errors0 / skipped0**, 15.74초.
  기존354개보다 검사 범위도 넓혔으므로 단순 차이를 신규 테스트 수로 해석하지 않는다.
- 독립 `tests/data_quality`는 **87개**다. DB·업무 모델 패키지
  `sqlalchemy/psycopg/transformers/torch`의 import를 차단하고 pytest 자동 플러그인 로딩을 끈
  로컬 환경에서도87개 통과했다. 서빙 평가 검사는 별도 전체 환경의 테스트에 둔다.
- Python 변경 범위 ruff 통과. 레지스트리의 package-data 선언을 검사했으며,
  실제 wheel 빌드·설치 및 원격 CI 실행을 검증한 것은 아니다.
- 기존 학습2042/검증256/시험256행 로드, 기존 metadata의 등급 후보1055건 로드 통과.
  업무 모델 학습·추론은 실행하지 않았다.
- 준비도 검사: **`HOLD_OWNER_DECISIONS`, 미결정8개, 종료2**. 정책 결정 파일 해시 유지.
- 보존 검사: 이전 스냅샷이 지정한 원본JSONL406개, 기존소스12개, 이전 모델/입력/소스48개 불변.
  이번에 수정한 검사/학습·평가 진입점까지 모든 소스가 불변이라는 뜻은 아니다.

### 실제 누설 검사 실행

같은 개발90건 중 검토10건을 제외한 등급80건(TS/S1/S2/S3 각20건)을 검사했다.
봉인후보 분할을 CV에 추가하지 않았다. 문장 단서 검사만으로는 반복 단서0개였다.

| 실행 | 결과 | 종료코드 |
|---|---|---:|
| `--strict`, CV 생략 | `INCOMPLETE_CHECKS`, 문자/계열 검사 미실행 | 3 |
| `--strict --cv-seeds 3` | `SHORTCUT_WARNING`, 선택 파일 측정 후 지문 일치 | 3 |

3seed 문자CV 평균98.75% / permutation18.75%, 계열CV 평균100% / permutation21.25%.
이는 작은 선형 분류기를 학습한 **지름길 진단**이며 업무용 분류 모델 정확도가 아니다.
선행30seed 결과와 설정이 다르므로 점수의 상승/하락을 개선 효과로 비교하지 않는다.
실제 누설 원인의 단독 증명이나 독립 정답 인증도 아니다. 같은 입력의 위험 경고가 유지됨을 확인했다.

## 재현 및 증거

작업 디렉터리: `F:\antigravity\rag\poc`. PowerShell에서 실행한다.

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TESTING='1'
$env:DATABASE_URL='postgresql+psycopg://test:test@127.0.0.1:9/test'
.\.venv\Scripts\python.exe -B -m pytest tests/data_quality tests/test_content_reference.py tests/test_content_reference_revision.py tests/test_content_reference_review.py tests/test_classification_review_workflow.py tests/test_classification_pilot_intake.py tests/test_trainer_sample_weights.py tests/test_trainer_fnr_metrics.py tests/test_eval_p1_model_gold_source_prior.py tests/test_proxy_training_finalization.py tests/test_proxy_model_comparison.py tests/test_serving_eval.py -q --tb=short
.\.venv\Scripts\python.exe -B scripts/measure_grade_phrase_leak.py --pool reports/CONTENT_REFERENCE_20260914/reference_v1 --min-docs 5 --strict --top 0
.\.venv\Scripts\python.exe -B scripts/measure_grade_phrase_leak.py --pool reports/CONTENT_REFERENCE_20260914/reference_v1 --min-docs 5 --cv-seeds 3 --strict --top 0
.\.venv\Scripts\python.exe -B scripts/audit_classification_readiness.py
```

뒤의 세 명령은 각각 종료3/3/2가 예상 결과다. 성공0으로 바꾸기 위해 제한을 해제하지 않는다.
새 결과는 `poc/reports/CLASSIFICATION_RELIABILITY_PHASE2_20260915/`에 저장했다.
`tests_final.xml`, `strict_incomplete.json`, `strict_complete_warning.json`,
`workspace_preservation.json`, 현재 소스·결과 지문 `evidence.json`을 참조한다.
`reports/`는 Git ignore 대상이며 외부 백업이 아니다.
선행1차 `evidence.json`은 당시 소스의 역사 기록으로 보존했다. 2차 소스와 같다고 주장하지 않는다.

## 한계와 다음 실제 작업

1. 정책 판단의 주체·목적·판본은 여전히 미정이다. 먼저 D01/D02 검토안에 실제 담당자 결정을 받는다.
   그 후 D03/D04의 판단요소·네 조합 차이, D05 보류 규칙, D06/D07 모집단·분모를 확정한다.
2. 이후 사용이 승인된 실제 문서로 독립 정답과 문서 계열/분할을 고정한다.
   D08의 범위 결정 없이 합성 자료 추가나 업무용 모델 라벨 A/B를 자동 재개하지 않는다.
3. 모든 과거 스크립트·직접 지표 계산을 차단한 것은 아니다. 본문을 재작성하거나 부분 청크로
   바꾸면서 계보를 제거하면 단순 지문 차단이 놓칠 수 있다. 새 경로에는 공통 검사가 필요하다.
4. 입력 지문 재대조는 선택 파일의 전후 일치 검사다. 파일 잠금·중간 변경 후 원상복귀 탐지·
   디렉터리 전체의 트랜잭션 스냅샷·승인 진위 인증을 제공하지 않는다.
5. 운영 API·등급 산정식·검수 판정·활성 모델은 변경하지 않았다. 평가용 함수의 입력 검사는 변경했다.
   정책/정답 승인·사람 서명·실제 문서 접수·고객사 정확도 검증은 수행하지 않았다.
6. 이번 턴 커밋/푸시, 원격 CI/복구 검증, 사용자 홈 전역 메모리 수정은 하지 않았다.

다음 ‘진행’은 미정인 사업 방향을 대신 승인한다는 뜻이 아니다. 담당자 결정이 들어오면
그 결정과 근거를 검증·기록하고 후속 데이터 작업 범위를 구체화한다.
