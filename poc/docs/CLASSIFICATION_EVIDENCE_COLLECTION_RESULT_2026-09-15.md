# 읽기 전용 근거 수집 어댑터 결과 — 2026-09-15

**명시적으로 제공된 문서/관리 스냅샷을 기존 근거 패킷으로 변환하는 어댑터를 구현했다.**
실제 관리시스템 연결이나 문서 분류 정확도 개선 실측은 아니다.
상세 입력/보류/출력 계약: [수집 어댑터 v1 초안](POLICY_EVIDENCE_COLLECTION_V1_DRAFT.md).

## 완료한 것

- 조직·문서·문서 판본·원본 지문·추출 본문 지문을 독립 호출 문맥과 대조.
- 관리 사실5종의 observed / proven_absent / unknown 보존. 본문/등급/점수로 값 생성 금지.
- 공급 스냅샷 전체와 JSON Pointer에 주장 연결. 충돌은 마지막 값으로 덮지 않음.
- 추출 누락/오류/경고/미확인 완전성은 진단으로 남기고 **정책 계산용 패킷 내보내기 차단**.
- 기존 `FactPacket`/정책 비교 계산과 왕복 검증. 새로운 호환 불가 등급 계약은 만들지 않음.
- 기본 출력은 본문/관리 값/출처 URI 미포함. 민감 패킷 저장은 명시적 `--packet-out`만 허용.
- 가상 데모5건, JSON Schema, CLI 오류/기존 출력 보존/입력 변경 감지 포함 회귀118개.

현재 ExtractResult에는 원본/추출 판본/공급자 인증 정보가 없으므로 운영 객체를 억지로 완전한
스냅샷으로 승격하지 않았다. 이번 인터페이스에는 제공자가 별도 판본/완전성 정보를 보내야 한다.
기존 고객사 매핑의 raw→canonical 변환도 승인된 계보가 없어 자동 연결하지 않았다.

## 검증

- 확대 회귀 **666 passed**, 실패/오류/skip0. 새118개와 이전 관련548개를 포함한다.
- 독립 데이터 품질 회귀 **308 passed**. DB/torch/transformers import 차단 환경에서도 통과.
  308개는666개에 포함되며 이전 수치와 합산하지 않는다. 전체 저장소/원격 CI 검증은 아니다.
- 새 Python3개 Ruff 검사 및 diff 공백 검사 통과.
- 가상 관리정보 미수신 → 정책 비교 needs_evidence/grade=null.
- 가상 유효 관리정보·명시적 표식 부재 → 공급 값/부재를 보존하며 정책 fixture 후보 계산.
- 가상 관리 스냅샷 충돌 → needs_evidence_conflict_review/grade=null.
- 가상 3페이지 중1페이지 회수 → held_extraction, 패킷/정책 후보 없음.

데모 정책의 ACL→등급 규칙은 어댑터 기계적 시험만을 위한 가상 규칙이다.
ACL 하나로 실제 고객사 등급을 정하자는 기준이 아니다. 실제 문서0건, 신규 GOLD0건이다.
초기 CLI 회귀6건은 테스트 입력의 내부 `version`/파일 `policy_version` 혼동으로 실패했고
파일 계약에 맞게 시험 입력을 수정한 뒤 전체를 다시 통과했다. 정책 파서를 느슨하게 바꾸지 않았다.

## 보존과 미완료 경계

이번 시작점 대비 기존 정책 엔진/룰 엔진/분류 서비스/추론 파이프라인/추출기/고객사 매핑,
기존 근거 계약/비교 계산부, 미결정8개/fixture 사용 제한 레지스트리의10개 지문을 대조했다.
이번에 이 파일들을 변경하지 않았다. 운영 API 연결·모델/라벨/승인 변경도 없다.

과거9/14 보존 검사는 원본 JSONL406개 불변을 확인했다. 정책 엔진의 **앞선 턴 의도한 변경**은
계속 감지돼 `CHANGED_REQUIRES_REVIEW`/종료1이다. 과거 기준선을 덮어 녹색으로 만들지 않았다.
기존 참조140건/v1.1 40건은 manifest 대상13/91개 파일 지문으로 별도 보존 대조했다.
D01~D08은8개 모두 미정이며 준비도 검사 결과 HOLD/종료2다.

원본 바이너리 진위, 실제 공급자 인증, 추출 의미상 완전성, 최신성 기준, 정책 승인은 검증하지 않았다.
S/V/M 근거 공급 구조의 일부를 구현했을 뿐 점수 공식이나 FUN-023 전체 충족을 선언하지 않는다.
커밋·푸시·전역 사용자 메모리 변경 없음. 워크스페이스 MEMORY/인계에는 이번 결과를 추가했다.

## 재현 자료

위치: `poc/reports/CLASSIFICATION_EVIDENCE_COLLECTION_20260915/` — Git ignore 대상.

- `tests.xml`: 확대666개 회귀.
- `isolated-tests.xml`: 무거운 런타임 import 차단 독립308개.
- `demo.json`: 가상5건 진단, 실행 소스 지문 포함.
- `collection.schema.json`: 수집 입력/독립 호출 문맥 구조.
- `workspace_preservation.json`: 과거9/14 기준 보존 검사와 알려진 정책 엔진 변경.
- `evidence.json`: 이번 소스/산출물 지문, 검사 수치, 판정/보존 경계.

`F:\antigravity\rag\poc`에서 기본 재현:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B scripts/collect_policy_evidence.py --demo
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_evidence_collection.py -q
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality -q
```

확대 회귀 명령은 [이전 결과의 재현 명령](CLASSIFICATION_FACTS_SHADOW_RESULT_2026-09-15.md#파일과-재현)과
같으며 `tests/data_quality`에 이번118개가 포함된다. 새 JUnit 경로를 지정하면 기존 결과를 보존할 수 있다.

## 다음 진행

1. **실제 관리시스템의 읽기 전용 공급 계약 대조**: 담당자가 제공 가능한 문서 판본/원본 지문/
   추출 결과/표식/ACL/실제 열람 범위와 공급 시각을 확인한다. 인증정보는 문서/메모에 넣지 않는다.
2. 원시 필드·고객 용어를 canonical 값으로 옮길 필요가 있으면 **매핑 후보와 근거 계보**를 별도 설계한다.
   승인되지 않은 매핑을 관측 사실로 숨겨 넣지 않는다. 필요한 필드가 없으면 수집 공백을 보고한다.
3. 사용 허가된 소수 실제 문서에 대해 스냅샷 값/판본/누락/충돌과 담당자 확인 결과를 대조한다.
   아직 등급 정확도 분모로 쓰지 않고 근거 수집 오류와 정책 판단 오류를 분리한다.
4. D01~D08과 실제 근거 공급/정답 권한을 고정한 뒤 별도 운영 shadow 연결·독립 평가를 결정한다.

새 합성 정답 대량 제작·재학습으로 자동 재개하지 않는다. 연결 정보 없이 실제 시스템 연동을
완료했다고 기록하지 않으며, 다음 기술 보완도 미결정을 대신 승인하는 작업으로 확대하지 않는다.
