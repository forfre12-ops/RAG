# 1단계 인수 실행 — 결함 3건. 그중 하나는 KL 시험에서 그대로 터질 자리였다

작성 2026-08-14. 기존 인수 팩 10건을 운영 경로에 태운 결과다.

**이 실행의 결론은 "운영 전 리허설 통과 여부"이지 실문서 성능 보증이 아니다.**
팩 자신의 판정 규율이 그렇다 — 정확 등급일치가 아니라 severity floor(고등급 미탐 veto)
+ 숫자 무손실 + 게이트 가시성이고, 서빙은 의도적 안전 과분류라 정확도로 합격시키지 않는다.

---

## 1. 결과

```
A(메타데이터 없음)   PASS · 등급일치 9/10 · 안전과분류 1 · 검수전송 2
B(ICD 메타데이터)    PASS · 등급일치 10/10 · 안전과분류 0 · 검수전송 5

두 실행 공통   VETO 0 (파싱실패 0 · 숫자유실 0 · 고등급 미탐 0)
              MODEL loaded=True · v-fe4b386b · rule_fallback 0/10
              포맷 7종(TXT/PDF/DOCX/XLSX/XLS/PPTX/HWPX) + 구형 .hwp 전부 파싱
```

---

## 2. 결함 3건

### (1) ICD §3.1 의 source_type 값을 배포본이 하나도 인식하지 못했다 ← 가장 중요

ICD 는 `source_type` enum 을 이렇게 규정한다.

```
public               공개 웹·보도·공시·뉴스레터   S=0 (Gate-1)
registered_patent    등록·공개 특허·실용신안      S=0 (Gate-1)
academic             학술 발표 논문·학위논문      S=0 (Gate-1)
internal             사내 비공개                 텍스트로 S 산정
external_confidential NDA 하 외부 수령            텍스트로 S 산정
```

그런데 배포본 `_PUBLIC_SOURCE_TOKENS` 는 이랬다.

```
court_decision · public_disclosure · published_patent · 판례 · 공시 · 채용공고 ·
보도자료 · 공개특허 · 등록특허 · 공개공보 · 특허공보
```

**ICD 값이 하나도 없다.** KL 이 규약대로 `public` 을 보내면 출처 prior 가 한 번도 안
걸린다. 실제로 A/B 실행에서 공개 사업공고문이 `source_type=public` 을 받고도 TS 로
나왔다.

→ ICD 3값을 토큰에 추가. 음성 토큰(`internal` · `non-public` · `nonpublic` ·
  `unpublished` · `private`)이 먼저 검사되고 토큰 분리가 하이픈·언더스코어를 보존하므로
  `non-public` · `external_confidential` 은 안 새어 든다. 계약을 테스트로 고정했다.

**교훈은 기록에 이미 있던 것이다 — 문서끼리 대조해서는 유령 계약을 못 잡는다.
문서의 식별자를 소스에 넣어 봐야 잡힌다.** 이번에는 값을 실제로 태워서 잡았다.

### (2) 모델 없이 돌아도 PASS 가 나온다

첫 실행은 `MODEL: loaded=False · rule_fallback=10/10` 이었다. `CLASSIFIER_MODEL_DIR`
미지정 시 조용히 룰로 떨어지는데 **판정은 PASS 였다.** 보고서가 그 줄을 찍고는 있으나
게이트 조건이 아니다. 로그를 안 읽었으면 "운영 경로 통과" 로 잘못 보고했을 것이다.

→ 인수 실행 절차에 모델 로드 확인을 필수로 넣어야 한다(판정 조건 승격 검토).

### (3) VETO 1건은 테스트 정의 오류였다

구형 `.hwp` 에서 숫자 `3287` 유실로 VETO 했는데, 추적하니 그 숫자는 **전화번호 조각**
(`02-3287-4218`)이고 원문 2회 출현이 전부 그 안이다. 전처리가 `[PHONE]` 으로 마스킹한
정상 동작을 숫자 유실로 셌다.

표 회수 자체는 정상이었다 — unhwp 보강으로 **표 47개 · 36,033자 회수, 나머지 숫자 4종
전부 보존**. 숫자 무손실 게이트의 대상은 **문서가 담은 값**이지 PII 가 아니다.

→ 기대 토큰에서 제외하고 이유를 fixture 에 남겼다.

---

## 3. 메타데이터 경로는 실제로 동작한다

```
doc                     A     B     B 에서 걸린 게이트
acc-TS-01               TS    TS    metadata-management
acc-S1-03/04            S1    S1    metadata-management
acc-S3-07               S3    S3    metadata-access-conflict · metadata-management
acc-S3-08               S3    S3    metadata-access-conflict
real-S3-ipo-ksensor     S3    S3    metadata-access-conflict · metadata-management
real-S3-iprd-notice     TS -> S3    metadata-management · source-prior
```

`metadata-access-conflict` 가 3건에서 걸린 것이 특히 의미 있다 — **접근이 제한된 문서를
S3 로 예측한 상태**이고, 그것이 무음 미탐의 모양이다. 게이트가 검수로 돌렸다.

---

## 4. 이 실행이 보증하지 않는 것

```
⚠ 팩 문서는 본문에 등급 표시가 이미 찍혀 있다(TS=특급기밀 · S1=1급비밀 · S2=대외비 ·
  S3=표시없음). 그래서 등급일치 10/10 은 **판별력의 근거가 아니다.**
⚠ 메타데이터도 그 표시에서 뽑았으므로 B 실행은 **추가 정확도를 보여주지 않는다.**
  보여주는 것은 배관이 동작한다는 사실뿐이다.
⚠ n=10 이다. 어떤 비율도 신뢰구간이 무의미하다.
```

실문서 정확도 주장에는 사람이 라벨링한 문서가 필요하다. 가장 가까운 재료는 이미 포장돼
있다 — `datasets/golden_review/ff5a822c` 120건(TS/S1/S2/S3 각 30건, `review_status=pending`,
`reviewer_id=None`). 만드는 비용은 0이고 필요한 것은 검수자뿐이다.
