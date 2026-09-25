# 근거 입력·정책 비교 결과 계약 v1 — 미승인 초안

상태: **운영과 분리된 개발 계약**. 사용자의 ‘다음 진행’을 앞서 제안한 근거+정책 후보 구조의
구현 진행으로 해석했다. 고객사 정책 승인, D01~D08의 담당자 승인, 실제 정답 확정은 아니다.
기존 모델의 등급 예측을 제거하거나 S/V/M 요소가 불필요하다고 결정하지 않았다.

## 무엇을 만들었나

- `policy_facts.py`: 조직·문서·정책 판본에 결합된 근거와 사실 주장 입력을 검사한다.
- `policy_shadow.py`: 새 사실 계약으로 정책을 검산하고, 기존 결과와 분리된 후보/보류 결과를 만든다.
- `check_policy_shadow.py`: 명시적으로 지정한 로컬 파일 또는 가상 예제로 실행한다.

운영 API·분류 서비스·추론 파이프라인은 새 모듈을 호출하지 않는다.
`attach_shadow()`는 순수 결합 함수이며 이 함수를 실제 서비스에 연결하는 변경은 하지 않았다.

## 1. 입력의 권위와 세 상태

여기서 ‘사실’은 **제공자가 사실이라고 주장하며 근거를 결합한 값**이다.
`observed`라고 입력하거나 해시가 맞는다고 진위가 인증되지는 않는다.
실제 공급자 인증·검수자 신원·전자서명·근거의 의미상 적합성은 이 계약 밖이다.

| 상태 | 허용 값 | 의미 |
|---|---|---|
| `observed` | true / 비어 있지 않은 문자열·문자열 목록 | 제공자가 관측했다고 주장하며 근거 결합 |
| `proven_absent` | boolean은 false, 목록은 [], 문자열 속성은 null | 명시적인 부재/반증 기록에 결합된 주장 |
| `unknown` | null, origin 없음, evidence 없음 | 값 미수신 또는 미확인; false/0으로 변환하지 않음 |

같은 사실에 서로 다른 유효 주장이 있으면 파생 상태 `conflict`로 남긴다.
첫 값·마지막 값·더 높은 등급을 임의로 채택하지 않는다.
같은 값의 복수 근거는 합치고, `content_kinds` 목록의 순서 차이는 충돌로 보지 않는다.
원래 근거 JSON 값과 위치 해시는 순서를 포함해 정확히 대조한다.

모델·룰 추정은 별도 `estimates` 배열이다. `model_estimated`/`rule_estimated`는 사실의 origin으로
허용하지 않으며 정책 조건을 충족시키지 않는다. 등급·점수·`evaluation_factors`·`rule_factors`를
패킷에 끼워 넣거나 사실 이름으로 사용하는 것도 거절한다.

## 2. 입력 구조

### 독립 호출 문맥 `FactContext`

`org_id`, `document_id`, `document_sha256`, `as_of`가 필요하다.
향후 서비스에서는 인증된 조직과 서버가 확인한 문서 판본에서 만들어야 한다.
현재 로컬 CLI에서는 사용자가 제공하는 비교 문맥이며, 문맥 객체 자체를 인증 수단으로 보지 않는다.

### 패킷 `FactPacket`

필수 필드:

- `schema_version`: `policy-facts-v1-draft`
- `org_id`, `document_id`, `document_sha256`
- `policy_version`, `policy_sha256`

배열은 `sources`, `facts`, `estimates`이고 생략하면 빈 배열이다.
`material_role`은 기본 `unverified_supplied_assertions`, 가상 예제는 `synthetic_policy_fixture`다.
정의하지 않은 필드는 거절한다. 누락된 사실은 unknown으로 처리한다.

정책 해시는 `policy_digest(Policy)`의 **정규화된 파싱 결과** 해시다.
원래 JSON 파일 바이트 해시와 다르다. CLI는 원래 입력 파일 바이트 해시도 별도로 기록한다.
룰 배열 순서·정책/규칙 note를 포함한 정규화 객체가 바뀌면 정책 해시도 바뀐다.
정책 파일의 `_` 안내 필드는 실행 정책에서 제외되며 원본 파일 지문에는 포함된다.

### 근거 자료 `EvidenceSource`

필수: `source_id`, `kind`, 조직·문서 ID/본문 해시, `payload`, `payload_sha256`,
`captured_at`, `source_ref`. 선택: `valid_until`.

| kind | payload | 사용하는 방식 |
|---|---|---|
| `document_text` | 정확한 추출 본문 문자열 | 본문 해시까지 문서 문맥과 대조; 내용 사실 근거 |
| `system_metadata` | JSON 객체 | 관리시스템 속성 값의 위치·값 대조 |
| `public_source` | 공개 출처 스냅샷 문자열 | http(s) 출처 표시 필수; 네트워크 조회·공개 사실 인증은 안 함 |
| `human_review` | JSON 객체 | 제출된 판단 기록의 위치·값 대조; 실제 사람 인증은 안 함 |

본문 해시는 현재 계약에서 **정확한 추출 문자열의 UTF-8 SHA-256**이다.
원본 PDF/HWP/Office 바이너리의 해시나 청크 해시로 바꾸어 넣지 않는다.
추출기/추출 판본·원본 바이너리 해시의 전체 계보 연결은 후속 수집 어댑터에서 다뤄야 한다.

모든 출처는 같은 조직·문서 판본에 결합해야 한다. 자료의 조회 시각이 as_of보다 미래이면 거절한다.
valid_until이 주어지면 `captured_at <= as_of < valid_until`을 확인한다.
만료일이 없으면 임의 유효기간을 만들지 않는다. 이 경우 최신 상태 보장은 별도 과제다.
시각에는 시간대가 필요하다. 정책의 날짜형 시행일은 이 개발 계약에서 UTC 날짜로 해석한다.
고객사 현지 시각·소급 적용·폐기 정책은 별도 승인/확장 대상이다.

### 사실 주장 `FactAssertion`

`fact`, `state`, `value`, `origin`, `evidence`로 구성한다.
알려진 주장에는 근거가 필수이며 아래 두 위치 형식을 지원한다.

- `text_span`: Python 문자열 기준 0부터 시작하는 `[start, end)` 문자 위치. UTF-8 바이트 위치가 아니다.
  지정 범위의 UTF-8 SHA-256과 `value_sha256`을 대조한다. 인용문이 주장을 의미상 뒷받침하는지는 검증하지 않는다.
- `json_pointer`: `/access_scope` 같은 경로. `~0`, `~1` 이스케이프와 배열 인덱스를 지원한다.
  **속성이 실제 존재해야** 하며 그 값과 주장 값의 정규 JSON 해시가 같아야 한다.
  명시된 null과 속성 누락은 다르며, false와 숫자0도 다르다.

각 근거에는 `source_id`, `source_sha256`, `locator`, `value_sha256`이 들어간다.
JSON 값의 해시는 UTF-8, 키 정렬, `ensure_ascii=False`, 구분자 `(',', ':')`, 비유한 수 금지 방식이다.
본문·JSON 값·정책의 해시는 목적별로 구분하며 임의 공백 정규화를 하지 않는다.

## 3. 허용 사실과 S/V/M의 관계

| 그룹 | 현재 허용 사실 | 현재 범위 |
|---|---|---|
| S 근거 묶음 | `public_disclosed` | 공개 출처 또는 별도 검토 기록; 본문/내부 저장 위치로 미공개를 추정하지 않음 |
| V 관련 내용 근거 묶음 | `content_kinds`, `has_concrete_parameters` | 구체 내용이 있다는 근거; 경제적 유용성 점수가 확인됐다는 뜻은 아님 |
| M 관련 관리 근거 묶음 | `security_marking`, `access_scope`, `owner_org`, `dlp_label`, `actual_reader_scope` | 관리시스템/검토 기록만 허용; 본문에서 실제 ACL을 창작하지 않음 |
| 기타 | `legal_protection_basis` | 근거 문자열 주장; 법적 적용 여부를 시스템이 인증하지 않음 |

`public_disclosed`, `has_concrete_parameters`는 boolean, `content_kinds`는 중복 없는 문자열 목록,
나머지는 문자열 또는 명시된 부재 null이다.
`factor_evidence_groups`는 근거 항목의 분류와 미확인 목록이며 **S/V/M 점수·필수 요소 완성 판정이 아니다.**
어떤 근거로 S/V/M의 0/1/2를 정할지, 예외를 언제 적용할지는 D03/D04의 정책 결정이 남아 있다.
FUN-023 전체 이행 완료나 실제 등급모델 학습 완료를 주장하지 않는다.

## 4. 정책 계산 계약

기존 `Policy`/`Rule`의 등급 순서·우선순위·조건을 사용한다. 기존 엔진과 출력은 변경하지 않는다.
새 계층은 unknown과 부재를 구분해야 하므로 별도 조건 평가/결과 억제 로직을 둔다.

- `eq`, `ne`, `in`, `not_in`, `contains_any`, `exists`, `missing`을 지원한다.
- 현재 사실에는 숫자형 속성이 없어 `gte/lte`는 거절한다. boolean·문자열을 숫자로 강제 변환하지 않는다.
- unknown/conflict는 `exists`/`missing` 조건에서도 판단 불가다. ‘입력 안 옴’을 ‘없음 확인’으로 쓰지 않는다.
- boolean false는 값이 있는 부재/반증 주장이다. `exists`는 true지만 `eq true`는 false다.
- AND 중 하나가 확정 false이면 해당 규칙은 미적용이며 다른 미확인 항목 때문에 다시 보류하지 않는다.
- `requires_evidence`는 근거가 결합된 주장 여부를 본다. 부재 자체를 명시적으로 확인한 주장도 포함한다.
- 적용 가능한 규칙은 작은 priority 우선이다. 같은 최고 우선순위에서 등급이 갈리면 주 등급을 비운다.
- 보류 규칙이 현재 후보보다 민감하거나 우선순위가 앞서거나 같으면 결정을 보류한다.
- 규칙이 하나도 맞지 않아도 `default_grade`를 자동 적용하지 않는다.
- 입력 사실에 충돌이 하나라도 있으면 이 초안은 보수적으로 주 등급을 비운다.

| status | 주 grade | 의미 |
|---|---|---|
| `candidate` | 규칙 후보 | 근거 결합·정책 계산상 후보. 승인/최종확정 아님 |
| `needs_evidence` | null | 결정을 바꿀 수 있는 규칙의 근거 부족 |
| `needs_evidence_conflict_review` | null | 사실 주장 충돌 |
| `needs_policy_review` | null | 적용되는 동순위 정책 충돌 |
| `no_matching_rule` | null | 적용 규칙 없음 |
| `invalid_input` | null | 결합 함수에서 입력 오류를 기존 결과와 격리 |

`matched_candidate`는 보류 중에도 어떤 규칙 후보가 있었는지 보는 참고 항목이지 확정 결과가 아니다.
`would_require_review`는 **이 비교 계산에서 추가 검토가 필요한지**이며, false여도 사람 확정이나
고객사 정책 승인을 생략할 수 없다. `automation_allowed`, `policy_approval_verified`,
`evidence_authenticity_verified`, `semantic_truth_verified`, `finalized`는 모두 false다.
이 초안 출력은 학습/모델 성능 분모 사용도 false로 표시한다.

## 5. 실행과 기존 결과 보존

`attach_shadow(existing_result, policy=None, ...)`는 입력을 검사하지 않고 기존 결과의 깊은 복사만 반환한다.
정책이 있으면 `policy_proposal`만 추가한다. 기존 label/status/score/needs_review 등은 수정하지 않는다.
이미 같은 키가 있으면 덮어쓰지 않고 거절한다. 사실 입력 오류는 추가 결과에만 담는다.
이 동작은 함수 테스트로 확인했으며 실제 서빙 기능 플래그·API 연결 완료를 뜻하지 않는다.

`F:\antigravity\rag\poc`에서:

```powershell
.\.venv\Scripts\python.exe -B scripts/check_policy_shadow.py --demo
.\.venv\Scripts\python.exe -B scripts/check_policy_shadow.py --schema
.\.venv\Scripts\python.exe -B scripts/check_policy_shadow.py --policy <정책.json> --packet <근거패킷.json> --context <서버문맥.json> --out <새결과.json>
```

CLI는 경로를 현재 디렉터리 기준으로 읽고, 지정 파일 외의 원문·URL·DB를 읽지 않는다.
JSON 중복 키/NaN을 거절하며 읽은 바이트와 종료 직전 지문을 대조한다.
기존 출력 파일은 덮어쓰지 않는다. 이 검사는 파일 잠금/중간 변경 후 원복 탐지가 아니다.
종료0은 후보 또는 데모 계산, 종료3은 검토 필요, 종료2는 입력/출력 오류이며 어떤 값도 승인권을 주지 않는다.
`--schema`는 구조용 JSON Schema다. 해시·근거 위치·정책 결합 등의 교차 검사는 Python 검증기가 필요하다.

## 6. 다음 실제 연결 작업

1. 현재 비교용 계약에 맞춰 문서 판본·시스템 메타데이터를 보내는 **읽기 전용 수집 어댑터** 설계/시험.
   공급하지 못하는 사실은 unknown으로 유지하고 모델 추정은 estimates에만 둔다.
2. 인증된 조직 문맥·근거 공급 주체·자료 최신성·정책 버전/승인 권한을 실제 시스템과 연결.
3. 정책 책임자가 사실→요소/등급 규칙, 보류/충돌 처리와 적용 범위를 고정.
4. 사용 허가된 실제 문서의 독립 근거 검수로 추출/정책 오류를 따로 측정.
5. 그 후 별도 결정으로 운영 shadow 연결. 기존 등급·검수 경로 변경과 모델 교체는 추가 승인 대상.

현재는 위 1~5의 실제 연동이나 고객사 정답 검증을 수행하지 않았다.
