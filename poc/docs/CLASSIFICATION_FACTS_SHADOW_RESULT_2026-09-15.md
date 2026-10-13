# 근거 계약·정책 비교 계산 구현 결과 — 2026-09-15

결론: **근거에 연결된 사실 주장, 미확인, 모델 추정을 분리하는 입력 계약과 비교 계산부를 구현했다.**
고객사 정책 승인·실제 근거의 진위 인증·운영 전환·모델 학습은 하지 않았다.
상세 계약: [입력·정책 비교 결과 v1 초안](POLICY_FACTS_SHADOW_CONTRACT_V1_DRAFT.md).

## 완료 범위

- 새 `policy_facts.py`: 출처/판본/정책 결합, 본문 위치·JSON Pointer·해시, 시간대/명시된 만료일 검사.
- 새 `policy_shadow.py`: 정책 불충족/미확인 구분, 상위 가능성·우선순위 예외·동순위 충돌 처리.
  근거 부족/충돌 시 주 grade는 null. 낮은 규칙 후보는 참고 항목으로만 남긴다.
- 새 `check_policy_shadow.py`: 세 입력 파일 검증, 별도 결과 저장, JSON Schema, 가상 데모5건.
- 새 회귀84개: 모델/룰 추정의 사실 승격 차단, 다른 조직·문서·판본 재사용, 변조·기간·부재/미수신,
  충돌 순서 독립성, 기존 출력 무변경, CLI/출력 보존/중복 JSON/오류 메시지 검사.

근거 자료가 맞게 연결됐다는 것은 자료가 참이라는 뜻이 아니다.
모든 결과에 인증·의미상 진위·정책 승인·자동화 허용·최종확정 false를 명시한다.
S/V/M 관련 근거를 묶어 보여주지만 숫자 단계 판정이나 FUN-023 전체 충족을 주장하지 않는다.

## 검증 결과

- 확대 회귀 **548 passed / 실패0 / 오류0 / skip0**. 최종 시간은 tests_final.xml 참조.
- 독립 검사190개 포함. DB/torch/transformers import를 차단한 로컬 실행에서도190개 통과.
  원격 CI·전체 저장소 테스트·실제 모델 추론 시험은 아니다.
- 변경 Python 범위 ruff 및 `git diff --check` 통과.
- 정책 미설정 결합 함수는 기존 결과 값과 동일하고 새 필드를 추가하지 않는다.
- 정책 설정 시에도 기존 label/status/score/needs_review를 수정하지 않음을 테스트로 확인했다.
- 기존 `policy_engine.py`, `rule_engine.py`, `classify_service.py`, `pipeline.py`,
  미결정8개 파일, 사용 제한 레지스트리의 이번 변경 전후 지문을 대조한다.
  정책 엔진에는 앞선 턴의 수정이 이미 존재하며 이번에 추가로 변경하지 않았다.

### 실행한 가상 데모

| 사례 | 주 grade | 상태 |
|---|---|---|
| 사실 전부 미수신 | null | needs_evidence |
| 비공개 주장만 있고 관리 근거 미수신 | null | needs_evidence; S2는 참고 후보로만 표시 |
| 가상 비공개·제한 접근 주장과 결합 근거 | TS | candidate |
| 가상 공개 주장과 결합 근거 | S3 | candidate |
| 가상 공개/비공개 주장 충돌 | null | needs_evidence_conflict_review |

이 표의 정책과 자료는 모두 코드에 명시된 가상 조건이다. 실제 사람의 검토 기록은 없다.
고객사 문서나 신규 GOLD/학습 사례가 아니며, 정확도 분모로 쓰지 않는다.

## 파일과 재현

결과: `poc/reports/CLASSIFICATION_FACTS_SHADOW_20260915/`

- `tests_final.xml`: 최종 확대 회귀
- `demo_final.json`: 최종 가상 계산 결과5건, 원문 내용 미포함
- `fact_packet.schema.json`: 생성된 구조 계약
- `evidence.json`: 소스/결과 지문과 보존 대조

`reports/`는 Git ignore 대상이다. 기존 결과를 덮지 않고 새 경로를 사용했다.

`F:\antigravity\rag\poc`에서:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TESTING='1'
$env:DATABASE_URL='postgresql+psycopg://test:test@127.0.0.1:9/test'
.\.venv\Scripts\python.exe -B scripts/check_policy_shadow.py --demo
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality -q
.\.venv\Scripts\python.exe -B -m pytest tests/data_quality tests/test_content_reference.py tests/test_content_reference_revision.py tests/test_content_reference_review.py tests/test_classification_review_workflow.py tests/test_classification_pilot_intake.py tests/test_trainer_sample_weights.py tests/test_trainer_fnr_metrics.py tests/test_eval_p1_model_gold_source_prior.py tests/test_proxy_training_finalization.py tests/test_proxy_model_comparison.py tests/test_serving_eval.py tests/test_policy_engine.py tests/test_policy_engine_defects.py tests/test_policy_rule_checker.py tests/test_org_mapping.py -q --tb=short
```

## 경계와 다음 작업

사용자의 ‘다음 진행’은 이 설계의 개발 진행으로 해석했다. 사업/고객사 승인 원장은 임의로 채우지 않았다.
운영 API 연결·실제 ACL 연동·정책 승인·독립 정답 작성·업무 모델 학습·고객 정확도 측정은 미실행이다.
기존 분류 경로의 안전성이 새 비교 계층만으로 개선됐다고 주장하지 않는다.
커밋·푸시·원격 백업/CI·사용자 홈 전역 메모리 변경은 하지 않았다.

다음 기술 단계는 **문서/관리시스템 입력에서 이 계약으로 옮기는 읽기 전용 수집 어댑터**다.
숫자 S/V/M을 역산해 넣지 않고, 실제 공급되는 필드만 근거와 함께 연결한다.
실제 연동 권한·고객사 정책이 없다면 먼저 고정된 인터페이스와 가상 시스템 스냅샷으로 시험한다.
고객사별 사실→요소→등급 기준과 승인 권한은 여전히 별도로 받아야 한다.
