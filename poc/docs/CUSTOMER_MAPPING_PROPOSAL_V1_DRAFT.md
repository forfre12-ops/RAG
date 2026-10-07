# 고객사 용어 매핑·변환 근거 계약 v1 — 개발 초안

구현: `src/koipa/mapping_proposal.py`, `scripts/check_customer_mapping.py`.
기존 `org_mapping.py`/수집 어댑터/운영 경로는 변경하지 않는다.
목적은 **원시 표식·접근범위의 변환 후보를 재현 가능하게 제시하는 것**이다. 등급·S/V/M 산정기가 아니다.

## 1. 이번 선택과 경계

현재의 FactPacket은 JSON 근거 값과 주장 값이 동일해야 한다.
원시 ‘가상-대외비’가 ‘confidential’로 바뀌면 단순 값 동등 검증으로 변환 정당성을 표현할 수 없다.
그래서 원시 값을 덮어써 관측 사실로 내보내지 않고 **별도 MappingPreview**를 만든다.

얻는 것: 원시 값·매핑 규칙·출처·판본·시각·충돌 보존과 재검산.
남는 비용: 공급자/매핑 승인을 확인하고 변환을 사실로 사용하는 연결부가 별도로 필요하다.
현재 preview는 FactPacket/ManagementSnapshot 스키마로 수락되지 않는다. 수작업 복사까지 방지하는 인증 수단은 아니다.

## 2. 입력 계약

### MappingPackage

- schema_version: `customer-vocabulary-mapping-v1-draft`
- org_id, mapping_id, version, effective_at(시간대 필수), valid_until(선택)
- declared_status: draft / review_requested / approved / retired
- approval_record_ref: 선택 불투명 참조. approved일 때 필수, draft/review_requested에는 허용 안 함.
- sources: 조직·출처 참조·수집 시각·JSON payload·payload 해시.
- rules: rule_id, fact, raw_value, canonical_value, evidence.

`declared_status=approved`와 approval_record_ref는 **공급자가 선언한 상태/참조**다.
승인자 신원·권한·서명·승인 문서 진위는 확인하지 않는다. 어떤 상태도 자동으로 사실 사용 권한을 주지 않는다.
이번 도구는 선언을 읽을 뿐 정책 게시/승인/폐기 상태를 변경하지 않는다.

지원 fact는 `security_marking`, `access_scope` 두 개뿐이다.
canonical 어휘는 기존 계약과 동일: 표식 top_secret/secret/confidential/none,
접근범위 approved_only/designated/department/all_employees.
문서 유형→등급, S/V/M 점수, 공개 여부, 실제 열람 범위는 매핑하지 않는다.

각 rule의 근거는 JSON Pointer로 아래 **한 행 전체**를 가리킨다.

```json
{"fact":"security_marking","raw_value":"가상-대외비","canonical_value":"confidential"}
```

원시 값과 변환 값 양쪽이 source payload의 행 및 해시와 일치해야 한다.
PDF/자유 문장에서 이 행을 추출하는 기능은 없다. 구조화된 행의 의미가 정당한지는 별도 검수다.
source_id/규칙 ID 중복, 다른 조직 출처, 미래 수집, 잘못된 Pointer/해시/어휘를 거절한다.
같은 선택자(fact/raw_value)에 서로 다른 canonical 값이 있으면 conflict이며 우선순위로 감추지 않는다.
동일 선택자·동일 값의 다른 규칙은 모든 근거를 보존한다. 패키지 전체 충돌 선택자 수도 별도 보고한다.

### RawManagementSnapshot

기존 DocumentBinding의 org_id/document_id/document_revision/original_sha256/document_sha256을 사용한다.
snapshot_id, provider_ref, origin, captured_at, payload, payload_sha256, fields가 필요하다.
valid_until은 선택이며 명시한 유효기간은 검사한다. 기간이 없으면 최신성을 보장하지 않는다.
origin은 system_export / request_metadata / unverified_stored_metadata이다. 모두 미인증 입력이다.

fields의 각 항목은 state와 pointer를 명시한다:

- observed: Pointer가 실제 존재하는 비어 있지 않은 문자열을 가리킴.
- proven_absent: Pointer가 실제 존재하는 null을 가리킴. 선언된 부재를 보존할 뿐 사실 인증 아님.
- unknown: pointer=null. 값/근거를 추측하지 않음.
- fields에서 항목 누락: unknown.

공백 제거·대소문자 변환·비슷한 말 추정·벡터/LLM 보충을 하지 않는다.
이미 canonical처럼 보이는 값도 명시적 규칙이 없으면 unmapped다.
`none` 문자열과 null 부재, 속성 미수신은 서로 다르다.
본문은 필요하지 않다. 문서/원본 해시도 스냅샷과 문맥의 일치만 확인하고 바이너리 진위는 검증하지 않는다.

### MappingContext

독립 호출 문맥: document(기존 CollectionContext), mapping_id, mapping_version, mapping_sha256.
정확한 매핑 ID·판본·정규화 패키지 해시를 지정한다. 임의 최신판 검색/자동 선택은 하지 않는다.
패키지 해시는 `mapping_digest()`로 계산하며 원본 JSON 파일의 바이트 해시와 다르다.
규칙/출처/배열 순서/선언 상태/시각을 포함한다. 범용 JSON 정규화 국제 표준이나 전자서명은 아니다.

## 3. 결과와 차단

| 항목 상태 | canonical_candidate | 의미 |
|---|---|---|
| mapped_candidate | 매핑 후보 | 정확한 선택자에 일치, 근거 결합. observed 사실 아님 |
| unmapped | null | 받은 용어에 대응 규칙 없음 |
| unknown | null | 원시 값 미확인/미수신 |
| absence_claim_preserved | null | 원시 null 부재 주장을 유지 |
| conflict | null | 같은 선택자에 다른 출력. 양쪽 규칙을 참고로 남김 |
| mapping_not_effective / mapping_expired / mapping_retired | null | 해당 매핑으로 변환하지 않음 |

패키지가 유효기간 밖/retired이면 전체 보고서 status=blocked_mapping이다.
부재·미수신 상태는 패키지 차단 상태와 별도로 그대로 남긴다.
draft/review_requested/approved 선언의 유효기간 내 미리보기는 preview_requires_review다.
입력 계약·출처 해시·문서 바인딩 오류는 예외/CLI 종료2다.

항상 false: 승인 진위, 공급자 진위, 조직 인증, 의미상 진위, 사실/관리 스냅샷 내보내기,
학습/모델 평가, 자동화, 고객 정확도 측정, 최종확정.

## 4. 저장과 재검산

기본 report는 원시 값·canonical 값·출처 URI·규칙 이름을 제외하고 상태·개수·지문을 보고한다.
`--preview-out`은 모든 제공 입력·원시 값·변환 값·출처 참조가 포함된 **민감 검토 파일**이다.
기존 파일은 덮지 않으며, 원본 입력의 읽은 바이트/종료 전 지문을 대조한다.
파일 잠금, 변경 후 원복 탐지, 다중 출력 원자적 트랜잭션은 아니다.

`verify_preview()`는 저장된 입력으로 변환을 재실행하고 전체 artifact를 대조한다.
후보 값·근거·플래그만 고친 변경은 거절한다. 입력과 문맥 전체를 새로 꾸민 위조까지 검출하는
서명/외부 승인 앵커는 아니다. replay_verified=true는 내용상 정답/승인/진위가 아니다.

`F:\antigravity\rag\poc`에서:

```powershell
.\.venv\Scripts\python.exe -B scripts/check_customer_mapping.py --demo
.\.venv\Scripts\python.exe -B scripts/check_customer_mapping.py --schema
.\.venv\Scripts\python.exe -B scripts/check_customer_mapping.py --mapping <매핑.json> --snapshot <원시스냅샷.json> --context <독립문맥.json> --out <새진단.json>
# 민감 자료 저장이 허용된 경로에 한해 --preview-out <새검토파일.json>
.\.venv\Scripts\python.exe -B scripts/check_customer_mapping.py --verify-preview <검토파일.json>
```

종료3: 유효한 입력의 검토 후보(성공적으로 계산했어도 승인 아님).
종료0: 가상 데모/스키마/재검산 성공. 종료2: 입력/출력 오류. 어느 값도 배포 승인이 아니다.
데모6개는 모두 명시적 가상 조직/규칙/출처이며 승인 선언 사례도 실제 사람의 승인이 아니다.
