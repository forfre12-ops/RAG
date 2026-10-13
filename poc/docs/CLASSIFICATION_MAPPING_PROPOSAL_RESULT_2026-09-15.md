# 공급 계약·매핑 근거·후속 연결 설계 결과 — 2026-09-15

**첫 실행 묶음의 코드 대조·매핑 검토기 구현·추출/실제 검수 확장 설계를 완료했다.**
실제 원천 시스템 접속, 정책 승인, 관측 사실 승격, GOLD 제작, 운영 경로 연결은 하지 않았다.

## 1. 완료 범위와 미완료 경계

| 요청 묶음 | 이번 완료 | 아직 하지 않은 일 |
|---|---|---|
| 공급 항목·조직 경계 | 코드 기반18항목 대조, 권위별 입력 구분, 배포 대안/외부 확인 목록 | 실제 공급 여부·KL 라우팅/조직 격리 검증 |
| 버전형 고객사 매핑 | 패키지/원시 스냅샷/독립 문맥 계약, 근거 결합, 검토 후보, 재검산 CLI·테스트 | 승인 진위 인증, FactPacket/운영 관리정보로 내보내기 |
| 실제 추출 결과 연결 | 원본·추출·정규화 view, receipt, 완전성/재추출·저장/회귀 상세 설계 | 실제 exporter/저장 receipt/DB 연결 구현 |
| 실제 문서 검수 확장 | 고정 합성40건과 분리한 정책 주입형 입력/제출/비교/확정 설계 | v2 validator/CLI·실제 검수·인증/확정 통합 |

문서:

- [공급18항목·조직 경계 대조](EVIDENCE_SUPPLY_AND_ORG_BOUNDARY_V1_DRAFT.md)
- [고객사 매핑·변환 근거 계약](CUSTOMER_MAPPING_PROPOSAL_V1_DRAFT.md)
- [실제 추출 판본 연결 설계](EXTRACTION_LINEAGE_BRIDGE_V1_DESIGN.md)
- [실제 문서 검수 v2 설계](REAL_DOCUMENT_REVIEW_V2_DESIGN.md)

## 2. 현재 코드에서 확인한 주의점

1. JWT/Actor/Document 경로는 단일 고객사 엔진·KL 상위 격리 전제다. 새 계약의 org_id 문자열을
   넣는 것만으로 운영 조직 인증/격리가 완료되지는 않는다.
2. 표식/접근범위는 요청값 우선·DB 보관값 보충 경로다. 실제 ACL의 인증된 관측과 구별해야 한다.
3. 기존 ingestion은 total_pages가 없으면 pages로 채우고 일부 신호로 extraction_complete를 만든다.
   이를 새 계약의 독립적인 원본 전체 회수 증명으로 재사용하지 않는다.
4. ext.text와 정규화/마스킹된 pre.text의 해시·인용 위치를 혼용할 수 없다.
5. 같은 file_hash의 재업로드에서 기존 doc_id를 재사용하는 경로가 있다. ACL 변경/재추출 판본 보존을
   별도 설계해야 한다. 이번에 dedup이나 DB를 수정하지 않았다.
6. R1/R2 비교 검수는 품질 실험 제안이다. 현재 golden_signoff의 단일 서명 규칙을 사업상 이중서명
   요구로 바꾸지 않는다. 검수 실험과 공식 확정 경로를 분리했다.

이는 현재 소스 대조 결과이며 실제 운영 자료/상위 포털/배포 상태 전수 검증이 아니다.

## 3. 매핑 검토기 구현

새 코드: `src/koipa/mapping_proposal.py`, `scripts/check_customer_mapping.py`.
지원하는 것은 표식/접근범위의 **용어 변환 후보**이며 TS/S1/S2/S3 등급을 계산하지 않는다.

- 조직·문서·원본/추출 판본과 매핑 ID·판본·정규화 해시를 독립 문맥과 대조.
- 원시 값은 raw snapshot의 JSON Pointer/해시에 결합.
- 변환 규칙은 원시 값+canonical 값 양쪽이 포함된 근거 행의 Pointer/해시에 결합.
- 공백/대소문자/유사어 자동 추론 없음. unknown/부재/unmapped/충돌/기간 밖을 구별.
- 다른 조직, 중복 ID, 잘못된 어휘·근거·기간·바인딩은 거절.
- 패키지 선언 상태 approved도 미인증 주장이다. 사실 내보내기/자동화/학습은 항상 false.
- 민감 preview는 명시적 새 파일로만 저장. 기본 보고서는 원시 값/canonical 값/출처 URI 미포함.
- 저장 preview를 재실행해 전체 대조. 변환 값/권한 플래그/근거 변경을 탐지하지만 전자서명은 아님.

기존 FactPacket은 JSON 근거와 주장 값의 동일성을 검사한다. 변환 값을 원시 관측으로 위장하지
않기 위해 이번 preview는 별도 스키마다. 인증된 매핑 변환 receipt를 실제 사실 계약에 연결하는
작업은 후속이며, 미완료를 approved 플래그나 승인 문서 참조만으로 우회하지 않는다.

## 4. 검증 결과

- 새 전용 회귀 **96 passed**.
- 확대 회귀 **762 passed**, 실패/오류/skip0. 이전666개+신규96개이며 누적 합산하지 않는다.
- DB/torch/transformers import를 차단한 독립 데이터 품질 회귀 **404 passed**. 위762개에 포함된다.
- 새 Python3개 Ruff 검사 통과. Git diff 공백 검사 통과.
- 가상 데모6건: 정확 매핑, 미등록 용어, 명시적 부재, 규칙 충돌, 미래 시행, 승인 선언.
- 실제 고객 문서0, 실제 승인0, 신규 GOLD0, 모델 학습/고객 정확도 측정 없음.

후속 추출 연결/v2 검수 문서의 시험 목록은 **계획**이다. 이번 통과 수에 해당 구현 검증을 포함하지 않는다.
원격 CI·전체 저장소·실제 조직 격리·운영 부하/인증/저장 통합 시험도 아니다.

## 5. 보존과 기록

이번 시작점의 운영/근거/기존 매핑·검수/미결정 원장 등17개 지문을 별도 대조했다.
기존 운영 코드·기준·모델·원본 라벨을 바꾸지 않았다. 과거 policy_engine.py 수정은 그대로다.
9/14 기준 보존 검사는 원본 JSONL406개 불변을 확인하고, 앞선 policy_engine.py 변경은 계속
`CHANGED_REQUIRES_REVIEW`로 남긴다. 기준선을 덮어쓰지 않았다.
참조팩140건/v1.1 40건은 manifest 대상13/91개 파일 지문을 대조한다.
D01~D08은 모두 미정이고 준비도 검사는 HOLD/종료2다.

결과: `poc/reports/CLASSIFICATION_MAPPING_PROPOSAL_20260915/` (Git ignore 대상)

- tests.xml / isolated-tests.xml: 확대/독립 회귀 결과.
- demo.json / mapping.schema.json: 가상 데모와 입력 구조.
- workspace_preservation.json: 과거 기준 대조.
- evidence.json: 소스/산출물/보존 지문, 실제 미실행 범위.

워크스페이스 MEMORY와 인계에 추가했다. 사용자 홈 전역 메모리·커밋·푸시·운영 설정 변경 없음.

## 6. 재현

`F:\antigravity\rag\poc`에서:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B scripts/check_customer_mapping.py --demo
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_mapping_proposal.py -q
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality -q
```

확대 회귀는 [근거 비교 계산 결과의 명령](CLASSIFICATION_FACTS_SHADOW_RESULT_2026-09-15.md#파일과-재현)과
동일한 대상이다. 새96개는 tests/data_quality에 포함된다. CLI 실제 입력은 검토 필요 종료3이며,
데모/스키마/재검산 종료0을 승인이나 고객 정확도 성공으로 해석하지 않는다.

## 7. 다음 구현과 외부 결정

바로 가능한 다음 기술 묶음은 **실제 문서 검수 v2의 정책 주입형 manifest/제출 validator**다.
기존 합성40건을 변경하지 않고 두 가상 정책으로 계약을 시험한 뒤 실제 허가 문서를 받는다.
추출 receipt 연결부도 명세에 따라 단위 구현 가능하지만 실제 원본/저장소/조직 검증은 별도다.

실제 자료로 넘어가려면 공급18항목에 대한 담당자 회신, 배포 조직 경계, 문서 사용 허가,
고객사 매핑 원본/승인, D01~D08 결정이 필요하다. 사용자의 ‘진행’을 이것들의 승인으로 기록하지 않는다.
새 합성 정답 대량 제작·재학습·모델 교체는 자동 재개하지 않는다.
