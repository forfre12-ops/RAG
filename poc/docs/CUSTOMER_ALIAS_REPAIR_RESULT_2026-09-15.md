# 병렬 별칭 진단·이력 보강 결과

2026-09-15. 기존 정답 후보를 늘리지 않고 같은 batch06 64개 부모의 작성 편향을 조사했다.

## 결론과 적용 범위

영문 별칭 삭제만으로 안전한 원고 교정이 된다는 가설은 채택하지 않는다.
‘이번 결과는 기질 배치 M에 한정’에서 M을 지우면 적용 대상 식별이 덜 명확해진다는 독립 검토 결과 때문이다.
원본260개가 계속 정본이며 삭제본은 **진단 전용·미채택**이다. 숫자/정책 검산 통과를 의미 보존 인증으로 대체하지 않았다.

이번 산출물은 재현 가능한 제한 변환기, 별도 검산기, 네 가지 짝비교, 수정 이력·노출 전파 장치다.
문서 품질 전반이나 고객사 분류 정확도의 합격 결과가 아니다. 운영 API·학습기·정책·모델은 바꾸지 않았다.

## 병렬 작업 결과

| 작업 | 실제 범위 | 확인한 경계 |
| --- | --- | --- |
| A 원고 변환 | 64개 조사, 16개/18구간 변경, 48개 동일 | 등급을 변환 인자로 사용하지 않음; 적용 대상 의미 동등성 미인증 |
| B 독립 검산·짝비교 | 다른 파서로 변환/인용/수치/조건/맥락/정책 비교 | 별칭 삭제가 범위를 일반화할 위험을 발견, 미채택 처리 |
| C 이력·노출 | 원본260행 보존, 변경16판본 추가, 진단뷰260 | 원장276판본은 신규276문서가 아님; 진단 전수 최종평가 제외 |
| 중앙 통합 | 기존196 보존, 정책 검산260, 입력검사, 회귀·보존 검사 | 품질 HOLD, 채택/학습/고객정확도/사람서명 플래그 해제 없음 |

원본 등급 분포 TS54/S1 72/S2 65/S3 69, 선언 그룹236, 의미 연결11개를 유지했다.
본문 근거520개, 가상 맥락 근거3,640개, 산술 검산207개다. 본문+맥락260개는 기술적 입력 준비에 한해 통과하며
본문 단독260개는 계속 HOLD다. 입력 길이 검사는 대상 의미 동등성이나 원고 품질 승인을 뜻하지 않는다.

## 실제 짝비교 측정

동결된 같은260부모/236그룹, 5seed×5fold에서 문자 TF-IDF+LinearSVC 진단기를100회 적합했다.
TF-IDF는 학습 fold 안에서만 학습했다. 여기서 LinearSVC는 진단 알고리즘이며 보안 판단요소 S·V·M과는 별개다.
실제 운영 체크포인트의 학습·forward는0회다. 변환본의 대상 의미 동등성이 미인증이므로 아래는
**원본 조건부 참조 답에 대한 진단 일치율**이며 새 정답의 정확도나 고객사 정확도가 아니다.

| 진단 입력 | 적합 자료 | held-out 원문 | held-out 삭제뷰 |
| --- | --- | ---: | ---: |
| 본문 | 원문 | 63.15% | 62.77% |
| 본문 | 삭제뷰 | 62.69% | 62.54% |
| 본문+가상 맥락 | 원문 | 76.54% | 76.38% |
| 본문+가상 맥락 | 삭제뷰 | 76.54% | 76.38% |

각 칸 분모는260×5=1,300반복관측이다. 같은 문서의 반복을 독립1,300문서로 세지 않으며 신뢰구간은 계산하지 않았다.
실제 변경16개는 모두 TS이고, 아래 분모80=16×5다. 64개 패널 전체의 분모320과 구별한다.

| 적합 자료 / 입력 | 변경16개에서 입력 삭제에 따른 예측변경 | 참조 일치→불일치 | 불일치→일치 |
| --- | ---: | ---: | ---: |
| 원문 / 본문 | 6/80 (7.50%) | 5 | 0 |
| 삭제뷰 / 본문 | 5/80 (6.25%) | 3 | 1 |
| 원문 / 본문+맥락 | 2/80 (2.50%) | 2 | 0 |
| 삭제뷰 / 본문+맥락 | 2/80 (2.50%) | 2 | 0 |

64개 패널 전체로 보면 각각6/320,5/320,2/320,2/320이다. 나머지48개와 기존196개는 같은 적합 모형에서
입력 자체가 같아 입력뷰 변경에 따른 예측차0이었다. 적합 자료를 바꾸면 다른 문서에도 영향이 있으므로 별도로 측정했다.
전체260개에서 적합 자료만 바꾼 예측차는 본문 원문입력10/1300·삭제입력9/1300,
결합입력은 각각4/1300이다. 모든 등급 혼동표와 대조군을 JSON에 기록했다.

결론: 이 진단기에서는 해당 별칭 삭제의 영향이 제한적이며, 삭제만으로 남은 작성 편향이 해소됐다는 근거가 없다.
저하된 일치율을 데이터 품질 개선으로 해석하지 않는다. 특히 대상 식별 손실 위험은 작은 예측차로 정당화되지 않는다.

본문 표식 검사에서 기존 좁은 패턴17→0, 넓은 대문자 단문자19→1이다.
18개 대상 별칭은 제거됐고 남은1개는 단위 `μL`의 L이다. 단위/약어를 무조건 삭제하지 않았다.
이 수치는 본문에 대한 검사이며 가상 맥락까지 모든 단서가 사라졌다고 주장하지 않는다.

결과: `poc/reports/CUSTOMER_GUIDE_REPAIR_20260915/independent/diagnostic_v1.json`와
그 옆 `diagnostic_v1.json.manifest.json`을 함께 확인한다. 완료 표식이 없는 JSON은 성공 산출물로 보지 않는다.

## 정본과 진단 이력

- 원본: `poc/reports/CUSTOMER_GUIDE_PARALLEL02_20260915/reference_v0_6/`.
- 진단뷰: `poc/reports/CUSTOMER_GUIDE_REPAIR_20260915/reference_repair_v1/`.
  manifest `8f5fd54580c49e285702b38ada8aa0c2c2c6774797981ff35ff880ba5a9983c0`, 278 payload.
- 통합: 같은 상위 폴더 `integration_v1/`, manifest
  `33a23af4970e2ff2d2f4a6c10d51b42fb3c083f81cc3fcdfc89bc94e0167aa64`, 8 payload.
- 후속 작업용 노출 원장: `integration_v1/exposure/development_versions276.json`, 내부 지문
  `ec4e33107d387e04244d1cdb1bf4c952d1abc19e8a4440dd2001c918710d438f`.
- C 별도 실행: `lineage/actual_v1/`, manifest
  `1f713934bc83b484708bf6e96144a50db21611fbbe5b690e699197dd9249599a`, 2 payload.
  같은16판본의 출처 경로를 절대경로로 기록하므로 원장 지문은 중앙 원장과 다르다. 두 원장을 합치지 않는다.
- A 자체 재현본 `authoring/diagnostic_views_v1/`은 중앙 진단팩과 같은 manifest이며 신규 문서로 중복 집계하지 않는다.

`adoption_allowed=false`, `authoritative_parent_replaced=false`, `target_binding_not_certified=true`.
‘active260’은 제안된 진단뷰의 집합을 뜻하며 정본 대체·학습 채택260건을 뜻하지 않는다.

## 회귀·보존 확인

- 신규293개: A162/B67/C64. 관련 통합 **2,807개 통과**(288.59초).
- torch/transformers/sqlalchemy/psycopg 의존성을 차단한 데이터 품질 검사 **2,449개 통과**(256.67초).
  후자는 앞의 부분집합이며 합산하지 않는다. 테스트는 소프트웨어 계약 검증이고 원고 품질 합격 건수가 아니다.
- 이번 보호119파일과 이전23팩/1,426payload 지문 확인. 새 진단팩/통합팩/별도 이력팩도 저장 후 검증했다.
- 원본 JSONL406개 변경/삭제/추가0. 9월14일 기준 보존 검사에는 이전 작업의 `policy_engine.py` 변경이
  계속 남아 `CHANGED_REQUIRES_REVIEW`이며 전체 GREEN으로 기록하지 않았다. 이번에 해당 파일은 수정하지 않았다.
- 새 외부 학습 풀 전수 재검사는 미수행이다. 이전 검사도 미지원/파싱 실패 행으로 완전 커버리지가 아니었다.
  이번 결과로 외부 풀 중복이 전혀 없다고 주장하지 않는다.
- 테스트/소스/팩/메모리 지문과 진단 재집계는 `poc/reports/CUSTOMER_GUIDE_REPAIR_20260915/evidence-final.json`에 결합한다.
  현재 HEAD는 `83c88e9f5833c61d01d36276e415be5bc24987df`, 이번 커밋/푸시는 하지 않았다.

검증 파일: `tests-final.xml`, `isolated-tests-final.xml`, `workspace_preservation_posttests.json`.
검증 실행 시점은 각 XML/JSON에 기록하며 이후 소스가 바뀌면 이전 통과를 새 소스의 결과로 재사용하지 않는다.

## 재현 명령

아래는 `F:\antigravity\rag\poc` 기준이다. 출력은 이미 존재하지 않는 새 경로를 사용해야 한다.
동결 팩 내부 출력, 잘못된 부모 지문, 입력/소스 변경, 빈 결과를 거절한다.

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_alias_repair_v1.py verify --pack reports/CUSTOMER_GUIDE_REPAIR_20260915/reference_repair_v1
.\.venv\Scripts\python.exe -B scripts/audit_customer_alias_repair_v1.py --original-pack reports/CUSTOMER_GUIDE_PARALLEL02_20260915/reference_v0_6 --repair-pack reports/CUSTOMER_GUIDE_REPAIR_20260915/reference_repair_v1 --repair-manifest-sha256 8f5fd54580c49e285702b38ada8aa0c2c2c6774797981ff35ff880ba5a9983c0 --grouping reports/CUSTOMER_GUIDE_PARALLEL02_20260915/integration_v1/exposure/audit.json --out reports/CUSTOMER_GUIDE_REPAIR_20260915/independent/diagnostic_reproduction.json --seeds 5 --run-cv-after-freeze
```

소스: `scripts/customer_guide_alias_repair_v1.py`, `scripts/build_customer_guide_alias_repair_v1.py`,
`scripts/audit_customer_alias_repair_v1.py`, `src/koipa/customer_revision_lineage_v1.py`,
`scripts/audit_customer_revision_lineage_v1.py`.
`reports/CUSTOMER_GUIDE_REPAIR_20260915/assemble.py`와 `collect_evidence.py`가 통합/보존 근거를 결합한다.
기존 동결 소스는 수정하지 않았다. 보고서 폴더는 계속 Git ignore 대상이며 외부 백업 완료를 의미하지 않는다.

## 다음 작업

1. [대상 보존 작성 명세](CUSTOMER_TARGET_PRESERVING_AUTHORING_V1.md)에 따라 대상·재언급·적용 범위·예외의 결합을 별도 근거로 기록한다.
2. 64개 각각 유지/안전한 수정후보/미해결을 판단한다. 별칭을 일괄 삭제하지 않고, 수정본을 신규 원고 수로 세지 않는다.
3. 이름·말투·형식의 네 등급 교차 배정과 대상 보존 검사를 신규 원고 생성 단계에 적용한다.
4. 내부 채택 가능한 개발 원고를 확대한 뒤 **새로운 미노출200개**를 분리 제작한다.
   현재260개 전부 채택 가능하다는 가정에서만 부족740=개발540+최종200이다. 현재 채택은0이다.
5. 사실 추출·장문·비산술 사례·모델 입력과 시험팩 채택을 검증한 다음 동일 조건의800/200 시험으로 넘어간다.

고객 정책 승인 D01~D08, 실제 관리/권한 근거와 고객사 성능 검증은 별도 미완료다.
이런 외부 미완료를 내부 원고·검산 도구 개발의 무기한 대기 조건으로 삼지는 않는다.
