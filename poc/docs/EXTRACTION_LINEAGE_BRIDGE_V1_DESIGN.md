# 실제 추출 결과 → 근거 스냅샷 연결 상세 설계 v1

상태: **설계 완료안, 연결 구현/실행 미완료**. 운영 ingestion/DB/schema/추출기를 수정하지 않았다.
목표는 기존 `ExtractResult`를 새 계약에 연결하면서 누락된 판본/완전성을 창작하지 않는 것이다.

## 1. 확인한 현재 경로

`DocumentIngestionService.ingest()`는 수신 content_bytes의 SHA-256을 계산하고 원본을 저장한다.
`PreprocessPipeline._finalize()`는 `ext.text`를 정규화하고 설정에 따라 PII 마스킹해 `pre.text`로 만든다.
저장된 normalized.txt와 Document.text_preview는 이 후처리된 본문 계열이다.
따라서 **ext.text 해시·pre.text 해시·원본 바이너리 해시를 같은 값으로 취급하면 안 된다.**

또한 현재 경로는 total_pages 미수신 시 pages 값으로 채우고, 일부 truncated 경고/페이지 비교로
extraction_complete를 계산한다. 이 값은 원본 전체/표/첨부 검증의 증거가 아니다.
같은 file_hash의 재업로드는 기존 doc_id를 재사용하는 경로이므로 새로운 ACL/추출 판본이
이전 값에 자동 덮어써졌다고 가정하지 않는다.

근거: `src/koipa/services/document_ingestion_service.py:210,238,479`,
`src/koipa/modules/m2_preprocess/pipeline.py:126`, `src/koipa/db/models.py:138`.

## 2. 제안 인터페이스와 책임

후속 새 연결부의 제안 인터페이스(아직 Python 함수로 구현하지 않음):

`build_extraction_snapshot(extraction_result, document_receipt, extraction_receipt, coverage_receipt)`

| 입력 | 책임/필수 정보 | 없는 경우 |
|---|---|---|
| document_receipt | 원천 문서 ID·판본, 원본 실측 해시, 저장 객체 판본, 조직 결합 확인 결과 | 입력 보류; 파일명/시각으로 판본 생성 금지 |
| extraction_result | 정확한 ext.text, pages/total_pages, table_coverage, warnings, error, method/quality/ocr | 미확인 신호 그대로 전달 |
| extraction_receipt | 추출 작업 ID, 실행 시각, 추출기 빌드/설정 지문, 원본 receipt 해시 | 재현 가능한 추출 판본으로 수락하지 않음 |
| coverage_receipt | 회수 범위와 검증 방법, 원본/회수 페이지·표·첨부 식별자, 검증 참조/지문 | complete를 만들지 않고 unknown |

함수는 원본 바이너리를 임의 URI에서 가져오지 않는다. 허가된 수집 계층에서 실제 바이트를 확인해
receipt를 만든다. receipt 자체의 해시는 실제 수신·인증이 완료됐다는 증거가 아니므로
수집 시스템 신원/권한 확인은 별도의 신뢰 경계에 둔다.

## 3. 출력 연결

1. 정확한 ext.text에서 document_sha256을 계산한다.
2. DocumentBinding 다섯 값(org/document/revision/original hash/text hash)을 만든다.
3. ExtractionSnapshot에 추출 원시 신호를 넣되 total_pages를 pages로 대체하지 않는다.
4. 정규화/마스킹 본문은 별도 view로 기록하고 ext.text 기반 인용 위치와 혼용하지 않는다.
5. 필요하면 변환 위치 대응표를 별도 구축한다. 그 전에는 서로 다른 본문의 offset을 재사용하지 않는다.
6. 기존 `collect_evidence()`에 전달하고 held_extraction이면 패킷 출력 없이 수집 진단만 보관한다.

부가 계보에는 original/extracted/normalized/masked의 각 지문, 변환 함수/설정 지문,
parent receipt 지문을 남긴다. 기존 FactPacket의 document_sha256 의미는 바꾸지 않는다.
새 계보를 원본 자료와 함께 보호된 별도 저장물로 두는 설계부터 시작하고 DB migration은 별도 검토한다.
실제 구현 시 기존 EvidenceSource의 추출 기록 payload와 바인딩 가능한 범위를 확정해야 한다.

## 4. 완전성 판정표

| 상황 | 새 계약 처리 |
|---|---|
| 원본 범위 확인과 회수 범위 일치, 누락/오류 없음 | 제공자가 complete라고 주장할 수 있음; 독립 진위 확인은 별도 |
| total_pages 미수신 | 페이지형 문서는 unknown, 회수 수로 전체 수를 채우지 않음 |
| 비페이지 문서 | 페이지 두 값 없음 가능; 표/첨부/본문 범위는 별도 확인 |
| table_coverage=None | unknown, not_applicable/complete로 자동 변환하지 않음 |
| 원본에 표 없음이 확인됨 | not_applicable 가능; 추측만으로 설정하지 않음 |
| quality가 높지만 경고/누락 있음 | 보류 유지 |
| 마스킹으로 정보가 사라짐 | 별도 입력 view와 검토 필요 상태; 원본 기준 정답을 숨겨 채점하지 않음 |
| 기존 extraction_complete=true만 있음 | 기존 파생 신호로 기록; 새 완전성 증거 부족 |

현재 어댑터는 모든 warning을 보류한다. 무해한 경고만 제외하는 개선은 실제 경고 분포와
안전 영향 검토 후 별도 승인한다. 이번에 임계값/기존 검수 라우팅은 변경하지 않았다.

## 5. 판본·재실행·동시성

- 같은 문서를 재추출하면 새 extraction_id와 receipt를 만들고 기존 이력을 유지한다.
- 내용 판본과 ACL 관측 판본/시각을 분리한다. 파일 바이트가 같아도 권한은 바뀔 수 있다.
- 새 결과는 독립 문맥의 문서 판본/해시와 일치해야 한다. 이전 결과를 최신으로 덮어 읽지 않는다.
- dedup으로 doc_id가 재사용되더라도 수집/추출 receipt는 관측 실행별로 보존한다.
- 저장 실패/부분 저장/재시도는 별도 상태로 남기고 유효 패킷으로 게시하지 않는다.
- 캐시 키는 조직·문서 판본·추출/입력 view·정책·매핑 판본을 포함하도록 후속 설계한다.

## 6. 구현 완료 시 필요한 시험

아래는 **후속 시험 계획**이며 이번 회귀 통과 수에 포함하지 않는다.

1. 원본 해시와 텍스트 해시 뒤바뀜, 다른 판본/조직/원본에 ext 재사용 거절.
2. 같은 bytes 재업로드와 다른 ACL/추출기 설정의 별도 관측 이력.
3. total_pages 없음, 일부 페이지/표/첨부 누락, OCR 오류, 경고 보존.
4. ext.text/pre.text/마스킹 text의 인용 offset 교차 재사용 거절.
5. 저장 실패·중간 변경·동시 재추출 시 불완전 패킷 게시 금지.
6. 조직 신뢰 경계 미확인 시 운영 연결 금지, 기존 모델/등급/검수 상태 무변경.

필요한 외부 확인: 실제 문서 판본/저장소 버전, 허가된 원문 범위, 공급자/조직 인증 방식.
없으면 가상 receipt로 연결부 단위 시험은 가능하지만 실제 근거 수집 완료로 계산하지 않는다.
