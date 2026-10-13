# 실제 문서 검수 v2 입력·제출 검증 결과 — 2026-09-15

**정책 주입형 입력·제출 검증기와 로컬 CLI를 구현했다.**
검수 도구의 준비 단계이며 실제 고객 문서 검수, 정답 확정, 모델 정확도 개선의 완료가 아니다.

## 완료

- [구현 계약](REAL_DOCUMENT_REVIEW_V2_CONTRACT_DRAFT.md): 필드·시간·해시·입력 view·허가 주장·제출 상태·오류/불일치 경계.
- `src/koipa/review_v2.py`: manifest/context/case/packet/policy 결합, 제시 근거 제한, 빈 제출 양식, 제출 구조·근거 검사.
- `scripts/check_document_review.py`: strict JSON, 입력 변경/출력 겹침 검사, 새 파일 저장, 정책 fixture 데모/스키마.
- `tests/data_quality/test_review_v2.py`: 130개 신규 회귀.

핵심 재발 방지:

1. 다른 고객사/정책 판본·입력 view·자료로 과거 답안을 재사용하면 거절한다.
2. 본문 전용 입력에 숨긴 관리정보를 같은 packet으로 섞지 못하게 한다.
3. 없는 증거를 요구해 보류 답변을 억지로 채우지 않는다. missing_evidence/policy_gap은 근거0건을 허용한다.
4. 규칙ID·인용 근거·등급 구조 오류와 사람의 정책 해석 차이를 분리한다.
   의미상 불일치는 답안 수정 없이 조정 검토 사유로 남긴다.
5. 구조 검증 통과·허가 참조·human_declared를 정답/인증/승격으로 해석하지 않는다.
6. 빈 양식에는 AI 답·기계 정책 후보·개인 서명이 없다. 기본 결과에도 근거 원문/검수 사유 원문을 복사하지 않는다.

같은 규칙ID를 가진 두 가상 정책의 S1/S2 의미 차이를 주입해 검사했다.
가상 demo에는 실제 문서0·실제 사람 제출0이며, 테스트의 가상 제출은 실제 답안이 아니다.

## 검증

- 전용 회귀: **130 passed**.
- 확대 회귀: **892 passed**, 실패/오류/skip0. 기존762개+신규130개.
- SQLAlchemy/psycopg/torch/transformers import를 차단한 독립 데이터 품질 회귀: **534 passed**.
  위892개에 포함되며 더하지 않는다.
- 새 Python3개 Ruff 통과. Git diff 공백 검사 통과(기존 파일의 CRLF 정규화 경고 별도).
- CLI demo/schema 실행·저장, 빈 양식→제출 검사, 실패/덮어쓰기 거절, 변경 중 입력 검사 시험 통과.
- 이번 시작점의 기존 소스/설계/정책 원장21개 지문 유지.
- 원본 JSONL406개 불변, 추가0. 기존 참조팩 manifest 대상13/91개 파일 지문 유지.
- 준비도 검사: D01~D08 미정8개, `HOLD_OWNER_DECISIONS`/종료2 유지.

과거 9/14 보존 기준의 `policy_engine.py` 변경은 앞선 기술 보완에서 생긴 차이다.
그 검사는 계속 `CHANGED_REQUIRES_REVIEW`/종료1이며 이번 변경으로 숨기거나 기준선을 덮지 않았다.
이번 시작점 대비 해당 파일은 불변이다.

전체 저장소/원격 CI/실제 고객시스템/신원·조직격리/운영 부하 시험을 완료했다는 주장이 아니다.

## 결과·재현

결과 폴더: `poc/reports/CLASSIFICATION_REVIEW_V2_20260915/` (Git ignore 대상).

- tests.xml, isolated-tests.xml: 두 회귀 결과.
- demo.json, review.schema.json: 가상 정책 입력 검사/계약 구조.
- workspace_preservation.json: 기존 원본/역사적 기준 대조.
- evidence.json: 이번 보존 지문, 실행 결과, 새 파일/산출물 지문, 미실행 경계.

`F:\antigravity\rag\poc`에서:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B scripts/check_document_review.py --demo
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_review_v2.py -q
```

확대 회귀 대상은 `tests/data_quality`와 다음 파일이다.

```powershell
$env:TESTING='1'
$env:DATABASE_URL='postgresql+psycopg://test:test@127.0.0.1:9/test'
.\.venv\Scripts\python.exe -B -m pytest tests/data_quality tests/test_content_reference.py tests/test_content_reference_revision.py tests/test_content_reference_review.py tests/test_classification_review_workflow.py tests/test_classification_pilot_intake.py tests/test_trainer_sample_weights.py tests/test_trainer_fnr_metrics.py tests/test_eval_p1_model_gold_source_prior.py tests/test_proxy_training_finalization.py tests/test_proxy_model_comparison.py tests/test_serving_eval.py tests/test_policy_engine.py tests/test_policy_engine_defects.py tests/test_policy_rule_checker.py tests/test_org_mapping.py -q --tb=short
```

## 미완료와 다음 순서

다음 바로 가능한 기술 묶음은 **R3: 두 검수 제출의 비교·불일치 분류·조정 제안 결합 검증**이다.
같은 문서/정책/view만 비교하고 양쪽 제출 지문이 바뀌면 조정을 다시 받아야 한다.
일치해도 정답 확정/GOLD 승격을 하지 않는다. 기존 합성40건용 비교기를 그대로 재명명하지 않는다.

별도로 추출 판본 receipt의 실제 연결부, 공급18항목/조직 격리, 허가·정책 승인 진위, 독립 검수자,
실제 정답 모집단·분모 확정이 필요하다. R4 실제 파일럿과 R5 인증/확정 연결은 미실행이다.
운영 API/DB/분류·확정 경로/모델/원라벨/결정 원장은 변경하지 않았다.
원본 관리등급과 보호 필요도의 의미를 자동 확정하거나 S/V/M 요소를 제거하지 않았다.

워크스페이스 MEMORY/인계에 추가 기록했다. 이번 변경은 미커밋이며 푸시·전역 사용자 메모리 변경 없음.
