# DataGen Studio round_1 — 품질 통과 1,000건 (2026-10-07)

## 출처

211.233.204.32:8765(DataGen Studio, 별도 합성 데이터셋 생성기 — koipa 콘솔과 무관)의
`result/round_1/round_1_generated.json`(2,000건) + `scenario_pool.json`(시나리오당
domain·label 메타데이터). 원본은 `poc/datasets/_raw_downloads/datagen_studio_211_8765_round1_20261007/`
에 그대로 저장돼 있다(round_1_generated.json·scenario_pool.json·seed_scenarios.json).

⚠ 등급 체계가 이 프로젝트의 TS·S1·S2·S3 와 **다르다** — 이 앱은 TS0(가장 민감)~TS3(공개 가능)
4단계를 쓴다(실측: TS3 라벨 문서의 access_context가 "전 임직원이 알 수 있고 대외에도
공개될 수 있는 상황"). 이 1,000건을 기존 TS/S1/S2/S3 체계와 섞어 쓰려면 매핑을 먼저 정해야 한다.

## 방법 (도구: `poc/scripts/curate_datagen_round1_1000.py`, 재실행 가능)

1. **품질 하한 — 프로젝트 정본 재사용**(`koipa.services.synth_quality`, mock1000 등
   기존 배치에 쓰던 것과 동일 함수). 하나라도 걸리면 제외:
   - `not isgood` — 생성기 자체 검증(길이 200~9,000자·근접중복 0.92 미만·시나리오
     키워드 포함) 탈락 8건
   - `grade_token_exposed` — 등급 코드/한국어 등급 표기 노출 2건
   - `self_grade_declaration` — "본 문서는 …기밀/유출금지…" 자기 진술 구조 2건
   - `pii_detected` — 전화번호·이메일 정규식 매칭 **129건**(가짜 회사 이메일
     `hr@company.com`·`jy.kim@gaon.co.kr`류, 실제 개인정보 아님을 샘플 확인했으나
     정규식은 형태만 보므로 프로젝트 관례대로 제외했다)
   - **분모·결과**: 생성 2,000건 중 통과 **1,865건**(93.25%), 제외 135건.
     전수 기록 = `_raw_downloads/.../all_2000_quality_audit.jsonl`.
2. **분야 배분** — 1,865건을 분야(18개)별로 세어 `1,000 × (분야 건수/1,865)`를
   최대나머지법으로 정수 배분(균등 배분 아님 — 원래 분야 비중을 유지). 분야 안에서는
   등급(TS0~TS3) 비중도 같은 방식으로 2단계 배분.
3. **분야×등급 칸 안에서 순위** — `quality_error_count`(한글비율·문단수·수치사실 등
   8지표, 0이 최선) → `reflection_epoch`(0=첫 시도 통과가 최선) → 글자수 내림차순
   순으로 상위 N건 선택.

## 결과 (실측, 1,000건 전수)

- 분야별: 생산199·연구개발141·영업107·총무85·회계56·인사51·경영기획50·품질42·법무42·
  IT39·해외사업32·전략30·재무30·구매24·마케팅22·고객지원20·물류18·정보보안12
  (원본 2,000건의 분야 비중과 동일한 순서 — 균등 배분이 아니다)
- 등급별: TS2 295·TS1 293·TS3 215·TS0 197
- 선정분 전부 `quality_error_count=0`·`exclusion_reasons=[]`(품질 하한 100% 통과,
  칸 안에 통과분이 배분량보다 적은 경우는 없었다 — 가장 작은 칸인 정보보안×등급도
  통과분이 충분했다)

## 파일

- `curated_manifest.jsonl` — 1,000행. doc_id(DG1-0001~1000)·scenario_id·domain·label·
  char_count·reflection_epoch·quality_error_count·source.
- `documents/DG1-####.txt` — 문서 본문 1,000개(제목 없음, 생성기 원문 그대로).

## 하지 않은 것 (한계)

- **라벨은 사람 확정 아님** — scenario_pool의 `label`은 생성 시점에 `_internal`(s·v·m·
  product) 값에서 **생성기가 taxonomy로 배정한 것**이다(`manual_grade: null` 확인).
  기존 mock1000 계열의 "가이드 카탈로그 직접 배정"과 같은 성격(사람 검수 전).
- **지름길·누출 검사 안 함** — mock1000-v2 때 했던 길이-1NN·tell-coverage 코퍼스 검사,
  TF-IDF 기준선 비교는 이번에는 돌리지 않았다. 품질 하한(개별 문서)만 봤다.
- **TS0~TS3 ↔ TS/S1/S2/S3 매핑 안 함** — 이 프로젝트 등급 체계로 쓰려면 먼저
  대응 규칙을 정해야 한다.
- 근접중복(코사인 유사도) 재검사 안 함 — 생성기 자체 검증(임계 0.92)만 신뢰했다.
