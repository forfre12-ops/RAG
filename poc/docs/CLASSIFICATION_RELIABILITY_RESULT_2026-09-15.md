# 분류 품질 재발 방지 구현 결과 — 2026-09-15

결론: **정책 시험자료의 모델 학습·평가 유입과 빈 검사 성공을 막는 안전장치를 구현했다.**
사업 분류 목적·정책·정답은 아직 미승인이고, 업무용 모델 품질이 향상됐다고 주장하지 않는다.
계획/재개 조건: [재발 방지 계획](CLASSIFICATION_RELIABILITY_PLAN_2026-09-15.md).

## 완료한 작업

1. 기존140건 및 v1.1 40건을 `policy_fixture` 사용 제한 레지스트리에 등록했다.
   원본 위치·내용·답안·manifest는 변경하지 않았다. ID/계열/정책 및 공백 정규화 본문 해시로
   알려진 복사본도 차단한다. 자칭 승인/서명 또는 true 플래그로 금지를 해제할 수 없다.
2. 공통 검사를 주 학습기·요소 학습기·별도 A/B 학습기·주요 오프라인 평가와 체크포인트 로드에 연결했다.
   `validate_content_reference --predictions`는 정책 시험자료의 모델 채점을 거절하며 구조 검증은 유지한다.
3. 누설 검사기에 JSONL 입력/답안 결합과 v1.1 본문/맥락 관점을 지원했다.
   ID/본문/정책/맥락/입력 관점 해시, manifest의 선택 파일 지문을 확인한다.
   기본은 development만 읽고 검토 사례를 분모에서 별도 집계한다.
4. 빈 풀·지원하지 않는 형식·등급 후보0건은 종료2. `--strict` 지름길 경고는 종료3.
   긴 문장을120자로 잘라 매칭에 실패하던 오류와 동률 선택의 비결정성도 고쳤다.
5. 문자 n-gram/문서 계열 CV, 라벨 permutation을 추가했다. 어휘/IDF는 각 학습 fold에서만 만든다.
   판정 설명·ID·답안 필드를 특징으로 추가하지 않고 선택한 입력 관점만 사용한다.
   이는 지름길 탐지용 선형 분류기 학습이며 업무용 분류모델의 재학습이 아니다.
6. 미결정8개와 준비도 검사기를 추가했다. 현재 `HOLD_OWNER_DECISIONS`, 미결정8개다.
   값/담당자/근거를 적는 것만으로 승인을 인증하거나 실행을 허가하지 않는다.
7. 워크스페이스 MEMORY와 인계에 ‘내부 실험 방향’과 ‘사업 주 출력 승인’의 혼동을 정정했다.
   과거 기록은 보존했다. 홈 전역 메모리는 변경하지 않았다.
8. 모델/DB/보고서 원본이 없는 환경에서도 수행할 독립 회귀 테스트를 CI에 추가했다.
   CI 설정은 작성했으며 원격 CI 실행·통과는 확인하지 않았다.

## 현재 소스로 검증한 결과

- **354 tests passed**, 실패/오류/skip0. 신규 독립 안전장치63개가 포함되며 두 수치를 합산하지 않는다.
- 변경 Python 파일 ruff 통과, `git diff --check` 공백 오류 없음.
- 기존 학습2042/검증256/시험256행의 입력 로드 통과. 실제 모델 학습은 실행하지 않았다.
- 기존 metadata 풀1067건은 등급 후보1055건/미라벨 제외12건으로 집계했다.
  미라벨 업로드는 잘못된 등급을 만들어 채우지 않고, 제외 분모를 기록한다.
- 기존140건 구조 검사 및 v1.1 40건 저장본 재검사 통과.
- v1.1 본문 단독 관점은 전체40건/등급 후보0건/검토40건으로 종료2.
  가상 맥락을 포함하면40건(TS20/S1 20)의 정책 진단이며, 4등급 고객 정확도 평가가 아니다.
- 이전 스냅샷 범위의 원본JSONL406개·기존소스12개·모델/입력/소스48개 지문 유지.
  이 범위 밖의 학습/평가 진입점은 이번에 의도적으로 수정했다. 모든 소스 불변이라는 뜻은 아니다.

### 개발80건의 지름길 진단

개발 분할90건에서 검토10건을 제외했다. TS/S1/S2/S3 각20건, seed0~29, 5-fold.
봉인 후보는 이번 CV에 사용하지 않았다. 레지스트리 등록·구조 검증을 위한 해시 계산/파싱은 별개다.

| 진단 | 평균 | 범위 | 라벨 permutation 평균 |
|---|---:|---:|---:|
| 문자 n-gram 층화 CV | 98.71% | 97.5~100% | 24.29% |
| 문서 계열 분리 CV | 99.96% | 98.75~100% | 24.33% |

동일 문장 반복 검사(min_docs5)에서는 단서0개였다. 그 검사 하나만으로는 이 위험을 놓친다.
전체 진단은 `SHORTCUT_WARNING`, strict 종료3으로 처리됐다. 이 수치는 생산 모델 정확도가 아니며,
높은 문자 분류 성적만으로 누설의 원인이 증명되는 것도 아니다. 원고의 반복 판정 설명 등과 함께 해석한다.
이전 별도 의견의 CV와 알고리즘/설정이 같다고 가정하지 않고, 이번 설정과 지문을 결과에 기록했다.

## 재현

작업 디렉터리: `F:\antigravity\rag\poc`. 기존 결과를 덮어쓰지 않도록 새 출력 경로를 사용한다.

```powershell
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality -q
.\.venv\Scripts\python.exe -B scripts/audit_classification_readiness.py
.\.venv\Scripts\python.exe -B scripts/measure_grade_phrase_leak.py --pool reports/CONTENT_REFERENCE_20260914/reference_v1 --min-docs 5 --cv-seeds 30 --strict --top 0
.\.venv\Scripts\python.exe -B scripts/validate_content_reference.py --pack reports/CONTENT_REFERENCE_20260914/reference_v1 --out reports/CLASSIFICATION_RELIABILITY_RECHECK/reference_structure.json
```

확대 회귀:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/data_quality tests/test_content_reference.py tests/test_content_reference_revision.py tests/test_content_reference_review.py tests/test_classification_review_workflow.py tests/test_classification_pilot_intake.py tests/test_trainer_sample_weights.py tests/test_trainer_fnr_metrics.py tests/test_eval_p1_model_gold_source_prior.py tests/test_proxy_training_finalization.py -q
```

로컬 실행 결과는 `poc/reports/CLASSIFICATION_RELIABILITY_20260915/`에 있다.
주 결과는 `development_verified.json`, `tests_final.xml`, `reference_structure.json`,
`workspace_preservation.json`, 소스/결과 지문은 `evidence.json`이다.
선행 시험 결과는 덮어쓰지 않고 남겼다. 최종 수치는 위 파일을 따른다.
`reports/`는 Git ignore 대상이다. 이 문서는 버전 관리 가능한 요약이며 원격 백업을 대신하지 않는다.

## 미완료와 다음 순서

- [D01~D08](classification_decisions.pending.json)의 실제 정책 책임자 결정과 근거 수신.
- 결정된 정책에 맞는 실제 문서 모집단·사용 권한·독립 정답 및 블라인드 시험 조건 확정.
- 임의 과거 스크립트/수동 지표 계산의 모든 경로를 차단한 것은 아니다. 신규 경로는 공통 검사를 연결해야 한다.
  본문 변형·부분 청크에서 계보까지 제거하면 해시 차단만으로 모든 파생 자료를 탐지할 수 없다.
- 후보 정책/답안 승인, 업무용 모델 재학습, 운영 API·등급/검수 경로·활성 모델 교체는 하지 않았다.
- 이번 변경은 미커밋이며 푸시하지 않았다. 원격 CI와 복구/백업도 검증하지 않았다.

**다음은 추가 합성 자료 제작이 아니라 D01 분류 대상·주 출력, D02 적용 정책의 실제 결정이다.**
그 후 증거/요소·산정식·골든셋/분모를 고정하고, 승인된 동일 문서의 라벨 A/B로 넘어간다.
