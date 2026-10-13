# 실제 문서 검수 v2 비교·조정 제안 계약 — R3 구현 초안

상태: **R3 로컬 도구 구현 완료. 실제 검수·공식 조정·정답 확정은 미완료**.
[R1/R2 입력·제출 계약](REAL_DOCUMENT_REVIEW_V2_CONTRACT_DRAFT.md)을 그대로 재사용한다.
이 문서는 앞선 계약의 ‘다음 비교·조정기’ 단계에 대한 후속이다. 기존 단일 서명 GOLD 요건은 변경하지 않는다.

## 1. 선택한 범위

`review_pair_v2.py`와 `compare_document_reviews.py`를 별도로 추가했다.
기존 `review_v2.py`, 합성40건 검수기, 운영 분류/확정 경로를 수정하지 않는다.

얻은 것: 입력·제출 판본 추적, 분모가 드러나는 일치 통계, 조정 제안의 근거/쟁점 누락 방지.
치른 것: 두 slot의 배정 범위가 같아야 하며, 서로 다른 view/정책 작업을 억지로 합쳐 비교하지 않는다.
원인 진단·자료 진위·검수자 독립성을 자동 판정하는 도구가 아니므로 실제 운영 검토가 필요하다.

## 2. 비교 입력과 판본

입력은 동결한 manifest, 독립 ReviewContext, left/right SubmissionBatch다.
두 제출 각각에 R2 validator를 실행하고 다음을 추가 검사한다.

- 서로 다른 slot이어야 한다. 같은 제출을 두 번 넣어 독립 검수처럼 집계하지 않는다.
- 배정 case_id 집합이 같아야 한다. 교집합만 골라 미제출/불일치를 분모에서 숨기지 않는다.
- 양쪽 모두 같은 manifest/문서판본/정책/제시자료 case 해시와 결합돼야 한다.
- 양쪽 제출은 pending 행을 포함하여 배정 범위를 모두 표현해야 한다.

`PairBinding`은 조직/작업/manifest SHA, left/right slot, 양쪽 제출 배치의 정규화 SHA를 담는다.
`pair_sha256`는 그 구조 전체의 정규화 SHA다. 양쪽 역할 순서를 포함한다.
행별로 case SHA와 양쪽 SubmissionRow SHA도 기록한다.

설명 한 글자나 제출 순서가 바뀌어도 배치 SHA가 달라져 기존 조정안이 무효가 된다.
반면 검증 시각만 뒤로 이동한 것은 pair 자체의 새 판본으로 보지 않는다.
재검증 때 허가 유효기간 등 R2의 시각 조건은 다시 검사한다.
해시는 서명이 아니다. 신뢰된 보관본/독립 context와 비교하지 않으면 함께 고친 위조를 인증할 수 없다.

## 3. 차이와 집계 기준

두 행이 모두 submitted일 때만 비교한다. 하나라도 pending이면 awaiting_pair이며 분모에서 분리한다.

| 항목 | 비교 방식 | 분모 |
|---|---|---|
| 결과 일치 outcome_agreement | (status, grade) 쌍 일치 | 양쪽 제출 완료 쌍 |
| 등급 일치 grade_agreement | grade 일치 | 양쪽 모두 candidate인 쌍 |
| 규칙 일치 rule_set_agreement | rule_ids 집합 일치 | 양쪽 제출 완료 쌍 |
| 근거 일치 evidence_set_agreement | fact 표기를 포함한 인용 구조 집합 일치 | 양쪽 제출 완료 쌍 |

각 통계는 numerator/denominator/rate를 함께 내보내고, 분모0은 rate=null이다.
보류끼리 같은 결론이어도 4등급 일치 분모에 넣지 않는다. grade=null 둘을 같은 정답으로 세지 않는다.
빈 규칙/근거 집합끼리의 일치는 집합 일치일 뿐 증거 충분성이나 정답 인증이 아니다.

개별 differences는 status/grade/rule_ids/evidence/rationale/not_higher_reason/not_lower_reason/requests다.
규칙·근거·요청 목록은 순서 없는 집합, 설명문은 정확 문자열로 비교한다. 의미상 동의어 판정은 하지 않는다.
같은 인용에 fact 표기가 다르면 인용 구조 차이로 남는다. 이를 라벨 오답으로 자동 분류하지 않는다.

### 독립성 신고와 두 통계

- `agreement_all_completed`: 완료된 모든 쌍. 동일인/노출/AI 보조 신고가 있어도 원 집계에 남긴다.
- `agreement_declared_clean_only`: 양쪽 human_declared, 사전답안 미열람 신고, 다른 reviewer_ref인 완료 쌍.
- 원 집계와 제외 사유를 함께 보여 선정 효과를 숨기지 않는다.
- 이는 **자기신고상 조건**이다. 다른 문자열 계정이 다른 사람임을 증명하지 않는다.
  계정 별칭/사람 동일성/로그인/실제 노출 이력은 확인하지 않는다.

정책 불일치·추출 공백이 있어도 위 일치율은 같을 수 있다. 해당 경고는 별도로 계속 남는다.
예를 들어 두 검수자가 같은 낮은 등급을 골랐어도 정책 계산과 다르면 일치율1과 경고가 공존한다.
일치율을 고객 정확도, 재현율, 심각 오분류율 또는 원래 GOLD 대비 성능으로 발표하지 않는다.

## 4. 쟁점과 원인 구별

기계가 검출하는 `issue_codes`:

- `difference:<field>`: 위 제출 필드 차이.
- `left:<R2 finding>` / `right:<R2 finding>`: 정책·근거·추출·노출/AI 신고 경고.
- `same_reviewer_claim`: 양쪽의 동일 신원 문자열 신고.

기계는 이것을 원인으로 단정하지 않는다. 조정자가 별도 `cause_codes`를 제시한다.
종류는 extraction_gap, management_evidence_gap, mapping_dispute, policy_gap, policy_conflict,
rule_interpretation, label_error, citation_difference, explanation_difference, independence_issue,
agreement_check, **undetermined**다. 원인을 모르는데 라벨 오류로 고르도록 강제하지 않는다.
모든 원인 코드는 주장으로 기록하며 root_causes_verified=false다.

## 5. 빈 조정 양식과 제출된 조정 제안

빈 양식은 `AdjudicationBatch` (`real-document-review-adjudication-v2-draft`)다.
pair binding과 행별 판본 해시만 채우고, decision/조정자/시각/원인/서명은 생성하지 않는다.
제시자료 원문·원 제출 답안도 복사하지 않는다. 실제/공개 문서의 양식 출력은 허가 provided 주장이 필요하고,
denied면 합성도 출력하지 않는다. 진단용 검사 가능 여부와 실제 문서 배포 권한은 구별한다.

조정 배치에는 비교한 모든 사례를 한 번씩 넣는다. 미완료는 pending 행으로 둔다.

- pending: 조정자/actor_kind/proposed_at/reviewed_both/resolution_note/decision은 null,
  cause_codes/issue_responses는 빈 목록이다.
- proposed: 양쪽 검수 제출이 완료돼야 한다. 두 제출을 읽었다는 명시적 true 신고, 조정자,
  시각, 설명, 원인 코드 ≥1, 새 ReviewDecision을 받는다.
- 조정자 문자열은 양쪽 검수자와 달라야 하며, 한 배치에서 하나의 조정자 주장을 사용한다.
- proposed_at은 두 submitted_at 이후, 검증 context.as_of 이전이어야 한다.
- 각 issue_code에 중복/누락 없이 action과 이유를 적는다.
- action: retain_for_review / request_new_input / request_policy_change / interpretation_proposed.
  새로운 입력/정책 변경을 요청했으면 현재 판본에 candidate 등급을 제안할 수 없다. 보류와 요청을 남긴다.
- decision의 규칙·등급·근거 검사는 R2와 동일하다. 제시되지 않은 새 증거를 조정 단계에서 몰래 추가하지 않는다.
  새 근거나 정책 정정은 새 manifest/배정/제출을 요구한다.

조정자가 기존과 다른 판단을 제안할 수는 있지만 기계가 다수결·상위 등급 선택으로 채워주지는 않는다.
제안 이후에도 원래 issue_codes, 자기신고상 제외 사유, 원 일치 통계는 불변이다.
제안 자체의 정책 불일치는 proposal_findings로 추가한다. 해석을 제안했다고 쟁점이 해결됐다고 표시하지 않는다.

## 6. 출력·보안·권위

보고서는 코디네이터/조정자용이다. 독립 제출 완료 전에 검수자에게 노출하지 않는다.
본문·근거값·검수자/조정자 ID·자유서술 이유·개별 등급은 기본 보고서에 복사하지 않는다.
다만 사례ID/지문/일치 통계/고정 쟁점·원인 코드도 민감할 수 있어 익명화나 블라인드 ACL을 보장하지 않는다.

항상 final_grade=null, gold_eligible/training_allowed/model_evaluation_allowed/automation_allowed/finalized=false.
신원/허가/정책 승인/독립성 인증=false, quality_accuracy=null, adjudication_performed=false다.
`adjudication_proposals_checked=true`는 제안 형식 검사만 뜻하며 공식 조정/DB 저장/정답 확정이 아니다.

파일 SHA는 판본·재현 보조일 뿐 서명/인증/진위 증명이 아니다.
CLI는 중복 JSON 키·비정상 JSON 상수·기존 출력 덮어쓰기·입출력 경로 겹침·읽은 입력 변경을 거절한다.
파일 잠금/동시 트랜잭션/인증 저장소는 없다. 출력 디렉터리 보안은 운영자가 별도 설정해야 한다.
현재 로컬 입력과 R2를 여러 차례 재검증하므로 대량 문서 성능/메모리 한도는 측정하지 않았다.

## 7. 실행과 다음 단계

`F:\antigravity\rag\poc`에서:

```powershell
.\.venv\Scripts\python.exe -B scripts/compare_document_reviews.py --demo
.\.venv\Scripts\python.exe -B scripts/compare_document_reviews.py --schema
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_review_pair_v2.py -q
```

아래 입력은 운영자가 준비할 파일명 예시이며 이번 작업에서 실제 자료/검수자를 만들지 않았다.

```powershell
.\.venv\Scripts\python.exe -B scripts/compare_document_reviews.py --manifest input.json --context trusted-context.json --left supplied-A.json --right supplied-B.json --out new-pair-check.json --template-out new-adjudication-blank.json
.\.venv\Scripts\python.exe -B scripts/compare_document_reviews.py --manifest input.json --context trusted-context.json --left supplied-A.json --right supplied-B.json --adjudications supplied-proposals.json --out new-proposal-check.json
```

종료0=demo/schema, 종료3=검토 전용 정상 검사, 종료2=입출력/계약 오류. 종료코드로 승인하지 않는다.
JSON Schema만으로 해시·범위·근거·시간·쟁점 응답 결합 검사는 안 되므로 validator를 실행해야 한다.

R1~R3 도구가 준비됐다고 R4 실제 파일럿을 완료한 것은 아니다. 승인 정책/사용 허가/검수 배정과
실제 문서판본·근거·공급18항목을 받은 뒤 기존 절차에 따라 소수 문서로 점검한다.
자료 없이 진행 가능한 남은 기술 작업은 [추출 판본 receipt 연결부](EXTRACTION_LINEAGE_BRIDGE_V1_DESIGN.md)다.
R5 신원·조직 인증 및 기존 확정 경로 연결, GOLD/학습·운영 전환은 별도 결정과 검증이 필요하다.
