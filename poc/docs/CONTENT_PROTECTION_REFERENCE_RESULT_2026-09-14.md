# 내용 기반 보호등급 참조 후보 — 첫 묶음 완료

작업 기준일 2026-09-14, 제작·검증 완료 2026-09-15 00:13 KST 이후.
자정을 넘겨 완료했으며 이전 기록을 같은 작업 기록에 이어 쓴다.

## 결과

**미승인 내부 기준과 답안 후보 140건을 실제 파일로 작성하고 분할·구조검증을 완료했다.**
원래 목표대로 고객사의 관리등급을 추측하지 않으며 S·V·M 숫자를 필수 입력으로 요구하지 않는다.

- [기준 완성 검토안](CONTENT_PROTECTION_REFERENCE_V1.md)
- [후보집 색인](../reports/CONTENT_REFERENCE_20260914/reference_v1/README.md)
- [개발용 답안 90건](../reports/CONTENT_REFERENCE_20260914/reference_v1/development/ANSWERS_CANDIDATE.md)
- [봉인 후보 답안 50건](../reports/CONTENT_REFERENCE_20260914/reference_v1/sealed_candidate/ANSWERS_CANDIDATE.md)
- [기존 라벨 충돌 4건](../reports/CONTENT_REFERENCE_20260914/reference_v1/LEGACY_LABEL_ISSUES.md)
- [워크스페이스 작업 기록](memory/2026-09-14-classification.md)

| 분할 | TS | S1 | S2 | S3 | 추가 증거 필요 | 맥락 충돌 | 합계 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 개발용 | 20 | 20 | 20 | 20 | 5 | 5 | 90 |
| 봉인 후보 | 10 | 10 | 10 | 10 | 5 | 5 | 50 |
| 전체 | 30 | 30 | 30 | 30 | 10 | 10 | 140 |

30개 등급 계열(각4건), 10개 검토 계열(각2건). 각 계열의 변형은 같은 분할에 있다.
입력집·JSONL과 답안 sidecar를 분리했다. 답안에는 정책/본문 해시, 적용 규칙, 명시된 조건,
정확한 본문 근거 위치·인용, 상·하위 배제 이유와 추가 확인 내용을 기록했다.
AI 작성·미승인·사람 서명 없음·학습 제외 상태도 모든 답안에 기록했다.

## 검증한 것

1. 140개 ID·고유 본문, 정책 버전과 파일 SHA-256, 근거 span140개, 규칙·조건 일관성 통과.
2. 개발/봉인후보 간 본문 중복0, 계열ID 중복0. 공백 정규화 중복까지 검사했다.
3. 기존 알려진 풀4개(총3,164행)와 본문 중복0. 네 파일 모두 계열ID가 없어
   **의미상 계열 중복 검증은 미완료**이며 전체 학습·사전학습 계보를 검증한 것은 아니다.
4. 신규42개를 포함한 관련 테스트252개 통과, 실패0, 건너뜀0. 기존196개·SVM14개와 합쳐 재실행한 수다.
5. 새 스크립트·테스트 정적 검사 통과, `git diff --check` 오류 없음(기존 파일 줄바꿈 경고는 있음).
6. 초기 스냅샷 원본JSONL406개와 기존소스12개 해시 불변. 앞선 실험의 모델·입력·소스48개 해시 불변.
7. 기존 교정20건은 별도로 유지(채운 등급0, 서명0), 기존64조합 표도 승인0 상태 유지.

검증 파일:

- [파일 재검증](../reports/CONTENT_REFERENCE_20260914/recheck.json)
- [원본·모델·소스 보존 검사](../reports/CONTENT_REFERENCE_20260914/preservation.json)
- [관련 테스트 결과](../reports/CONTENT_REFERENCE_20260914/tests.xml)
- [후보 파일·생성 소스 지문](../reports/CONTENT_REFERENCE_20260914/reference_v1/manifest.json)

## 평가·비교 학습 준비

`scripts/validate_content_reference.py`가 기존 `measure_four_metrics.compute`를 재사용한다.
등급120건과 검토 라우팅140건을 분리하며 S1→S2/S2→S1, S2→S3, TS/S1→S3, S3 과분류,
검토 포착·누락·불필요 검토와 검수 비율을 각각 낸다. 검수로 보내도 등급 오답은 그대로 센다.
등급 미추천(null)은 전체 등급 정확도에서 오답으로 남기며 행렬의 별도 분모·미추천 수도 표시한다.
정확한 문서ID·본문·정책 결합이 없거나 일부 결과가 빠지면 계산 전에 오류로 막는다.

[비교 학습 계약](../reports/CONTENT_REFERENCE_20260914/reference_v1/comparison_training_contract.json)은
동일 문서·초기모델·분할·학습 설정에서 **라벨만 바꾸는 A/B**를 지정한다.
정제 라벨이 미확정이면 해당ID는 양쪽 모두 제외한다. 새 문서 추가는 별도 실험으로 분리한다.
기존 학습기 입력 `text+label`은 바꾸지 않고 근거·변경이력은 별도 sidecar로 결합한다.
현재 계약은 `PREPARED_NOT_EXECUTED`, `training_enabled=false`다.

## 검증하지 않은 것과 다음 순서

이 후보는 **짧은 가상 상황 카드**다. 실제 1,000행 원장·제품 전체 코드·고객사 원문이 아니다.
동일 AI 작성자의 조건과 답안을 검산한 것이므로 독립 전문가 판단이나 실문서 분류 정확도를 보장하지 않는다.
봉인 후보라는 이름만으로 실제 접근 통제·블라인드 평가가 완료되는 것도 아니다.
TS의 1,000명 경계는 법정 기준이 아니라 명시적으로 검토받을 내부 제안이다.

다음 단계:

1. 기준 승인 책임자가 TS의 완전 재현·유효 통제·1,000명 경계 및 S1/S2 예외를 검토한다.
2. 검수자가 입력집과 기준으로 독립 판단하고 후보 답안과의 불일치·근거를 기록한다.
3. 미해결은 HOLD로 두고 승인한 정책·답안만 버전 고정한다. 평가셋 봉인 절차를 별도 수행한다.
4. 같은 기존 문서에 대한 라벨 A/B를 준비한 뒤 승인된 범위에서 비교 학습한다.

아직 수행하지 않음: 기준 승인, 사람 서명, GOLD 확정, 새 모델 추론/품질측정, 정제 재학습,
운영 API·분류·검수 변경, 모델 교체. 사용자 홈의 전역 메모리는 수정하지 않았다.
생성 결과는 워크스페이스의 reports에 저장되어 있으며 Git ignore 대상이다.
원고·생성기·기준 문서로 재생성할 수 있고, 커밋·외부 백업은 이번 작업에 포함하지 않았다.

## 재현

`F:\antigravity\rag\poc`에서 실행한다. 기존 결과 경로는 덮어쓰지 않고 새 경로를 사용한다.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:HF_HUB_OFFLINE='1'
./.venv/Scripts/python.exe -B scripts/prepare_content_reference.py --out reports/CONTENT_REFERENCE_RECHECK/reference_v1
./.venv/Scripts/python.exe -B scripts/validate_content_reference.py --pack reports/CONTENT_REFERENCE_RECHECK/reference_v1 --out reports/CONTENT_REFERENCE_RECHECK/recheck.json
./.venv/Scripts/python.exe -B scripts/verify_content_reference_workspace.py --before reports/CONTENT_REFERENCE_20260914/baseline_before.json --out reports/CONTENT_REFERENCE_RECHECK/preservation.json
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_content_reference.py
```

위 검증·생성 명령은 모델을 로드하거나 DB/API에 연결하지 않는다.
후보 본문·분할·답안은 결정적으로 재생성된다. manifest의 생성 시각은 실행마다 다르다.
