# 분류 기준·평가 정상화 1차 실행 기록

범위: 기준선 스냅샷, 평가기 결함 수정, 정책 승인 초안, 기존 자료 20건의 교정용 검수 팩.
운영 분류 경로·모델·가중치·기존 정답·운영 서명 절차는 변경하지 않는다. 신규 사람 서명은 만들지 않는다.

## 산출물

- 정책 승인 안건: `CLASSIFICATION_POLICY_APPROVAL_DRAFT_2026-09-14.md` (미승인).
- 변경 전 스냅샷: `../reports/CLASSIFICATION_PILOT_20260914/baseline_before.json`.
- 64조합 승인용 비교표: `../reports/CLASSIFICATION_PILOT_20260914/policy_decision_table_draft.jsonl`.
  모든 승인값은 null이며 구현 비교값을 시험 정답으로 쓰면 안 된다.
- 검수자 전달 폴더: `../reports/CLASSIFICATION_PILOT_20260914/calibration20/reviewer/`.
  `CASES.md`는 원문·증거 누락을 사람이 읽기 위한 사례집이며 답안은 비어 있다.
- 선정 원장: 같은 배치의 `coordinator/` (기존 라벨 포함, 검수자 전달 금지).

설정의 classifier_model_dir/factor_model_dir가 비어 있어 구성 모델 파일 지문은 확보되지 않았다.
서버 DB에서 활성 모델을 조회하지 않았으므로 운영 모델 고정/확인을 완료했다고 주장하지 않는다.
스냅샷은 406개 로컬 JSONL 파일과 분류 관련 코드의 실제 바이트 해시, 허용 목록의 설정을 기록한다.
`.env` 원문·비밀번호·API 키는 기록하지 않는다. 고유 문서 수와 파일 내 총 행 수는 다르다.

## 평가 v2 계약

기존 `model_recall`/`serving_recall` 필드는 호환을 위해 유지하며 **하향 오류율**임을 명시한다.
정확한 등급별 recall은 `per_grade`에서 읽는다. 추가 지표는 4등급 혼동행렬·정확도·등급별
오류·S2→S3·고등급 탐지율·검수 미라우팅 오류율이다. 검수로 보내도 등급 오답은 사라지지 않는다.
`status=staging`이나 legacy auto_confirm 지표는 실제 저장·사람 확정을 증명하지 않는다.

후보 판정은 `REGRESSION_OK / HOLD / REJECT`를 출력한다. PROMOTE는 더 이상 출력하지 않는다.
REGRESSION_OK는 비교 범위의 회귀가 관측되지 않았다는 뜻이며 고객사 합격·배포 허가가 아니다.
고객사 성능 합격은 기존 `eval_authority.assess`의 목표·대표성·권위·신뢰구간 검사로 별도 판단한다.
이전 스냅샷은 v2 필수 정보가 없어 HOLD가 정상이다. 필드를 추정해 채워 승격하지 않는다.

후보 판정 입력은 다음을 함께 확보해야 한다.

1. 원 평가셋: 고유 doc_id, 본문, 정답, 출처, family_id.
2. 측정 레코드: 같은 doc_id·정답·정확한 본문 SHA-256, 원시 모델 등급, 최종 등급, 라우팅 상태.
3. 전체 학습 명세: `schema_version=training-inputs-v1`, `model_id`, `complete=true`,
   `files=[{path,sha256}]`. 최종 학습뿐 아니라 튜닝에 사용한 자료도 포함한다.
4. 학습 파일의 본문·family_id. 본문 중복과 알려진 문서 계열 중복을 모두 검사한다.
5. 측정 당시 조건: `org_id`, `policy_version`, `model_id`, `measurement_config_sha256`,
   `records_sha256`, `eval_sha256`. 설정 지문은 비민감한 전체 판정 설정의 승인된 명세를 해시한다.

학습 명세의 complete 선언과 문서 계열 태그 자체의 진위는 데이터 관리자가 확인해야 한다.
의미상 유사문서 탐지 전체를 해결한 것은 아니다. 잘못된 JSON 행·누락 문서·중복·정답 변경·본문
불일치는 조용히 건너뛰지 않는다. 평가면/필수 지표가 빠지거나 정책·설정·평가셋이 다르면 HOLD다.

```powershell
# poc에서 실행. 새 출력 경로를 사용한다.
./.venv/Scripts/python.exe scripts/judge_model_candidate.py `
  --records reference=reports/새측정.records.jsonl `
  --eval reference=datasets/승인평가셋.jsonl `
  --context reference=reports/새측정.context.json `
  --training-manifest reports/후보.training-inputs.json `
  --snapshot reports/새기준선.json
```

위 파일명은 예시다. 존재하지 않는 입력이나 승인 정보를 만들어 명령을 통과시키지 않는다.

## 사람 정답 자격

`human_review` 문자열만으로 GOLD가 되지 않는다. 기존 `is_valid_signoff`를 재사용하고,
정책 버전·승인 증거·규칙 ID·판정 증거·문서 해시·인접 등급 배제 이유·적용 범위를 검사한다.
레거시 기록은 삭제하지 않고 추가 자료가 없는 경우 UNKNOWN으로 보고한다.
이 검사는 기록 형식·결합 검사다. 계정 실체·승인 원문 진위·내용상 타당성을 인증하는 기능은 아니다.
승인 정책·독립 검수·증거가 완비된 합성 참조 사례도 reference 범위의 GOLD가 될 수 있다.
합성이라는 이유로 정답 제작을 영구 차단하지 않는다. 다만 origin/scope를 함께 보고하며
synthetic/reference를 customer 성능 정답으로 취급하지 않는다. 기존 데이터의
held_review/locked_gold_eval 학습 격리는 바꾸지 않는다.

## 20건 검수 팩

```powershell
./.venv/Scripts/python.exe scripts/prepare_classification_pilot.py `
  --out reports/CLASSIFICATION_PILOT_20260914/calibration20
```

기존 합성 서명 표본에서 옛 라벨별 5건을 선택하고 섞는다. 원본 파일과 기존 서명은 변경하지 않는다.
답변의 grade·signer·policy_version은 비워 두며 사실 확인 자료가 없는 항목은 누락으로 표시한다.
교정용이므로 학습 중복 제외나 대표 표집을 했다고 주장하지 않는다. 학습·평가 승격은 금지한다.
검수자에게 reviewer 폴더만 제공하고 정책 초안의 미승인 상태를 설명한다. 현재 팩만으로 실제
관리성을 확정할 수 없으며 필요한 추가 증거를 적는 것이 정상적인 결과다.

## 다음 단계와 외부 의존성

1. 정책 책임자가 승인 요청서 A01~A08을 확정한다.
2. 권한 있는 검수자 2인과 불일치 조정자를 정하고 20건으로 기준·입력 공백을 교정한다.
3. 승인 후 140건 제작·문서 계열 분할·봉인을 진행한다. 현재 140건이나 GOLD를 만들었다고 세지 않는다.
4. 실제 관리 정보 계약과 기존 정책 엔진의 병행 경로를 연결한다. 이번 묶음에는 운영 연결이 없다.
5. 활성 모델·전체 학습 계보·측정 설정을 확인하고 새 기준선을 측정한다.
6. 고객사 내부 실문서 평가와 계약상 합격 기준은 별도 승인한다. 재학습·배포는 그 이후 의사결정이다.

## 이번 실행 검증 결과

- 먼저 S2→S3 누락, human_review 문자열 GOLD 오인, S3 학습 중복 누락을 실패 테스트 3개로 재현했다.
- 수정 후 관련 시험 196개 통과, 실패 0, 건너뜀 0. 전체 저장소 시험·DB/API E2E·모델 재추론 시험은 아니다.
  실행 기록: `../reports/CLASSIFICATION_PILOT_20260914/tests.xml`.
- 변경 파일 Ruff 검사 통과. `git diff --check` 오류 없음(기존 CRLF 정규화 경고 별도).
- 변경 전 스냅샷의 406개 원본 JSONL 파일을 바이트 SHA-256으로 대조: 변경 0개.
- rule_engine/policy_engine/inference pipeline/classify_service/golden_signoff 원본 해시도 동일하다.
- 20건 생성 확인: 옛 라벨별 5건. 새 등급 답변 0건, 새 서명 0건, 실제 관리 증거 누락 20건.
- 64조합 정책 초안: 승인된 답 0건. 코드 비교값을 참고로 남겼으며 승인 기대값은 null이다.
- 기존 측정 레코드 재집계: `../reports/CLASSIFICATION_PILOT_20260914/baseline_recomputed_v2.json`.
  판정 HOLD: 정답 권위·전체 학습 계보·측정 당시 정책/설정 정보가 불충분하다.
  mundane150은 평가 문서 ID 결합도 미완비다. 당시 모델·원본 입력까지 재현한 실험이 아니므로
  이 재집계 결과를 모델 성능 향상/하락으로 해석하지 않는다.
- reports/ 산출물은 저장소 ignore 대상이다. 로컬 파일은 생성했지만 커밋·배포·외부 전달은 하지 않았다.
