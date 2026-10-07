# 독립 검수·실제 문서 접수 준비 완료

2026-09-15. 요청: 다음 작업 진행.
기준 개정안과 합성 문서형40건 다음 단계인 **검수 불일치 수집/조정 도구와 실제 문서 파일럿 접수 계약**을 구현했다.
실제 사람 검수·정책 승인·원문 접수·학습을 완료했다는 뜻은 아니다.

## 완료한 것

- [검수·불일치·실제 문서 파일럿 절차](CLASSIFICATION_REVIEW_AND_PILOT_PROTOCOL_V1.md)
- 검수자 역할 R1/R2 각각 답안 없는 입력40건과 빈 제출 양식40건.
- 제출 ID/입력·정책 해시, 근거 위치, 추천/보류·규칙 조합, 계정·시점·사전 답 열람 선언 검사.
- 두 제출의 결론/규칙·사유/근거 차이와 TS/S1 대 S3 불일치 대조.
- 양쪽 제출 레코드 해시에 묶는 조정 제안 양식과 검증. 조정자 분리·과거 제안 재사용 검사.
- 실제 문서의 사용 허가·판본·추출 범위·계열·정책·맥락·학습/평가 격리를 점검하는 메타데이터 사전 검사.
- 실제 접수0건·제외 목록 미제공 상태를 그대로 담은 빈 양식, 실행 리포트, 원본 보존 검사.
- 신규83개 포함 관련 **432개 테스트 통과**, 실패0/오류0/건너뜀0. 신규 코드·테스트 ruff 통과.

## 현재 상태 — 준비와 실행을 구별

| 항목 | 현재 상태 |
|---|---|
| 준비한 합성 검수 대상 | 40건, R1/R2 동일 입력·정책 |
| 실제 사람 제출 | R1 0건 / R2 0건 |
| 검수 대조 | 40건 awaiting_reviews |
| 결론 일치율 | null — 제출이 없어 계산하지 않음 |
| 실제 조정 제안/확정 등급 | 0건 / 0건 |
| 실제 문서 접수/선정 | 0건 / 0건 |
| 계정·독립성·허가 진위 인증 | 미수행 |
| 정책 승인/GOLD/학습/운영 반영 | 미수행 |

단위 시험의 가상 계정·제출·조정값은 pytest 임시 폴더에서만 사용했다.
프로젝트 검수 결과 파일에 사람이 제출한 것처럼 넣지 않았다.
파일 속 계정 UUID와 human_declared는 형식/자기신고이지 사람 인증이 아니다.
두 사람이 같은 답을 쓰거나 조정 제안을 써도 자동 승인/GOLD/학습 허가로 처리하지 않는다.

## 실제 사용할 파일

패킷: [검수·접수 준비 색인](../reports/CLASSIFICATION_REVIEW_WORKFLOW_20260915/packet_v1/README.md)

- `reviewers/R1/`, `reviewers/R2/`: 각 담당자에게 전달할 입력·정책·빈 양식·안내.
  AI 답/예측/부모ID/계열ID/정답 분포를 넣지 않았다. 실제 폴더 접근제어는 설정하지 않았다.
- `coordinator/roles.template.json`: 기준 책임자·원문 소유자·R1/R2·조정자 배정 양식. 현재 모두 미배정.
- `coordinator/initial_queue.jsonl`, `readiness.json`: 아직 제출이 없다는 준비 상태.
- `pilot/intake.template.json`: 원문 내용 없는 접수 명세 틀.
- `pilot/intake.json`: 빈 배열. 이것이 현재 실제 접수 집합이다.
- `pilot/exclusions.template.json`: 제외 인덱스 미제공 상태. 빈 목록을 ‘중복 없음’으로 해석하지 않음.
- `blank_check/`: 빈 양식을 넣어 대조/조정 경로를 실행한 결과. 사람 검수 결과가 아님.
- `intake_check.json`: 실제 접수0건의 사전 검사 결과.

접수 검사기는 지정된 원문 경로/허가 문서를 읽거나 URL에 접속하지 않는다.
입력 명세가 갖춰져도 candidate_pending_source_verification이며 selected=false다.
실제 자료 선정을 위해서는 소유자의 원문/허가/추출/계열/중복 확인이 필요하다.

구현:

- `poc/scripts/classification_review_workflow.py`
- `poc/scripts/classification_pilot_intake.py`
- `poc/tests/test_classification_review_workflow.py`
- `poc/tests/test_classification_pilot_intake.py`

## 사용 순서와 재현 명령

먼저 R1/R2와 조정자, 기준 책임자를 정한다. R1/R2는 각 입력집만 보고 답안을 작성한다.
패킷 안의 템플릿은 동결 파일이다. **패킷 밖 새 파일로 복사한 뒤 작성**한다.
조정 제안 역시 대조 실행이 만든 새 resolution_template.jsonl을 바탕으로 별도 제출한다.

아래 명령은 `poc` 기준이다. 출력이 이미 있으면 중단하며 덮지 않는다.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
./.venv/Scripts/python.exe -B scripts/classification_review_workflow.py prepare --out reports/CLASSIFICATION_REVIEW_WORKFLOW_새실행/packet_v1

./.venv/Scripts/python.exe -B scripts/classification_review_workflow.py compare --pack reports/CLASSIFICATION_REVIEW_WORKFLOW_20260915/packet_v1 --left reports/제출/R1.jsonl --right reports/제출/R2.jsonl --out reports/CLASSIFICATION_REVIEW_WORKFLOW_새실행/comparison

./.venv/Scripts/python.exe -B scripts/classification_review_workflow.py compare --pack reports/CLASSIFICATION_REVIEW_WORKFLOW_20260915/packet_v1 --left reports/제출/R1.jsonl --right reports/제출/R2.jsonl --resolutions reports/제출/resolutions.jsonl --out reports/CLASSIFICATION_REVIEW_WORKFLOW_새실행/resolution_check

./.venv/Scripts/python.exe -B scripts/classification_pilot_intake.py --intake reports/제출/intake.json --exclusions reports/제출/exclusions.json --out reports/CLASSIFICATION_REVIEW_WORKFLOW_새실행/intake_check.json

./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_classification_review_workflow.py tests/test_classification_pilot_intake.py
```

`reports/제출/`은 실제 제출 파일을 저장할 예시 경로이며, 이번에 실제 사람 제출 파일을 만들지는 않았다.
제출의 일부만 완료해도 대조할 수 있지만 배정된40개 ID는 모두 유지하고 미완료 행은 pending으로 둔다.
정책/입력/제출이 바뀌면 새 버전으로 생성하고 기존 조정 제안 해시를 재사용하지 않는다.
현재 AI 후보 답과의 자동 비교, 사용자 인증 연동, 승인 DB 반영, 실제 표집·학습 export는 구현하지 않았다.

## 보존·검증 근거

- 새 준비 패킷 저장 후 다시 읽어 파일·소스 해시, 두 검수 입력집, 빈 양식 결합 검사 통과.
- 이전 합성40건 pack 및 v1.1 정책·소스 지문 재검증 통과. 이전140건/검토 자료도 기존 검증 경로로 유지 확인.
- 원본JSONL406개, 기존소스12개, 이전 모델/입력/소스48개 해시 보존. 원본 추가0.
- 교정20건 답/서명0, 비교표64건 승인0 유지. 봉인후보50은 의미상 재검토하지 않았다.
- `reports/CLASSIFICATION_REVIEW_WORKFLOW_20260915/tests.xml`: 432 tests, 0 failures/errors/skips.
  기록 시각: 2026-09-15 01:36:54 KST. 이전349/274/252와 합산하지 않는다.
- `workspace_preservation.json`: PRESERVED. 활성 서버 모델 조회/추론/재학습 없음.

패킷 manifest SHA-256:
`f61776e0ed10a52ae29adaa5e0e6b555a4951e6d9c2bf67b96644ba371e9f8be`

빈 양식 대조 manifest SHA-256:
`4de0f7b9ba079e982f3a86bfffcaeba23f1d74dc0e47409522f370a6a4c80c36`

절차 문서 SHA-256:
`7c7b2f447d098a0268a7f034971b5bf2f800549884485b83a70778eabe0dd5d8`

이 지문은 동일 파일 확인용이며 전자서명·신원 인증이 아니다.
reports는 기존 Git 제외 대상이고 소스/기록 변경도 아직 미커밋이다. 이번에는 커밋/푸시하지 않았다.
기존 무관한 데이터·스크립트·임시/압축파일은 수정하지 않았다.

## 바로 다음에 필요한 것

1. 실제 R1/R2·조정자·기준 책임자 배정.
2. v1.1 경계 쟁점의 책임자 결정과, 패킷 입력을 먼저 본 독립 검수 제출.
3. 사용이 승인된 실제 원문 보관 범위와 페이지/첨부·추출·계열·제외 인덱스 명세.
4. 제출 수신 후 불일치/부족 증거를 대조하고 조정 제안을 기록한다.
5. 실제 문서 참조를 소유자가 확인한 뒤 모집단·seed·출처/길이/표 포함 층을 고정하고 표집한다.
6. 정책·답안 확정 이후 동일 문서의 라벨 A/B 명세·학습·별도 품질 측정으로 넘어간다.

첫 실제 파일럿40문서/20계열 이상은 절차 확인용 제안 수량이며 통계적 품질 보증이 아니다.
실제 사람이 정해지지 않았거나 실제 원문이 오지 않았다는 사실을 새 합성물/가짜 제출로 채우지 않는다.
도구 준비 완료와 독립 검수·실제 파일럿 완료를 구분해 이어간다.
