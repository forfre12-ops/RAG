# 내부 고정 정답 파일럿 — 2026-09-15

## 결론

**등급별10건, 총40건을 명시한 내부 정책 아래 고정하고 두 계산 경로로 검산했다.**
외부 검수자가 올 때까지 기다리지 않고, 사실·정책·근거로 정답을 재현하는 방법을 구현했다.
하지만 범위는 가상 연결표/지급표/배정표의 제한된 문법이다. 일반 보안문서 정답지나 고객 GOLD 1,000건의 완성이 아니다.
정책의 정당성을 계산으로 증명한 것도 아니다. 숫자만 바꾼 원장 1,000개를 만들어 목표 달성으로 세지 않는다.

사용자의 최신 요청은 '검수자 없이도 우리 기준의 객관적 정답을 먼저 고정하자'이다.
따라서 앞선 '다음은 실제 검수자 배정/추출 연결부' 안내보다 **이 제한된 내부 참조 작업**을 우선했다.
주 사업 정책 변경·고객 승인을 대신하는 것으로 해석하지 않았다.

## 이번에 만든 것

- 정책: [내부 고정 참조 정책 v0.1](INTERNAL_FIXED_REFERENCE_POLICY_V0_1.md).
- 전체 본문 파서/조건식/근거 증명서: `poc/src/koipa/internal_reference.py`.
- 분리된 CSV+메모리 SQLite JOIN 검산기: `poc/src/koipa/internal_reference_oracle.py`.
- 새 묶음 제작/저장본 재검증: `poc/scripts/build_internal_reference.py`.
- 대조시험/문체 진단: `poc/scripts/audit_internal_reference.py`.
- 신규93개 시험: `poc/tests/data_quality/test_internal_reference.py`.
- 최종 자료: `poc/reports/INTERNAL_FIXED_REFERENCE_20260915/pilot_v0_1_final/`.

최종 팩 안에는 원문40개, 모델 관점 입력 JSONL, 별도 답안/근거 JSONL, 정책 JSON, 검증표와 파일 지문이 있다.
제작 목표 라벨은 답안 메타에만 있으며 검산기에 입력하지 않는다. 목표와 계산 답이 다르면 실패한다.
증명서의 `fixed_under_internal_policy`는 이 정책의 조건부 참조 정답 자격이며 고객 GOLD가 아니다.
가상 인원1,000명 TS 경계는 내부 실험용 설계값이다. 가이드 공식/법정 기준으로 표현하지 않는다.

개발28건(7계열)/예비후보12건(3계열). 예비후보도 제작/검증 시 읽었으므로 블라인드라고 하지 않는다.
서식 계열10개 각각 네 등급을 모두 포함하고, 같은 계열에서 본문 길이·행 수가 같다.
본문은30종, 본문+맥락은40종이다. 같은 본문에 공개 허가만 다른 S2/S3 대조쌍10개는 같은 분할에 있다.
이40건 전체를 서로 독립인 일반 업무 문서40종으로 세지 않는다.

## 현재 측정 결과

| 확인 항목 | 결과 | 의미 |
|---|---:|---|
| 네 등급 참조 답 | TS/S1/S2/S3 각10 | 이 내부 정책 안에서 고정 |
| Python/SQL 계산 일치 | 40/40 | 동일 정책, 다른 파서/계산 경로 |
| 정책 경계 열거 | 15조합 | 0/1/999/1,000/1,001 × 공개 false/true/unknown |
| 맥락을 제거한 입력 | 40/40 HOLD | 숨겨진 맥락을 정답으로 채점하지 않음 |
| TS/S1 공개 맥락 반전 | 20/20 등급 유지 | 상위 충분조건 유지 |
| S2/S3 공개 맥락 반전 | 20/20 상호 전환 | 필요한 맥락에 반응 |
| N=0 공개 여부 unknown | 20/20 HOLD | unknown을 false로 만들지 않음 |
| 계열/본문 분할 간 중복 | 0 | 이 팩의 명세 범위 |
| 기존 fixture 등록180건과 중복 | 0 | 공백 제거 본문 해시, 전체 학습풀 검사가 아님 |
| 확대 회귀 테스트 | 1,086 통과 | 실패/오류/건너뜀0 |
| 독립 경량 테스트 | 728 통과 | 위 수치의 부분집합, 합산 금지 |

독립 시험은 sqlalchemy/psycopg/transformers/torch import를 차단했다. SQL 검산은 외부 DB가 아닌 메모리 SQLite다.
오류 주입은 근거/정책/본문/등급 변조, 잘못된 맥락 타입, 누락/추가 내용, 중복키, 사람ID 중복 집계,
두 검산기 불일치, 0건 입력, 새 출력 덮어쓰기, 경로 이탈, 허가 플래그 및 숫자0/1과 bool 혼동을 포함한다.
저장본 JSON 목록/튜플 불일치 및 Python bool/숫자 동등 비교 문제를 테스트 중 수정했다.
초기1084개 결과와 `pilot_v0_1/`, `diagnostics.json`은 중간 이력이다. **최종은 `*-final`과 `pilot_v0_1_final/`**.
중간 팩은 소스 지문 변경으로 현재 verifier에서 거절되는 것이 정상이며 삭제/덮어쓰기하지 않았다.

문체 진단은 제목/표 순서/열/행 수만 사용했다. 문자2~5gram, LinearSVC, 5-fold, seed0/1/2:

| 방식 | 평균 | 라벨 섞기 평균 |
|---|---:|---:|
| 계열 분리 CV | 25.0% | 17.5% |
| 행 단위 층화 CV | 0.0% | 34.17% |

행 단위0%는 같은 서식의 다른 라벨이 학습 폴드에 남는 인위적 대조쌍 구조와 관련된다.
이를 우수한 분류 성능이나 누설 없음의 통계적 증명으로 해석하지 않는다.
정확하게 확인한 것은 열거한 서식10종 모두 네 라벨이 같은 수로 포함된다는 점이다.
전체 본문/맥락의 모든 지름길 검사는 아직 하지 않았다. 이 선형 진단기 학습 외 업무 모델 학습은 없다.

## 정답지와 보조기구의 현재 한계

1. 정답을 기계적으로 고정하는 통로는 갖췄지만, 지원하는 문서 의미는 좁다. 자유로운 업무 문서에는 적용하지 않는다.
2. 본문 길이는121,762~122,032자다. 원장 전체를 연결해야 N을 알 수 있어 본문 앞부분만 보는 분류기는 이 답을 재현할 수 없다.
   현행 코드는 학습 truncation 및 추론 청크/오버플로 처리가 있지만, 전체 표 JOIN 집계와 맥락 지원은 이번에 검증하지 않았다.
   모델을 로딩하지 않았으므로 정확한 토큰 수·운영 입력 호환성·추론 품질 측정은 없다.
3. 전체 기존 학습풀 중복/근접중복 검사는 아직이다. 등록180건과 비교0건을 전체 풀 중복0으로 쓰지 않는다.
4. 기존 경로/행 플래그의 학습·평가 차단은 확인했다. 새40건을 기존 deny registry에 추가하지는 않았다.
   메타데이터를 제거해 다른 위치로 복사한 새 본문까지 전부 차단한다고 주장하지 않는다. 기존 등록180건은 불변이다.
5. 정책 설계의 업무 적합성·위험 임계는 계산의 참/거짓과 다른 문제다. 내부 참조 버전 선택과 고객사 정책 승인은 구분한다.
6. 보조기구의 테스트 통과는 운영 연동/보안/권한/분류 품질의 완성을 의미하지 않는다.

`dataset_role=policy_fixture`, training/model_evaluation=false를 유지했다.
우리는 내부 참조 답은 고정할 수 있지만, 이 실험 결과만으로 일반 문서에 '누가 봐도 무조건 TS'를 부여할 수는 없다.

## 다음 작업 — 250건씩 확대하기 위한 순서

1. **문서군 확대 명세**: 이 원장군 밖의 핵심정보 완결/부분 제공/일반업무/공개 자료 조건을
   별도 정책 버전으로 만들고, 필요한 근거를 검산 가능한 형태로 정의한다. 한 문서군·숫자 변형으로 할당량을 채우지 않는다.
2. **입력 적합성**: 짧은 본문 사례와 전체 표 집계 사례를 구분한다. 현재 모델이 받는 입력만으로
   답이 결정되는 사례를 선별하고, 맥락 미지원/잘림/문서간 연결이 필요하면 별도 정책 엔진 시험으로 남긴다.
3. **전체 지름길/중복 검사**: 개발 자료로 전체 내용 진단, 표제/메타 제거, 어휘·수치·서식 반전과
   문서군 분리 실험을 한다. 전체 학습풀의 정확/근접중복을 측정하고 새로운 본문 지문 사용제한을 확장한다.
4. **총1,000건 채택**: 네 등급 각각250건을 목표로 하되, 유일 답·완전한 제시 근거·상하위 배제·독립 검산·입력 적합성
   조건을 만족하는 것만 채택한다. 정보부족/경계/HOLD 자료는 별도이며, 수량 부족을 모호한 라벨로 채우지 않는다.
5. **학습·평가 분리**: 정책 검산셋/본문 사실추출셋/분류 평가셋을 구분하고, 최종 미열람 평가 계열을 새로 확보한다.
   같은 제작자가 본 예비후보를 사후에 블라인드라 이름만 바꾸지 않는다.

내부 실험의 추가 참조 답 고정에 외부 사람 서명을 필수로 두지 않는다. 다만 고객사 등급/GOLD/PMR-002/운영 전환은 별도다.
이번40건이 기존140건이나 v1.1의 모든 문제를 고친 것도 아니다. 그 자료는 원래 용도 제한을 유지한다.

## 보존과 재현

이번 작업 시작 시점의 핵심 소스/정책/원장31개와 원본JSONL406개, 기존 참조팩13/91개 파일 지문은 유지했다.
9월14일 기준 보존검사는 과거에 의도적으로 수정한 `policy_engine.py` 때문에 계속 `CHANGED_REQUIRES_REVIEW`다.
이를 이번 변경으로 오인하거나 옛 baseline을 갱신해 숨기지 않았다. 고객 결정8개는 여전히 미정이다.
운영 API/분류 경로/모델/원본/서명/DB는 변경하지 않았다. 커밋/푸시/전역 사용자 메모리 수정도 없다.
`reports/`는 계속 Git ignore다. 소스 지문/재현성을 저장한 것이 외부 백업 완료를 뜻하지 않는다.

`poc`에서 실행한다. `--build`/진단 `--out`은 새 경로가 필요하다.

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B scripts/build_internal_reference.py --verify reports/INTERNAL_FIXED_REFERENCE_20260915/pilot_v0_1_final
.\.venv\Scripts\python.exe -B scripts/build_internal_reference.py --build reports/INTERNAL_FIXED_REFERENCE_20260915/replay_new
.\.venv\Scripts\python.exe -B scripts/audit_internal_reference.py --pack reports/INTERNAL_FIXED_REFERENCE_20260915/replay_new --out reports/INTERNAL_FIXED_REFERENCE_20260915/diagnostics_replay_new.json --seeds 3
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_internal_reference.py -q
```

확대 테스트 대상: `tests/data_quality`, `test_content_reference*`, `test_classification_review_workflow.py`,
`test_classification_pilot_intake.py`, `test_trainer_sample_weights.py`, `test_trainer_fnr_metrics.py`,
`test_eval_p1_model_gold_source_prior.py`, `test_proxy_training_finalization.py`, `test_proxy_model_comparison.py`,
`test_serving_eval.py`, `test_policy_engine.py`, `test_policy_engine_defects.py`, `test_policy_rule_checker.py`, `test_org_mapping.py`.
개별 명령과 확정 범위는 최종 JUnit XML에서 확인한다.

최종 pack manifest SHA256: `988ffb329153aba97323d4e812efac6bf0e7a482342ae7345cbcd196bc6c57b2`.
실행/검증/보존 지문: `poc/reports/INTERNAL_FIXED_REFERENCE_20260915/evidence-final.json`.
정책/소스 변경 시 기존 팩을 덮어쓰지 않고 새 버전과 새 결과 경로를 만든다.
