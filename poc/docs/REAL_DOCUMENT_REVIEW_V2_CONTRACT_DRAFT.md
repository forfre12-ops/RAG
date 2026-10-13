# 실제 문서 검수 v2 입력·제출 계약 — 구현 초안

상태: **R1/R2 기술 구현 완료, 사업 정책·실제 검수·정답 확정 미완료**.
여기서 R1/R2는 [설계](REAL_DOCUMENT_REVIEW_V2_DESIGN.md)의 구현 단계다.
검수자 두 명이나 사업상 이중서명을 의무화하는 뜻이 아니다. 기존 단일 서명 GOLD 경로는 그대로다.

## 1. 이번 선택과 경계

고정된 합성40건 검사기를 확장하지 않고 `koipa.review_v2`를 별도 추가했다.
문서·제시 근거·정책·검수 배정을 판본 해시로 고정한 뒤 제출을 검사한다.

얻은 것: 임의 사례 수와 고객사별 주입 정책, 실제 제시자료 제한, 오래된 답안 재사용 방지,
정보 부족 신고, 사람의 정책 해석과 기계 계산 간 불일치 보존.
치른 것: 새 입력 명세와 외부 receipt가 필요하며, 기존 DB/API에서 자동 생성되지 않는다.
이 도구의 통과를 정답/인증으로 사용할 수 없고 실제 공급·검수·승인은 별도 구현/운영이 필요하다.

- 지원 정책은 기존 `parse_shadow_policy`/`evaluate_shadow`의 9개 typed fact와 연산자 범위다.
- TS/S1/S2/S3 네 등급 어휘를 사용하지만 특정 고객사 정책·v22·내용 보호기준을 기본값으로 택하지 않는다.
- 가이드 S/V/M 숫자 곱 전체를 새로 구현한 도구가 아니다. 판단요소·근거 계약은 유지한다.
- 정책 계산은 제출과의 차이를 찾는 참고일 뿐, 사람 정답의 판정자나 교정 답안이 아니다.
- 추출/허가/정책 승인/신원은 수신 주장이다. 참조 문서·URL·외부 시스템을 열거나 인증하지 않는다.

## 2. 입력 구조

`ReviewManifest` (`real-document-review-input-v2-draft`):

| 묶음 | 필수 내용과 검사 |
|---|---|
| 작업 | job_id, org_id, created_at, purpose; 독립 context와 대조 |
| 정책 | policy_id, 원시 policy, policy_sha256; 정책 객체 정규화 해시·조직·시행일 검사 |
| 조직/승인 | org_receipt, policy_approval: unknown 또는 provided + reference/sha256 쌍 |
| 사용허가 | authorization: 상태/조직/참조/해시/목적/사례 범위/유효기간 |
| 사례 | case_id, 문서ID/판본/원본·추출본문 해시, as_of, family_id, origin, partition |
| 제시자료 | input_view, FactPacket, packet_sha256, presented_source_ids |
| 추출·계보 | extraction_state, extraction/collection/mapping receipt 주장 |
| 배정 | 임의 slot, case_ids, assigned_at, identity_mode, 선택 reviewer_ref |

`ReviewContext`: 호출자가 별도 전달하는 org_id/job_id/manifest_sha256/as_of.
manifest를 읽고 그 해시를 복사하는 것만으로 신뢰된 외부 기대값이 생기지는 않는다.
운영에서는 승인된 보관본의 지문을 별도 신뢰 경로로 전달해야 한다. 이번에는 그 경로가 없다.

빈 목록·중복 사례/문서판본/slot/배정 사례는 거절한다. 사례 수40, 규칙명CP-HOLD,
특정 정책 ID를 하드코딩하지 않는다. 동일 문서판본의 서로 다른 view는 별도 작업으로 만든다.
동일 계열·본문 해시·원본 해시가 한 manifest에서 서로 다른 partition에 나타나면 거절한다.
이는 입력한 계열ID/정확 해시 검사이며 의미상 변형 탐지, 기존 학습풀 중복 전수 검사, 봉인 ACL은 아니다.

### 제시자료 제한

- `body_only`: packet의 모든 source가 document_text여야 한다.
- `evidence_only`: document_text source를 포함할 수 없다.
- `body_and_evidence`: 두 종류를 허용하되 존재하지 않는 source를 만들어 채우지 않는다.
- presented_source_ids는 packet.sources와 정확히 같아야 한다. 숨겨진 근거를 같은 packet에 넣지 않는다.
- packet.estimates는 비워야 한다. model/rule_estimated 값을 검수 입력 정답처럼 선탑재하지 않는다.
- FactPacket의 원본 출처·위치·값·기간·조직/문서/정책 결합 검사를 재사용한다.
- synthetic origin은 synthetic_policy_fixture, 실제/공개 문서 선언은 unverified_supplied_assertions와 일치해야 한다.

본문이 없거나 관리정보가 없으면 빈 source/unknown 상태로 접수해 부족 신고를 할 수 있다.
‘본문만 제시했다’는 문자열만 붙이고 숨긴 ACL로 답을 계산하는 입력은 통과하지 않는다.
다만 source.kind 자체의 진위, 본문/정책 note 안에 섞인 답안·민감정보·문체 누설은 자동 탐지하지 않는다.
배포 전 입력 작성자와 검수 운영자의 실제 자료 분리/DLP/블라인드 점검이 필요하다.

### 해시·시간 규약

`manifest_digest()`는 strict model 파싱 후 기본 필드까지 포함한 JSON 정규화 해시다.
case/packet/submission 해시도 model_dump 결과에 `value_digest()`를 적용한다.
정책 해시는 기존 `policy_digest(parse_shadow_policy(raw))`를 사용한다.
파일 바이트 해시와 이 정규화 해시를 혼용하지 않는다. CLI는 파일 바이트 지문도 별도 기록한다.
원본 파일 자체를 열어 원본 해시를 재계산하지 않는다. document_text가 제시되면 그 문자열 해시는
검사하지만, 본문 미제시 상태의 document_sha256는 독립 context/메타데이터와 결합된 주장이다.

문서 as_of ≤ 작업 created_at ≤ 검증 as_of, 배정은 작업 이후/검증 시각 이전,
제출은 배정 이후/검증 시각 이전이어야 한다. 시간대 없는 시각은 거절한다.
정책 시행일과 근거의 유효성은 기존 계산기 규약으로 **고정한 문서 as_of**에서 검사한다.
따라서 현재 실시간 ACL 상태를 확인했다는 의미가 아니다. 정책 날짜 비교는 기존 UTC 날짜 규약이다.
허가 provided는 valid_from ≤ created_at ≤ 검증 as_of < valid_until 및 조직/목적/전체 사례 범위를 만족해야 한다.

## 3. 빈 제출 양식과 제출 계약

`blank_submissions()`는 선택 slot의 사례ID와 case/packet/policy/manifest 해시만 담은 빈 양식을 만든다.
답안·정책 계산 결과·근거값·본문·검수자 이름/서명은 만들지 않는다.
실제/공개 문서의 빈 양식 생성은 제공된 허가 주장 없으면 거절한다. denied는 합성도 거절한다.
합성은 unknown 허가로 계약 시험이 가능하다. 이는 자료 사용 승인이나 신원 인증이 아니다.
진단용 inspect/validate는 허가 부족·거부 상태도 보고할 수 있다. 실제 문서를 외부로 배포하는 기능은 없다.

제출 배치 (`real-document-review-submissions-v2-draft`)는 한 slot의 배정 사례를 빠짐없이 포함한다.
다른 작업/조직/입력 판본, 중복/누락/추가 사례, 오래된 정책/packet/case 해시는 거절한다.

각 행:

- pending: decision/reviewer_ref/actor_kind/submitted_at/prior_answers_seen 모두 null.
- submitted: 위 필드 모두 명시. prior_answers_seen=false도 명시적 자기신고이며 독립성 인증이 아니다.
- 한 slot의 제출 신원 주장은 하나여야 한다. 사전 배정 신원이 있으면 같아야 한다.
- human_declared 또는 ai_assisted_declared만 받는다. 둘 다 실제 사람 서명/확정 자격을 부여하지 않는다.

제출 decision에는 status, grade, rule_ids, evidence, rationale, not_higher_reason,
not_lower_reason, requests가 있다. 최상/최하위 경계에서도 이유 필드는 해당 없음의 이유를 명시한다.

| 상태 | 구조 조건 | 검증 통과 후 처리 |
|---|---|---|
| candidate | 등급, 존재하는 규칙 ≥1, 근거 ≥1, 미해결 requests 없음; 인용 규칙의 등급과 일치 | 입력된 판단 그대로 보존; 정책 계산과의 차이를 검토 사유로 기록 |
| missing_evidence | grade=null, requests ≥1 | 근거0/규칙0 허용; 필요한 자료 요청 |
| policy_conflict | grade=null, 알려진 규칙 ≥2, requests ≥1 | 충돌 주장 검토; 기계 계산과 같다는 이유로 실제 충돌을 확정하지 않음 |
| policy_gap | grade=null, requests ≥1 | 없는 규칙을 만들지 않고 기준 공백 검토 |

근거는 제시된 source의 SHA와 정확한 text_span 또는 JSON Pointer/값 SHA에 결합한다.
text_span은 Python 유니코드 문자 인덱스 [start,end)이며 UTF-8 바이트/UTF-16 위치가 아니다.
JSON의 null/false/빈 값과 필드 부재는 구별한다. 선택 fact를 적으면 해당 assertion의 실제 근거여야 한다.
값/위치가 맞아도 문맥상 적절한 근거인지, 상·하위 배제 이유가 타당한지까지 인증하지 않는다.

## 4. 오류, 검토 사유와 결과 권위

잘못된 스키마/판본/규칙ID/근거 위치·해시/인용 규칙-등급 불일치는 **입력 오류**로 거절한다.
기계 계산과 다른 의미상 판단은 답안을 덮어쓰지 않고 다음 사유로 보고한다.

- cited_rule_not_supported_by_supplied_facts
- policy_blocks_candidate: 더 높은 가능한 규칙, 충돌 등으로 정책 계산이 후보를 내지 못함
- submitted_grade_differs_from_policy_candidate
- required_rule_facts_not_cited: 인용은 유효하지만 해당 규칙 판단요소 근거가 인용되지 않음
- extraction_unknown/incomplete, prior_answer_exposure_declared, ai_assistance_declared
- missing_evidence/policy_conflict/policy_gap_claim_requires_review

이 목록은 조정자/코디네이터 진단용이다. 독립 제출 전에 검수자에게 피드백하면 블라인드가 깨질 수 있다.
개별 정책 참고 등급·rule_trace는 기본 보고서와 빈 양식에 넣지 않는다.
보고서는 사례ID/지문/상태/개수/고정 사유만 담지만 식별자도 민감할 수 있어 익명화를 보장하지 않는다.

모든 결과는 final_grade=null, gold_eligible/training_allowed/model_evaluation_allowed/
automation_allowed/finalized=false, 신원/허가/승인/독립성 인증=false다.
제출 개수·후보/보류 개수는 작업 상태 통계이며 quality_accuracy=null이다.
사람 두 명의 비교·일치율·조정 결과·정답 확정은 아직 구현하지 않았다.

## 5. 실행

`F:\antigravity\rag\poc`에서:

```powershell
.\.venv\Scripts\python.exe -B scripts/check_document_review.py --demo
.\.venv\Scripts\python.exe -B scripts/check_document_review.py --schema
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_review_v2.py -q
```

아래 파일명은 **운영자가 준비할 입력/새 출력 자리표시자**다. 실제 고객 자료를 이 작업에서 만들지 않았다.

```powershell
.\.venv\Scripts\python.exe -B scripts/check_document_review.py --manifest review-input.json --context trusted-context.json
.\.venv\Scripts\python.exe -B scripts/check_document_review.py --manifest review-input.json --context trusted-context.json --slot reviewer-A --template-out new-blank.json
.\.venv\Scripts\python.exe -B scripts/check_document_review.py --manifest review-input.json --context trusted-context.json --submissions supplied-submissions.json --out new-check.json
```

종료0은 demo/schema만, 종료3은 형식 검사를 통과한 검토 전용 입력, 종료2는 오류다.
출력 경로는 새 파일만 허용하고 입력/출력 겹침을 거절한다. 읽은 입력 지문을 저장 직전 재확인한다.
여러 출력 파일의 원자적 트랜잭션/파일 잠금/서명/재전송 저장소는 없으며 동시 작업에서 배타적 사용이 필요하다.
JSON Schema만으로 교차 필드/해시 검증은 충분하지 않다. 반드시 Python validator를 실행한다.

## 6. 다음 단계와 완료 조건

1. **R3 비교·조정기**: 같은 입력 view/판본의 두 제출을 묶고, 부족/오류/판단 차이를 분리.
   조정 제안은 양쪽 제출 해시에 결합. 입력이나 제출이 바뀌면 재사용 거절.
   동일인/사전답안 노출/AI 보조는 자기신고 경계 유지. 일치해도 자동 확정 금지.
2. **추출 판본 연결부**: 실제 ext.text와 정규화본 구분, 완전성·OCR·재추출 receipt 전달.
   현재 extraction_state=complete는 공급자 주장이지 원본 전체 회수 증명이 아니다.
3. **실제 파일럿 R4**: 허가·정책 원본/승인·모집단/분모·담당자·공급18항목 수신 후 실행.
   이번 가상2정책/테스트 데이터를 실제 표집이나 정답지로 대신하지 않는다.
4. **인증·확정 R5**: 인증된 조직/개인/권한/보관 경로를 별도 연결·검증한 후 기존 확정 계약과 통합 검토.

D01~D08 미정 상태에서 기술 회귀시험은 가능하지만 실제 정책 승인·정답 확정·학습/운영 재개는 불가하다.
