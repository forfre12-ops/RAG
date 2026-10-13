# 정책 검산기 후속 보완 — 2026-09-15

결론: **잘못된 검산 결과를 정책의 문제/정상으로 오인하는 경로를 보완했다.**
새19개 포함 확대464개 테스트 통과. 사업 주 출력·정책·정답 승인은 하지 않았다.
이번은 기존 정책 엔진과 오프라인 검사기의 기계적 일관성 보완이지 새 고객사 정책 구현이 아니다.

## 확인한 현재 연결 상태

- 현재 `poc/src`의 운영 분류 서비스·추론 경로에서 `policy_engine` 호출을 찾지 못했다.
  확인된 사용은 정책 검증/가상 입력 측정 CLI와 테스트다. 외부 호출자 부재까지 증명하지는 않는다.
- `classify_service.py`, `pipeline.py`, `rule_engine.py`는 이번 변경 전후 지문이 같다.
  API 응답·운영 S/V/M 공식과 모드·실제 검수 경로·활성 모델은 바꾸지 않았다.
- 모델/룰 표시용 요소를 실제 S/V/M 근거로 바꾸는 배선은 추가하지 않았다.
  출처·근거 위치·unknown을 다루는 사실 계약 및 공급 정보의 진위 검증은 여전히 후속 설계 대상이다.

## 고친 문제

1. **확정 불충족과 미확인의 혼동**: AND 조건 중 하나가 명백히 틀렸는데도 `requires_evidence`가
   없으면 규칙을 다시 보류시키고 있었다. 불충족을 별도 상태로 전달해 해당 규칙을 제외한다.
   아직 적용 가능성이 남은 규칙의 증거 부족은 계속 보류한다. 실제 근거가 없는데 false를 생성하지 않는다.
2. **한 예시로 도달 불가 단정**: 처음 예시는 상위 규칙에 가려져도 다른 값에서는 선택될 수 있다.
   규칙별 예시와 정책 상수·대표값 조합을 함께 탐색하고, 선택되는 실제 계산 예시를 남긴다.
3. **동순위만으로 충돌 확정**: 서로 다른 등급의 동순위 쌍을 잠재 충돌로 분리한다.
   두 규칙을 동시에 충족하는 입력을 찾은 경우만 `OBSERVED_OVERLAP`으로 보고하고 예시를 남긴다.
4. **부정 조건 예시 오류**: `not_in`의 첫 금지값만 피하는 대신 전체 금지 목록을 피한다.
   수치 비교 경계와 실제 조건을 위반하는 변형도 검사한다.
5. **문제를 발견해도 성공 종료**: 발견 사항 또는 미실행 검사가 있으면 종료3,
   입력/출력 오류는 종료2. 기존 보고서 덮어쓰기를 거절한다.
6. **검사 없음과 정상의 혼동**: 기본4096개의 대표 조합 한도를 넘으면 미완료로 표시한다.
   모든 대표 조합을 검사해도 `SAMPLED_NO_FINDINGS`일 뿐 논리적 완전성 증명이나 승인으로 부르지 않는다.
7. CLI가 호출자의 표준출력을 새 래퍼로 바꾸어 닫히게 하던 부작용을 제거했다.
   수정 전 회귀 실행에서 pytest 캡처 종료 오류가 함께 재현됐으며 수정 후 직접 CLI 호출 검사가 통과했다.

보고서의 기존 `unreachable` 키는 호환상 남겼지만 의미를 **시험 입력에서 선택되지 않음**으로 한정했다.
각 항목에 `NOT_SELECTED_IN_PROBES`, `proof=false`를 넣었다. 기존 소비자는 이 새 의미와 종료코드를 확인해야 한다.
잠재 충돌을 검사했으나 예시를 찾지 못했다는 사실 역시 모든 입력에서 충돌이 없다는 증명이 아니다.

## 실측 결과

| 검사 | 결과 | 해석 |
|---|---|---|
| 확대 회귀 | 464 passed / 실패0 / 오류0 / skip0 | 전체 저장소 전수 검사는 아님 |
| 독립 검사 | 106 passed | 앞선87개 + 이번19개; 확대464개에 포함 |
| 선택적 실행 의존성 차단 | 동일106개 통과 | DB/torch/transformers 없이 로컬 실행; 원격 CI는 미검증 |
| 제공 정책 양식 | 규칙4개, 대표 입력864개, 모두 선택 예시 발견 | 미승인 양식의 동작 검산 |
| 검사 한도1 실행 | `probe_limit_reached`, 종료3 | 미실행을 정상으로 숨기지 않음 |
| 정책 결정 검사 | 미결정8개 / HOLD / 종료2 | 승인 상태 변경 없음 |

864개 가상 입력을 HEAD `83c88e9f`의 정책 엔진과 수정본에 동일하게 넣었다.

- 후보 등급 변경0, 선택 규칙 변경0.
- 검토 표시340 → 264: **76건 제거, 추가0건**.
- 보류 규칙 목록이 달라진 입력88건. 이 중 검토 표시까지 바뀐 것이76건이다.

이는 가상 정책·대표값 조합에서 불필요한 검토 표시가 제거된 결과다.
**실제 고객 문서의 검수 감소율·미탐 감소·정확도 개선 수치가 아니다.**
원래 엔진은 현재 Git HEAD에서 읽어 메모리에서 실행했으며, 운영 프로세스나 모델은 실행하지 않았다.
원래 엔진의 Git 바이트 해시와 변경 전 워킹트리 파일 해시는 줄바꿈 차이로 다를 수 있으므로 각각 기록했다.

## 원본 보존과 의도한 변경

- 원본JSONL406개 불변, 추가0. 교정20건의 답안/서명0, 정책64조합 승인0 유지.
- 이전 보존 검사 결과는 이번에는 **`CHANGED_REQUIRES_REVIEW`, 종료1**이다.
  기존소스12개 중11개, 이전 모델/입력/소스48개 중47개가 같다.
  두 목록 모두 동일한 `policy_engine.py` 한 파일의 이번 의도한 변경을 감지했다.
  검사기를 완화하거나 과거 기준선을 덮어서 보존 성공으로 만들지 않았다.
- 기존 참조140건/v1.1 40건은 레지스트리의 manifest와 결합된 파일 지문으로 별도 확인한다.
- 새 결과는 `poc/reports/CLASSIFICATION_POLICY_AUDIT_20260915/`의
  `tests.xml`, `template_audit.json`, `limited_audit.json`, `mechanics_comparison.json`,
  `workspace_preservation.json`, `evidence.json`이다. 기존1/2차 결과는 보존한다.
- 이번 변경은 미커밋이다. 푸시·원격 CI/백업·사용자 홈 전역 메모리 변경은 하지 않았다.

## 재현

작업 디렉터리 `F:\antigravity\rag\poc`. 결과를 저장하려면 `--json`에 새 경로를 준다.

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TESTING='1'
$env:DATABASE_URL='postgresql+psycopg://test:test@127.0.0.1:9/test'
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality -q
.\.venv\Scripts\python.exe -B scripts/check_org_policy_rules.py datasets/mapping_tables/POLICY_TEMPLATE.json
.\.venv\Scripts\python.exe -B scripts/check_org_policy_rules.py datasets/mapping_tables/POLICY_TEMPLATE.json --probe-limit 1
.\.venv\Scripts\python.exe -B -m pytest tests/data_quality tests/test_content_reference.py tests/test_content_reference_revision.py tests/test_content_reference_review.py tests/test_classification_review_workflow.py tests/test_classification_pilot_intake.py tests/test_trainer_sample_weights.py tests/test_trainer_fnr_metrics.py tests/test_eval_p1_model_gold_source_prior.py tests/test_proxy_training_finalization.py tests/test_proxy_model_comparison.py tests/test_serving_eval.py tests/test_policy_engine.py tests/test_policy_engine_defects.py tests/test_policy_rule_checker.py tests/test_org_mapping.py -q --tb=short
```

## 아직 해결하지 않은 것

검사기는 대표값 기반이며 SMT/형식 증명기가 아니다. 사실의 출처·진위·최신성·고객사 범위를 인증하지 않는다.
기본 한도는 기본 입력 조합 수이며 규칙별 조건 변형/증거 제거 검사는 별도로 추가된다.
입력 해시 전후 대조는 파일 잠금이나 중간 변경 후 원복 감지가 아니다.
증거 부족 시 낮은 후보를 보류와 함께 노출하는 기존 정책 엔진 출력 계약은 유지했다.
이를 `grade=null` 등의 새 주 출력으로 바꾸는 것은 D01/D05 의미 결정과 호환성 설계가 필요하다.

다음 제품 설계 선택은 [D01/D02 검토안](CLASSIFICATION_D01_D02_PROPOSAL_2026-09-15.md)을 따른다.
주 출력의 개발 방향을 사용자에게 요청했으며, 응답이 없다고 권고안을 승인 처리하지 않는다.
방향이 선택돼도 고객사 정책 승인·실제 정답 모집단 확정·재학습·운영 반영은 별도다.
