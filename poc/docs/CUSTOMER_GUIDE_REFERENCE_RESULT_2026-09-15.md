# 가이드 기반 조건부 참조 20건 제작 결과

2026-09-15. 목표는 우리가 제작한 문서로 학습800 / 평가200의 고객사 시험을 준비하는 것이다.
이번에는 원 가이드 기반 진행 요청에 따라 내부 기준 0.1, 별도 가상 맥락, 조건부 고정 답안과 재현 도구를 만들었다.
이전 준비 단계의 ‘정책 선택 대기 / 고정 답0’을 **이번 합성 시험용 내부 참조에 한해** 갱신한다.
사람의 서명이나 고객사 정책 채택은 생성하지 않았다.

## 실제 완성 수량

| 구분 | TS | S1 | S2 | S3 | 합계 |
|---|---:|---:|---:|---:|---:|
| 내부 가상 조건 아래 고정한 참조 답 | 4 | 4 | 6 | 6 | 20 |
| 등급별250 목표까지 부족분 | 246 | 246 | 244 | 244 | 980 |

- 본문20개: 이전 별도 원고12개 그대로 + 신규 원고8개. 신규20개가 아니다.
- 본문 주장 인용·위치·해시40개, 가상 맥락 사실 바인딩280개.
- 독립 산술 절차로 본문 계산11항목 검산, 27개 S/V/M 조합을 다른 계산 방식으로 확인.
- 학습/평가 배포 채택0. 아직1,000개 제작·800/200분할·골든200동결을 하지 않았다.
- 가상 맥락이 없는 본문만으로 이20개 답을 요구할 수 없다. 본문 전용 등급 채점 대상0.

### 신규 원고8개

접합 시편 열처리 구간표, 광학 지그 영점 보정 계산서, 차기 분기 생산능력 배정 시나리오,
설비 투자 지급구간 조정안, 건조기 부하별 전력 관측 요약, 상차 작업 순서 계산안,
반품 용기 점검 항목 변경, 체험장 방문 순서 안내.
이름·숫자만 바꾼 복제본 대신 각각 다른 계산·관찰·업무 조건을 작성했다.
근접 중복 검사 통과가 의미적 독립성의 완전한 증명은 아니다.

## 기준과 정답의 권위

원 PDF16쪽을 시각적으로 검토하고 **11쪽 요소 기준 / 12쪽 4단계 곱셈표**를 사용했다.
5쪽의5요소 합산, 12쪽의2·3단계 방식, 기존 v22/fnr 보정은 혼합하지 않았다.
0→S3, 1/2→S2, 4→S1, 8→TS다. (2,2,0)은 S3이며 공개 허가와 같지 않다.

가이드에 수치가 없는 ‘상당한 비용/노력’을 반복 자의 해석하지 않도록 내부 시험 앵커를 명시했다.
V1은 비용≤100만원 AND 투입≤40인시(하나 이상 양수), V2는 비용≥3,000만원 OR 투입≥480인시다.
중간 구간은 HOLD이고, V0은 활용 없음과 비용/투입0이 모두 명시된 경우다.
해당 정보 취득개발에 귀속된 비용만 사용한다. 거래 금액·설비 가격·회사 매출과 바꾸어 쓰지 않는다.
**이 수치는 원 가이드 원문도, 고객사 승인 수치도 아니다. 내부 기준안 0.1이다.**

공개 여부, 보유자 외 취득 경로, 비용/노력, 실제 접근 제한은 문서 본문에서 추측한 사실이 아니라
별도로 주어진 가상 조건이다. 그 조건과 정책이 같으면 답을 재현할 수 있으므로 내부 조건부 답을 고정했다.
이는 검수자를 기다려 답안을 못 만들겠다는 뜻이 아니다. 반대로 가상 조건의 실제 진위나 고객 등급까지 보장한다는 뜻도 아니다.
세부 기준은 [참조 기준0.1](CUSTOMER_GUIDE_REFERENCE_V0_1.md)에 있다.

## 구현과 재발 방지

- `src/koipa/customer_guide_reference.py`: 원문 근거·버전·내부 앵커, 입력 맥락 codec, 정책 계산, unknown/충돌 HOLD.
- `scripts/customer_guide_cases.py`: 신규8개 원고와20개 가상 사실/판단 설명. 목표 등급을 생성기에 입력하지 않는다.
- `scripts/build_customer_guide_reference.py`: 별도 pack 생성/재검증, 입력·정답·근거 분리, 산술 검사, 본문 동일 반례.
- `scripts/measure_customer_guide_shortcuts.py`: **새 pack 형식 직접 지원** 누설 진단.
  0건·지원되지 않는 형식·답안 불일치·기존 출력 덮어쓰기·입력 pack 내부 출력은 실패한다.
- 신규 테스트122개. 필수 증거 누락, 모순, float/bool 숫자 혼동, 답/근거/입력/정책 변조,
  공개 허가 오인, 0건 성공, 수치 경계, 문서·manifest 재해시 조작을 회귀한다.
- 누설 경고20%p 경계의 부동소수점 잔여값은1e-9%p 허용오차로 처리한다.
  최초 진단은 보존하고 수정 후 `shortcut_diagnostic_final.json`으로 다시 측정했다.

`answers/REFERENCE_ANSWERS.md`에서 문서별 답과 근거를 바로 읽을 수 있다.
`answers/answers.candidate.jsonl`은 기존 준비 도구의 Answer 계약과 호환되며,
실제 맥락 근거는 `answers/evidence.jsonl`에서 정책 재계산과 함께 검증한다.
단순 Answer 스키마 검사만으로 의미·정책이 인증되었다고 주장하지 않는다.

## 현재 재실행 결과

| 검사 | 결과 |
|---|---|
| 추가 관련 회귀 포함 | 1,457 passed, 실패/오류/skip0 |
| DB·torch·transformers 차단 독립 data_quality | 1,099 passed, 실패/오류/skip0 |
| 신규 테스트 | 122개, 위 수치에 포함 |
| Ruff | 추가6개 Python 파일 통과 |
| 새 pack / 이전12개 pack 재검증 | 모두 통과 |
| 새20개 내부 정확/숫자변형/5gram Jaccard0.85 중복 | 모두0쌍 |
| 알려진 fixture 본문 registry 일치 | 0 |
| 현재20개 계열 연결 성분 | 20개, 의미적 독립 인증은 아님 |
| 기존datasets404JSONL 본문451,663행 비교 | 정확/숫자 정규화 일치0 |
| 기존풀 미지원본문/파싱실패 | 1,850행 / 14행, coverage_complete=false |
| 토큰 실측, 본문 | 157~225, 20/20이512토큰 이내 |
| 토큰 실측, 본문+맥락 | 422~491, 20/20이512토큰 이내 |

1,099개는1,457개의 부분집합이므로 합산하지 않는다.
451,663은 중복을 포함한 읽은 행 수이며 고유/학습 채택 문서 수가 아니다.
파싱실패14행은 기존 `datasets/gold_real/uncertain_cases.jsonl`1~14행이다. 원본은 수정하지 않았다.
전체 학습풀의 퍼지/의미적 근접 중복 검사는 아직 아니다.
토큰 수는 `artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json`을 이용한 실측이며 활성 모델/실제 추론 검증은 아니다.

### 문자 단서 진단 — 고객사 분류 성능이 아님

기존 문자2~5gram/LinearSVC 진단기를 재사용하고 5seed,4fold, 라벨 permutation과 계열 분리를 적용했다.
입력20개만으로 실행한 매우 작은 진단이며, 고객사 모델800건 학습/200건 평가는 아니다.

| 입력 | 층화CV 평균 | permutation | 계열CV 평균 | permutation |
|---|---:|---:|---:|---:|
| 본문만 | 35% | 23% | 35% | 22% |
| 맥락만 | 57% | 19% | 55% | 21% |
| 본문+맥락 | 44% | 24% | 41% | 23% |

맥락만 입력한 진단은 내부 경고선을 넘었다. 맥락은 정답 해설이 아니라 실제로 필요한 정책 전제이므로
높은 점수를 무조건 금지해야 할 누설로 단정하지 않는다. 반대로 본문 점수가 낮다고 누설 해소를 인증하지도 않는다.
이 작은 진단에서 TS/S1 재현율은 모든 회차0이므로, 종합 점수만 보고4등급 분류가 잘된다고 해석하면 안 된다.
현재 데이터의 성격은 **주어진 맥락 아래 근거 추출·정책 적용 참조**이며 본문 단독 분류 성능의 답안은 아니다.
같은 본문에서 가능한 맥락을 바꾸면20/20의 등급이 달라지는 반례도 보존했다. 반례는 신규 문서 수에 넣지 않는다.

## 원본 보존과 지문

- 이번 시작 시점65개 주요 파일(PDF·기존 원고/준비 도구·운영 코드 포함) 변경0.
- 기존 원본JSONL406개 변경0/누락0/추가0.
- 이전6개 pack의212payload와manifest 전부 보존. 새 pack34payload+manifest 검증.
- 9월14일 baseline 검사는 과거 `policy_engine.py` 수정1개 때문에 여전히 exit1 / CHANGED_REQUIRES_REVIEW다.
  이번 작업의 변경이 아니며 baseline을 다시 만들어 성공으로 숨기지 않았다.
- 기존 운영API·모델·분류경로·학습기·DB·fixture registry·서명·원라벨 변경 없음.
- 진단용 작은 선형분류기는 실행했지만 생산 모델 학습·추론·교체는 하지 않았다.
- Git HEAD `83c88e9f5833c61d01d36276e415be5bc24987df` 유지. 커밋/푸시하지 않았다.

새 pack: `poc/reports/CUSTOMER_GUIDE_REFERENCE_20260915/reference_v0_1/`

- 정책 SHA256: `e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9`
- manifest SHA256: `d1e8072450f854b6c069061b7d1976ad83fdbf177e3a6cecd3a31a6bed2eebca`
- 상위 기록: `baseline_before.json`, `workspace_preservation.json`, `tests-final.xml`, `isolated-tests-final.xml`,
  `shortcut_diagnostic_final.json`, `evidence-final.json`.
- reports는 Git ignore 대상이다. 로컬 저장과 외부 백업 완료는 다르다.

## 재현 명령 — poc 디렉터리

PowerShell에서 `$env:PYTHONIOENCODING='utf-8'`, `$env:PYTHONDONTWRITEBYTECODE='1'`을 설정한다.

```powershell
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_reference.py verify --pack reports/CUSTOMER_GUIDE_REFERENCE_20260915/reference_v0_1
# 생성/실측을 다시 수행할 때에는 존재하지 않는 새 출력 경로를 사용한다.
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_reference.py prepare --out reports/CUSTOMER_GUIDE_REFERENCE_20260915/reproduction_new --corpus-root datasets --tokenizer artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json
.\.venv\Scripts\python.exe -B scripts/measure_customer_guide_shortcuts.py --pack reports/CUSTOMER_GUIDE_REFERENCE_20260915/reference_v0_1 --out reports/CUSTOMER_GUIDE_REFERENCE_20260915/shortcut_reproduction_new.json --seeds 5
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_customer_guide_reference.py tests/data_quality/test_customer_guide_shortcuts.py -q
```

pack verify는 현재 제작 소스/PDF와 payload를 재검증한다. 외부 학습풀/토크나이저 결과는 입력 해시에 묶인 과거 관찰이며,
보고서에 적힌 임의 경로를 따라 읽어 현재 측정인 것처럼 만들지 않는다. 새 prepare는 실제 스캔을 다시 수행한다.

## 다음 작업 — 기준 선택 질문을 반복하지 않기

1. 가이드 기반 내부 기준안0.1로 이어서 서로 다른 원고를 집필한다. 검수자 확보를 기다리지 않는다.
2. 부족한980건을 각 등급별 부족분으로 추적하되, 본문/맥락을 먼저 작성하고 정책을 계산한다.
   V0, 중간 구간, unknown/충돌은 별도 검산 사례로 늘린다. HOLD를4등급 문서 수에 억지로 넣지 않는다.
3. 제목·본문·맥락을 분리한 누설/중복 검사와 근거 추출 검증을 확대한다. 현재20건의 문체 점수로 품질을 인증하지 않는다.
4. 시험 입력은 본문+맥락 경로임을 명시한다. 기존 본문 전용 운영 경로에는 아직 이 입력 계약을 연결하지 않았다.
5. 1,000건 선별 후 계열 단위800/200 분할·정답 동결·시험용 배포를 수행한다. 최종200개를 학습/튜닝에 보지 않는다.
6. 고객 프로토콜 채택과 생산 반영은 별도다. 기준안/조건부 답을 계속 작성하는 일과 혼동하지 않는다.
