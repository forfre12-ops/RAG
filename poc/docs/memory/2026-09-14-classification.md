# 2026-09-14 — 분류 품질 개선 작업 기록

목적: 이 대화에서 진행한 보안등급 분류 작업을 다음 작업자가 이어갈 수 있게 보존한다.
완료·제안·미확인을 분리한다. 대화의 모든 원문을 복사한 기록은 아니며, 보안문서 본문·비밀 설정은 담지 않는다.

## 1. 사용자 결정과 정정한 설명

- 초기 질문: 명확한 등급 기준과 신뢰할 정답지를 어떻게 만들 것인가.
- 정답안은 AI가 작성할 수 있다. 다만 AI의 확신이나 사람의 서명만으로 내용상 정당성이 보장되지는 않는다.
- 승인 기준을 검수자에게 제공하는 것은 순환 평가가 아니다. 모델 예측과 다른 검수자의 답은 숨긴다.
- 승인된 합성 사례는 reference 범위의 정답이 될 수 있다. 합성이라는 이유로 영구 배제하지 않는다.
- 사람 간 일치율이 모델 정확도의 상한인 것은 아니다. 수행사 소속이라는 이유만으로 검수를 무효라고 하지 않는다.
- S·V·M을 반드시 채워야 품질 개선을 시작할 수 있다는 설명은 지나치게 제한적이었다.
- 사용자 선택: **내용에 필요한 보호등급 추천을 우선 개발**. 실제 고객사 관리등급 연동은 뒤로 분리.
- 최신 실행 요청: 워크스페이스 기억 저장, 비-S/V/M 참조 기준, 후보 140건, 검증과 비교 학습 준비.

## 2. 1차 평가 정상화 — 완료

기준 HEAD `d51e07229cfc2041c3b7e05ba8e1648808b37916`. 미커밋 변경은 보존했다.

- S2→S3가 지표에 나타나지 않던 문제, human_review 문자열만으로 GOLD로 읽던 문제,
  S3 출처·학습 중복을 놓치던 문제를 실패 시험으로 재현한 뒤 수정.
- 4등급 혼동행렬·등급별 recall·정확한 등급 오류·S2 하향 오류를 추가.
- 문서 집합/본문/정답 결합, 전체 학습 명세, 정책·설정 지문이 불충분하면 후보 비교 HOLD.
- 후보 결과는 REGRESSION_OK/HOLD/REJECT. 자동 PROMOTE나 고객사 합격이 아님.
- 관련 시험 **196개 통과, 실패·건너뜀 0**. 전체 저장소·DB/API E2E 시험은 아님.
- 원본 JSONL 406개 바이트 보존 확인. 406은 파일 수이며 고유 문서 수가 아니다.
- 기존 교정용 20건, 64조합 승인 비교표 제작. 당시 신규 답안·사람 서명은 0건.

근거:

- `poc/docs/CLASSIFICATION_PILOT_RUNBOOK_2026-09-14.md`
- `poc/docs/CLASSIFICATION_POLICY_APPROVAL_DRAFT_2026-09-14.md`
- `poc/reports/CLASSIFICATION_PILOT_20260914/baseline_before.json`
- `poc/reports/CLASSIFICATION_PILOT_20260914/tests.xml`
- `poc/reports/CLASSIFICATION_PILOT_20260914/calibration20/`

초기 정책 문서의 목적·S/V/M 경계 미정 상태는 당시 기록이다. 새로운 내용 기반 참조 초안과
구분한다. 새 초안도 기존 고객사·계약 기준을 자동 대체하지 않는다.

## 3. S·V·M 기여 측정 — 완료

로컬 기준 모델 `poc/artifacts/classifier_p1_v5_clean/v-fe4b386b`를 지정했다.
운영 활성 모델은 조회하지 않았다. 온도 2.03, escalation τ 0.3, CUDA,
PyTorch 2.12.1+cu130 / Transformers 5.13.0. 모델·입력·코드 해시는 실행 manifest에 있다.

| 평가셋 | 행 | 기존 라벨 일치 | S/V/M 숫자 제외 | 모델 직접 분류 |
|---|---:|---:|---:|---:|
| hardened42 | 42 | 95.24% | 95.24% | 90.48% |
| holdout109 | 109 | 69.72% | 69.72% | 66.97% |
| golden100 | 100 | 64.00% | 64.00% | 63.00% |
| mundane150 | 150 | 14.00% | 14.00% | 14.00% |
| proxy development200 | 200 | 28.00% | 28.00% | 28.00% |

- 601행, 공백 정규화 고유 본문 559개. 서로 겹치는 평가셋이 있어 통합 정확도를 주장하지 않음.
- S/V/M 곱셈·숫자 요소 전달 제외 시 등급·점수·검수 상태 차이 0건.
- 전체 재실행의 문서별 레코드 20파일 바이트 동일.
- 42건×4조건은 모델을 각자 실행(168 forward)해 확인: 캐시 측정과 결과 차이 0건.
- 관련 시험 **84개 통과**. 앞선 196개와 겹치는 시험이 있으므로 합계 280개라고 하지 않음.
- 선택 입력·모델·실행 코드 48파일 해시 불변, 네트워크 연결 시도 0회.
- 숫자만 빼는 것과 규칙 보정·검수까지 빼는 것은 다름. golden100에서 합의 검수를 끄면
  19건이 검수에서 빠지며, 그중 기존 라벨 대비 오답 8건·하향 오답 3건.
- staging은 이번 실험에서 검수 미라우팅 상태다. DB 저장이나 사람 확정을 의미하지 않음.
- 기존 모델 학습 라벨에 S/V/M 판단이 반영됐을 가능성은 제거하지 못함.
- 실제 확인된 S/V/M 입력을 학습한 다른 모델의 효용, 정제 데이터 재학습 효과는 측정하지 않음.

근거와 재현:

- `poc/docs/SVM_ABLATION_RESULT_2026-09-14.md`
- `poc/scripts/measure_svm_ablation.py`
- `poc/reports/SVM_ABLATION_20260914/full02/report.json`
- `poc/reports/SVM_ABLATION_20260914/uncached42/report.json`
- `poc/reports/SVM_ABLATION_20260914/verification.json`
- `poc/reports/SVM_ABLATION_20260914/tests.xml`

```powershell
# F:\antigravity\rag\poc에서, 존재하지 않는 새 출력 경로 사용
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:HF_HUB_OFFLINE='1'
./.venv/Scripts/python.exe -B -u scripts/measure_svm_ablation.py `
  --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b `
  --out reports/SVM_ABLATION_새실행
```

## 4. 데이터 한계 — 확인

- 측정한 601행 모두 정책 버전 필드가 없었음. 출처 등급은 고객사 정답 진위 인증이 아님.
- holdout109 중 67행이 `gold_real/train_subset.jsonl`과 중복.
  `labeled_p1_v5_clean` train/val/test와는 각각 0행. 현재 모델이 67행을 학습했다고 단정하지 않음.
- hardened42와 holdout109의 동일 본문 42개 중 4개는 정답 버전이 다름.

| 문서 ID | holdout109 | hardened42 |
|---|---|---|
| a1beb524ceafe108 | S2 | S1 |
| 823545b7edf3a0ef | S2 | S1 |
| 9a4ace0da18602c1 | S1 | TS |
| 6ea073680b55d1e9 | S2 | S1 |

hardened에는 재심판·상향 기록이 있다. 차이가 있다는 이유만으로 한쪽을 오류로 확정하지 않는다.
서로 다른 정책·라벨 버전을 섞는 평가를 금지하고 별도 쟁점 기록으로 비교한다.

## 5. 후속 묶음 — 실행 시작

내부 기준 초안, 120개 등급 후보와 20개 검토 후보, 개발90/봉인후보50 분할,
문서 계열·근거·중복 검사, 비교 학습 계약을 준비한다. 완료 결과는 아래에 추가한다.

아직 하지 않은 것: 정책 승인, 사람 검수·서명, 고객사 GOLD 확정, 정제 데이터 재학습,
운영 API/등급/검수 변경, 모델 배포. 사용자 홈의 전역 메모리는 수정하지 않는다.

## 6. 후속 묶음 완료 — 2026-09-15 자정 이후 추가

앞의 “실행 시작” 상태를 보존하고 완료 내용을 추가한다. 작업 기준일은 9월14일이며
실제 후보 생성은 9월15일 00:11 KST, 보존 검사는 00:13 KST에 끝났다.

### 제작한 파일과 기준

- `poc/docs/CONTENT_PROTECTION_REFERENCE_V1.md`: 포함·제외·인접 등급 경계·필수 근거·충돌 처리까지 완성한 미승인 내부 기준.
- 정책ID `content-protection-reference-v1-draft`.
- TS는 완전한 핵심 재현 묶음/유효한 핵심 통제 재료/1,000명 이상 식별·민감정보 결합,
  S1은 구체 개인·거래·기술 세부내용, S2는 현재 내부 운영 세부사항, S3는 일반·빈 서식·안전한 배포 조건.
- 1,000명은 내부 제안의 명시적 경계이지 법정 기준이 아니다. 기존 고객사 정책을 대체하지 않는다.
- 필요한 맥락이 없으면 needs_evidence, 동일 버전·범위 근거가 충돌하면 needs_policy_review.
- 숫자 S/V/M은 사용하지 않는다. 미확인을 false/0으로 바꾸거나 아무 규칙도 없다고 S3를 기본값으로 주지 않는다.

후보 경로: `poc/reports/CONTENT_REFERENCE_20260914/reference_v1/`.

| 분할 | TS | S1 | S2 | S3 | 검토 | 전체 | 계열 |
|---|---:|---:|---:|---:|---:|---:|---:|
| development | 20 | 20 | 20 | 20 | 10 | 90 | 25 |
| sealed_candidate | 10 | 10 | 10 | 10 | 10 | 50 | 15 |

검토20건은 정보 부족10건·맥락 충돌10건. 두 분할에 각각5+5로 나뉜다.
30개 업무 계열의 4등급 변형120건과 10개 검토 계열의 짝20건이다.
정책ID/정책SHA, 문서ID/본문SHA, 규칙, 본문 근거 span, 조건, 상·하위 배제 이유를 답안 sidecar에 채웠다.
사람 서명·승인·GOLD 자격은 만들지 않았다. 답안과 분리된 입력집에는 등급이나 답안 조건을 붙이지 않았다.
모델 입력은 text만 쓰도록 계약화했다. 생성 원고의 variant_index도 입력에서 제외했다.

기존 라벨4건은 `legacy_label_issues.json`과 `LEGACY_LABEL_ISSUES.md`에 양쪽 라벨·정책 버전 누락·
재심판 이력·해시·남은 쟁점으로 기록했다. 재심판 라벨과 예전 근거 문구의 불일치도 경고했다.
어느 쪽을 신규 정답이라고 확정하거나 기존 원문·라벨을 바꾸지는 않았다.

### 검증 결과와 한계

- ID140개·정규화 고유 본문140개·계열40개·본문 근거 span140개 및 답안 바인딩 검사 통과.
- 같은 명시 조건에서 상충하는 답안 없음. 개발/봉인후보 본문·계열 중복 각각0.
- 알려진 기존 풀4개(train2042/val256/test256/old train610, 총3164행)와 본문 중복0.
  전부 계열ID가 없어 **기존 풀의 의미상 계열 중복 검증 미완료**. 전체 모델 학습 계보도 미확인.
- 새 테스트42개 포함 관련252개 통과(실패0/건너뜀0). 기존196개+SVM하니스14개+신규42개를 합쳐 재실행.
  이전84개도 중복된 시험이 있으므로 별도로 더해 누적 수처럼 표시하지 않는다.
- ruff 정적 검사 통과, git diff --check 오류 없음. 기존 파일의 CRLF 변환 경고만 있음.
- 원본JSONL406개 추가·수정·삭제0, 초기 기존소스12개 해시 불변.
- 이전 ablation의 모델·입력·소스48개 해시 불변. 운영 활성 모델은 조회하지 않았다.
- 기존 교정20건은 채운 등급0·서명0, 기존64조합 표는 승인0 상태로 유지.

**검증의 의미를 제한한다:** 이번 140건은 짧은 가상 상황 카드다. 실제 1,000행 원장이나 제품 전체
소스를 작성한 것이 아니다. 인원·완전성·유효성은 가상 세계의 조건이다. 답안·조건을 동일 AI가
작성했으므로 기계적 일관성 검사는 독립 전문가 검수도 고객사 정확도 측정도 아니다.
sealed_candidate라는 분할 이름만으로 실제 접근 통제·블라인드 봉인이 완료되지는 않는다.
모델 추론 점수는 이번 묶음에서 **미측정**이다. 테스트용 예측 fixture의 점수는 모델 성능이 아니다.

### 평가와 다음 비교 학습

기존 `measure_four_metrics.compute`를 재사용하는 오프라인 측정 어댑터를 추가했다.
등급120건의 오답과 전체140건의 검토 라우팅 TP/FP/FN/TN를 분리한다.
S1→S2, S2→S1, S2→S3, TS/S1→S3, S3 과분류, 검수/미라우팅 비율을 각각 낸다.
검수로 보내도 등급 오답이 없어지지 않으며 null 미추천도 전체 등급 정확도에서는 오답이다.
기존 행렬에는 유효 등급 예측만 넣고 분모와 미추천 수를 별도로 표시한다.
문서 누락·중복·본문/정책/답안 바인딩 오류는 계산 전에 차단한다.

`comparison_training_contract.json`은 PREPARED_NOT_EXECUTED / training_enabled=false.
기존 같은 문서·같은 초기모델·같은 학습/평가 조건에서 라벨만 바꾸는 A/B 계약을 준비했다.
미확정 문서는 양쪽 동일 제외, 신규 문서 추가는 별도 실험. 학습기 입력은 기존 text+label 유지,
상세 근거는 문서ID·본문해시로 연결한 sidecar. 이번140건·기존20건은 학습 내보내기를 하지 않았다.

### 결과·재현·지문

- 요약: `poc/docs/CONTENT_PROTECTION_REFERENCE_RESULT_2026-09-14.md`
- 후보·답안·출처: `poc/reports/CONTENT_REFERENCE_20260914/reference_v1/README.md`
- 파일 및 생성 소스 지문: 같은 폴더 `manifest.json`
- 재검증: `poc/reports/CONTENT_REFERENCE_20260914/recheck.json`
- 보존 검사: 같은 상위 폴더 `preservation.json`
- 테스트252개: 같은 상위 폴더 `tests.xml`
- 초기 스냅샷: 같은 상위 폴더 `baseline_before.json`
- 정책SHA256: `4263c96b624f26ea14f1eed919516a01e38f896dcc5990fc6e1dbcc3c120f66f`
- 후보manifest SHA256: `3a01e018cd63c746171fc26436d48cc02067ff78c52ae9645165a84488d7a7a0`
- 이전 로컬 model.safetensors SHA256 재확인: `ca3a4b59679ea4c828ba9768701e0e0a5589ffe6a2f027fd086fef95119e5e65`
- 새 모델을 로드하거나 학습한 것은 아니다. Git HEAD는 앞의 d51e072... 그대로이며 커밋하지 않았다.

```powershell
# F:\antigravity\rag\poc; 기존 경로 대신 새 출력 경로 사용
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:HF_HUB_OFFLINE='1'
./.venv/Scripts/python.exe -B scripts/prepare_content_reference.py --out reports/CONTENT_REFERENCE_재실행/reference_v1
./.venv/Scripts/python.exe -B scripts/validate_content_reference.py --pack reports/CONTENT_REFERENCE_재실행/reference_v1 --out reports/CONTENT_REFERENCE_재실행/recheck.json
./.venv/Scripts/python.exe -B scripts/verify_content_reference_workspace.py --before reports/CONTENT_REFERENCE_20260914/baseline_before.json --out reports/CONTENT_REFERENCE_재실행/preservation.json
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_content_reference.py
```

본문·답안·분할은 고정seed로 재생성되며 manifest의 생성 시각만 달라진다.
reports 산출물은 Git ignore 대상이다. 실제 워크스페이스 파일은 저장했으며 외부 백업·커밋은 수행하지 않았다.

### 다음 작업 — 아직 미실행

1. 기준 책임자의 TS 완전 재현/통제/1,000명 경계, S1/S2 범위·예외 검토.
2. 입력집과 기준을 이용한 독립 검토, 불일치의 근거·책임자 기록과 조정.
3. 승인된 기준/답안 버전 확정 및 평가셋 실제 봉인. 미해결은 검토 상태 유지.
4. 기존 문서의 확정된 정제 라벨과 원래 라벨만 바꾸는 비교 학습(실제 실행은 별도).

계속 미실행: 정책 승인, 사람 서명·GOLD 확정, 정제 재학습, 운영 분류·검수·API 변경, 모델 배포.
사용자 홈의 전역 메모리는 변경하지 않았다. 이번 첫 실행 묶음의 산출물 제작은 완료했다.

## 7. 인계 기록 추가 — 2026-09-15

사용자가 지금까지의 메모 저장과 다음 작업 안내를 요청했다.
현재 후보140건의 파일/정책/답안 결합을 재검증했고, 기존 테스트252개 통과 기록을 다시 확인했다.
이번 인계 턴에는 전체 테스트·모델 추론·학습을 재실행하지 않았다.

[2026-09-15 인계](2026-09-15-handoff.md)에 결정·완료·미검증·다음 작업의 산출물과 완료 조건을 저장했다.
다음 시작점은 개발90건과 기존 충돌4건의 **내용 검수표 및 기준 쟁점표**다.
이는 후속 계획이며 아직 해당 검수표를 작성하거나 신규 라벨을 확정한 것은 아니다.
이전 기록과 후보 파일은 보존하고, 프로젝트 메모만 추가·갱신했다.
