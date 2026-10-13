# 읽기 전용 근거 수집 어댑터 v1 — 미승인 개발 계약

문서 추출 스냅샷과 명시적으로 제공된 관리정보를 기존
[근거 계약](POLICY_FACTS_SHADOW_CONTRACT_V1_DRAFT.md)의 `FactPacket`으로 변환한다.
새로운 등급 체계나 승인 권한을 만들지 않는다. 실제 시스템 연결·본문 사실 자동 추출은 아직 아니다.

## 범위와 선택한 대안

본문·키워드로 관리정보를 복원하지 않고, **판본에 연결된 스냅샷 공급 계약**을 먼저 구현했다.
얻는 것은 조직/문서/판본 혼합 방지와 미수신·부재·충돌의 구별이다.
그 대가로 공급자의 명시적인 판본·완전성 정보가 필요하며, 없으면 진행이 보류될 수 있다.
실제 고객사 용어를 canonical 값으로 바꾸는 매핑은 승인/계보가 필요해 이번 자동 변환에서 제외했다.

- 모듈: `src/koipa/evidence_collection.py`, 순수 함수 `collect_evidence()`.
- 로컬 CLI: `scripts/collect_policy_evidence.py`.
- 출력: 기존 `policy-facts-v1-draft` 패킷과 별도 수집 진단/정책 비교 결과.
- 운영 서비스/추출기/정책 엔진을 수정하거나 새 모듈을 운영에서 호출하지 않는다.
- 문서·URL·원본 바이너리·DB를 어댑터가 직접 조회하지 않는다. CLI는 지정한 세 JSON만 읽는다.

## 1. 독립 호출 문맥과 문서 판본

`CollectionContext`의 필수 값:

| 필드 | 의미 |
|---|---|
| `org_id`, `document_id` | 대상 조직과 문서 식별자 |
| `document_revision` | 관리시스템의 불변 문서 판본 식별자 |
| `original_sha256` | 공급자가 주장하는 원본 바이너리의 SHA-256 |
| `document_sha256` | 정확한 추출 문자열의 UTF-8 SHA-256 |
| `as_of` | 시간대가 있는 비교 기준 시각 |

추출·관리 스냅샷 모두 앞의 다섯 값을 보내며 독립 호출 문맥과 같아야 한다.
같은 본문이더라도 다른 원본/판본/조직의 관리정보는 거절한다.
추출 문자열 해시는 직접 검산하지만 원본 바이너리 바이트는 읽지 않으므로
`original_binary_bytes_verified=false`다. 같은 값을 스스로 써 넣는 행위는 공급자 인증이 아니다.

## 2. 수집 입력

`CollectionInput`의 `schema_version`은 `policy-evidence-collection-v1-draft`다.
`material_role`은 기본 `unverified_supplied_assertions`, 가상 시험은 `synthetic_policy_fixture`다.
`extraction`은 필수, `management`와 `estimates`는 기본 빈 배열이다.
정의하지 않은 필드·숫자/불리언 강제 변환·잘못된 해시는 거절한다.

### 추출 스냅샷

판본 다섯 값 외 필수: `extraction_id`, `extractor_version`, `method`, 정확한 `text`,
`captured_at`, `source_ref`.

- `completeness`: complete / incomplete / unknown. 기본 unknown.
- `table_coverage`: complete / incomplete / unknown / not_applicable. 기본 unknown.
- `pages`, `total_pages`: 선택 정수. 실제 회수/전체 페이지 수를 구분한다.
- `quality`: 선택 0~1 추정치. 완전성 판단을 대신하지 않는다.
- `ocr_used`, `warnings`, `error`: 공급받은 추출 신호를 보존한다.

기존 `ExtractResult`에는 원본/추출 판본과 인증된 공급자 정보가 없으므로 그것만으로 이 입력을
완성할 수 없다. 기존 `table_coverage=None`을 complete로 바꾸거나 quality=0.95/1.0으로
완전성을 선언하지 않는다. 외부 exporter가 판본을 결합하고 완전성 확인 범위를 별도로 제공해야 한다.
원본과 추출본의 의미상 완전성을 이 어댑터가 직접 확인하지는 않는다.

### 관리 스냅샷

판본 다섯 값 외 필수: `snapshot_id`, `provider_ref`, `captured_at`, `fields`.
`valid_until`은 선택이다. 제공되면 `captured_at <= as_of < valid_until`을 검사한다.
기한이 없을 때 최신성을 보장하거나 임의 TTL을 부여하지 않는다.

`fields`는 아래 다섯 속성만 허용한다. 각 항목의 `state`와 `value`는 모두 필수다.

```json
{
  "access_scope": {"state": "observed", "value": "approved_only"},
  "security_marking": {"state": "proven_absent", "value": null},
  "actual_reader_scope": {"state": "unknown", "value": null}
}
```

| 속성 | observed 허용 값 |
|---|---|
| `security_marking` | top_secret / secret / confidential / none |
| `access_scope` | approved_only / designated / department / all_employees |
| `owner_org` | 공급된 비어 있지 않은 문자열 |
| `dlp_label` | 공급된 비어 있지 않은 문자열 |
| `actual_reader_scope` | 공급된 비어 있지 않은 문자열; 의도된 ACL을 복사하지 않음 |

`proven_absent`는 명시적 null만 허용한다. 누락 또는 `unknown/null`은 unknown으로 남는다.
`security_marking="none"`은 기존 canonical 어휘의 **관측 문자열**이다.
속성의 명시적 부재 null과 다르며, 자동으로 공개/비공개나 M 점수로 번역하지 않는다.
`owner_org`, `dlp_label`, `actual_reader_scope`의 조직별 어휘/의미는 아직 승인된 공통 표준이 아니다.

고객사 원시 용어 `대외비`, 공백 추가, 대소문자 별칭을 자동 정규화하지 않는다.
상위 exporter가 변환했다면 원시 속성→매핑 판본→canonical 값의 별도 계보와 검토가 필요하다.
현재 어댑터는 제공된 canonical 값과 그 스냅샷 결합만 검사한다.
`source_type=internal`, 등급/점수, S/V/M, `evaluation_factors`, `rule_factors`는 관리 사실로 받지 않는다.

### 본문과 추정

본문은 출처로 보관하지만 이 단계에서 사실 주장을 자동 생성하지 않는다.
본문의 ‘대외비’·‘공개’ 표현으로 보안표식/공개 여부를 확정하지 않는다.
모델/룰 값은 기존 `FactEstimate` 형식의 `estimates`에만 보관하며 정책 조건을 충족시키지 않는다.
공개 출처/검토자 판단/본문 인용 주장을 수집하는 전용 어댑터는 후속 범위다.
따라서 해당 사실을 요구하는 정책은 이번 관리정보만으로 후보 등급을 내지 못할 수 있다.

## 3. 변환과 보류

1. 입력을 복사하고 조직·문서·원본/추출 판본·시각을 검사한다.
2. 추출 본문 출처와 본문을 제외한 추출 계보 기록 출처를 각각 만든다.
3. 관리 스냅샷 전체를 근거 payload로 유지하고 `/fields/<fact>/value`에 주장을 결합한다.
   source ID는 스냅샷 해시에서 만들며 snapshot ID/provider_ref/기간도 payload 해시에 포함한다.
4. 기존 `resolve_packet()`과 `evaluate_shadow()`로 바인딩/정책 검증을 재사용한다.
5. 정상 수집이면 패킷과 비교 결과, 추출 보류이면 진단만 반환한다.

같은 공급자의 같은 snapshot ID가 중복되면 거절한다. 다른 유효 스냅샷의 상충 값은 conflict다.
최근 시각/배열 마지막 값으로 덮지 않는다. 같은 주장에 대한 독립 근거는 합쳐진다.
unknown 기록이 유효한 알려진 주장을 지우지는 않는다.

다음은 `held_extraction`이다:

- 실질 문자열이 비었거나 공백/추출 placeholder뿐인 본문.
- completeness 또는 표 회수 상태가 incomplete/unknown.
- 추출 오류 또는 경고가 하나라도 있음.
- 일부 페이지만 회수, 0페이지, 페이지 수 한쪽만 수신.

회수 페이지 > 전체 페이지 등 모순된 입력은 보류가 아니라 입력 오류다.
양쪽 페이지 수가 없으면 비페이지 문서일 수 있으므로 페이지 검사로 새 보류를 만들지 않는다.
그때도 completeness와 table_coverage의 명시적 값은 필요하다.
모든 경고를 보류하는 것은 이번 독립 어댑터의 보수적 개발 기본값이며 운영 정책 승인이 아니다.

보류 때는 `packet=null`, `packet_available=false`, `policy_proposal=null`이다.
구형 패킷 소비자가 추출 불완전성을 모르고 후보 등급을 내는 우회를 막기 위한 것이다.
관리정보 미수신/충돌은 패킷 안에서 unknown/conflict로 표현 가능하므로 별도 추출 보류와 구분한다.
`ready_for_shadow`는 수집 구조가 준비됐다는 뜻이지 등급 후보/승인/자동처리 가능을 뜻하지 않는다.

## 4. 출력 보안과 실행

기본 보고서는 본문·관리 값·출처 URI를 출력하지 않고 지문·상태·누락/충돌 항목을 기록한다.
정책 비교 결과의 문서/조직/정책/규칙 식별자는 남는다. 임의 문자열의 개인정보 자동 제거기는 아니다.
`--packet-out`을 명시하면 **본문·관리 값·출처 URI가 포함된 민감 패킷**을 새 파일에 저장한다.
해당 경로의 접근 권한·보관/폐기·암호화는 호출자의 책임이며 인증정보를 URI에 넣지 않는다.
추출 보류면 지정해도 패킷 파일을 만들지 않는다. 보고서와 패킷은 다중 파일 트랜잭션이 아니다.

`F:\antigravity\rag\poc`에서:

```powershell
.\.venv\Scripts\python.exe -B scripts/collect_policy_evidence.py --demo
.\.venv\Scripts\python.exe -B scripts/collect_policy_evidence.py --schema
.\.venv\Scripts\python.exe -B scripts/collect_policy_evidence.py --policy <정책.json> --snapshot <수집입력.json> --context <독립문맥.json> --out <새진단.json>
# 민감 패킷이 실제로 필요한 허가된 로컬 경로에 한해서만 추가:
# --packet-out <새근거패킷.json>
```

정책 JSON은 기존 `check_policy_shadow.py`와 동일하며 `policy_version`을 사용한다.
내부 Policy dataclass의 `version` 필드 이름과 혼동하지 않는다.
패킷은 기존 비교 계산기에서 재사용 가능하고 문맥은 `CollectionContext.fact_context()`로 얻는다.

JSON 중복 키/NaN/Infinity, 기존 출력 덮어쓰기, 같은 보고서/패킷 출력 경로를 거절한다.
지정 입력의 읽은 바이트와 계산 후 지문을 대조하며 불일치하면 파일을 내보내지 않는다.
파일 잠금이나 변경 후 원복 탐지, 공급자 진위 인증을 구현한 것은 아니다.

종료0은 구조상 후보/데모/스키마 성공, 3은 추출 또는 정책 비교 검토 필요, 2는 입력/출력 오류다.
데모의 기대된 보류는 시험 시나리오이므로 데모 전체는0이다. 어떤 종료 코드도 승인권을 주지 않는다.
모든 실제 결과에서 인증·독립 완전성 검증·정책 승인·학습/평가·자동화·고객 정확도·최종확정은 false다.

## 5. 실제 연동 전 남은 조건

- 공급자와 고객사 관리 속성의 실제 내보내기 형식, read-only 권한, 인증된 조직 문맥.
- 원본 바이너리/추출 작업 판본 확인, ACL과 실제 열람 정보 구별, 자료 만료/폐기 기준.
- 원시 용어 매핑의 판본·승인·근거 계보. 이번 adapter가 이를 인증한다고 주장하지 않는다.
- 정책 책임자의 D01~D08 결정과 실제 문서 독립 정답/사용 허가.
- 제한된 실제 스냅샷 대조 후 별도 운영 shadow 연결 결정. 기존 등급 경로 자동 교체 없음.
