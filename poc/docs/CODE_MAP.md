# 코드 맵 — 요건에서 파일까지

소스는 약 190 파일 · 4만여 줄이지만, 요건 하나를 확인하는 데 필요한 파일은 보통 **4~5개**입니다.
이 문서는 "FUN-0XX 를 어디서 구현했나"를 파일 경로까지 답하기 위한 것입니다.

> 규모는 계속 바뀌므로 어림수로만 적습니다. 정확한 수치가 필요하면 직접 세십시오 —
> `Get-ChildItem src/koipa -Recurse -Filter *.py | Measure-Object`.

읽는 규칙은 어디서나 같습니다.

```
API 라우터  →  서비스  →  코어 모듈  →  (필요 시) 리포지토리 · 어댑터
계약만 정의    조율만 담당   실제 알고리즘    DB 접근      외부 경계
```

라우터에 로직이 없고 모듈에 HTTP가 없습니다. 그래서 **한 요건을 확인하려면
그 요건의 행 하나만 따라 내려가면 됩니다.**

---

## 1. 요건별 호출 경로

### FUN-002 — 가이드 문서 업로드 · 버전 관리

```
api/guide.py                       POST /guide/documents · GET /guide/documents/{id}
  └ services/guide_service.py      버전 관리 + RAG 색인
      └ modules/m4_training/rag_indexer.py
```

권한: `admin` · `kl_backend`. 가이드 문서는 전역 기준을 바꾸는 변경성 작업이라 검수자 역할은 제외됩니다.

---

### FUN-003 — 합성 샘플 · 골든셋 구축

두 갈래입니다. **합성 샘플**은 학습셋을 늘리는 쪽, **골든셋**은 정답지를 만드는 쪽입니다.

```
[합성]
api/synthesis.py                   POST /synth/generate · GET /synth/queue
  └ services/synthesis_service.py
      ├ modules/m1_synthesis/generator.py         합성 문서 생성
      ├ modules/m1_synthesis/witness_taxonomy.py  근거 유형 체계
      └ repositories/synth_repo.py                합성 검수 큐

[골든셋]
api/golden.py                      POST /golden/build · /golden/jobs/{id}/signoff
  └ services/golden_build_service.py
      ├ golden_builder.py          후보 빌드 (룰 + LLM 합의)
      ├ golden_tiers.py            3분할(silver_train / gold_candidate / locked_gold_eval)
      ├ golden_signoff.py          서명 → locked 승격 (순수 함수 · DB/시계 없음)
      ├ golden_review_html.py      검수·서명 화면 렌더
      └ services/job_store.py      비동기 잡 상태
```

**주의** — 골든 검수(`/golden/jobs/{id}/signoff`)와 운영 검수(`/confirm`)는 **다른 루프**입니다.
전자는 평가 정답을 만들고, 후자는 재학습 교정을 쌓습니다. 이름이 비슷하니 헷갈리지 마십시오.

`golden_signoff.py`가 순수 함수인 것은 의도입니다 — 서명 로직에 시계·DB가 없어야
같은 입력에 같은 결과가 나오고, 감사에서 재현할 수 있습니다.

---

### FUN-004 — 학습 · 재학습

```
api/training.py                    POST /train · GET /train/jobs/{id}
  └ services/training_service.py   게이트 판정 · 잡 등록 · 배포 결정
      ├ modules/m6_evaluation/deploy_gate.py      회귀 차단 게이트
      ├ modules/m6_evaluation/metrics.py          지표 산출
      ├ modules/m6_evaluation/locked_readiness.py 평가셋 준비도
      └ modules/m6_evaluation/anchor_eval.py      앵커 카드 평가
  └ workers/tasks.py               (Celery) 실제 학습 실행
      └ modules/m4_training/trainer.py
          └ modules/m4_training/chunk_expand.py   TRAIN 분할만 chunk 단위 확장
```

**학습 본체는 API 프로세스가 아니라 Celery 워커에서 돕니다.** `training_service`는
요청을 받아 게이트를 판정하고 잡을 등록할 뿐입니다. 학습 코드를 찾는다면
`workers/tasks.py` → `m4_training/trainer.py`로 가십시오.

`chunk_expand.py`가 **TRAIN 분할만** chunk 확장하는 것은 누수 차단입니다.
val/test까지 확장하면 같은 문서의 청크가 학습·평가 양쪽에 들어가 지표가 부풀려집니다.

---

### FUN-005 — 등급 분류 · 등급체계 관리

```
[분류 — 서빙 핫패스]
api/classify.py                    POST /classify
  └ services/classify_service.py
      ├ modules/m2_preprocess/pipeline.py   본문 전처리·청킹
      ├ modules/m5_inference/pipeline.py    분류기 추론 + 근거 추출   ★ 핵심
      ├ modules/m3_labeling/rule_engine.py  룰 합의 게이트
      └ repositories/classify_repo.py       결과·근거 저장

[등급체계]
api/schema_admin.py                GET · PUT /schema/grades
  └ services/schema_admin_service.py
```

**서빙 핫패스에는 LLM이 없습니다.** 등급 결정은 분류기 + 룰만 씁니다
(고객사 대부분이 CPU 런타임이라 LLM-free여야 합니다).
`m3_labeling/llm_labeler.py`가 classify_service에서 import되긴 하지만
등급 확정 경로가 아니라 보조 경로입니다.

등급체계를 바꾸면 학습 라벨 공간이 바뀌므로 재학습이 필요합니다 —
콘솔이 저장 시 경고하는 지점입니다.

---

### FUN-022 — 문서 텍스트 추출 · 전처리

```
api/documents.py                   POST /documents · POST /documents/analyze
  └ services/document_ingestion_service.py
      └ modules/m2_preprocess/pipeline.py
          ├ extractor.py    HWP · Word · Excel · PPTX · PDF · TXT 추출  (1,303줄 · 최대 파일)
          ├ normalizer.py   정규화
          ├ chunker.py      청킹
          └ pii_masker.py   개인정보 마스킹
      ├ repositories/document_repo.py
      ├ repositories/chunk_repo.py
      └ adapters/storage/            원문 저장 (폐쇄망 = 로컬 FS + AES-256-GCM)
```

지원 포맷의 정확한 범위는 [parser_support_matrix.json](parser_support_matrix.json)에 있습니다.
**OCR은 요건이 아닙니다** — FUN-022는 전자문서 텍스트 추출까지입니다.

---

### FUN-023 — 라벨링 규칙 · 태깅 키워드

```
api/keyword_admin.py               /admin/keywords CRUD
  └ services/keyword_admin_service.py
      └ modules/m3_labeling/seeds.py       키워드 시드 · 요소 정규화
  └ (저장 후) services/classify_service.py 서빙 룰 엔진 핫리로드

modules/m3_labeling/
  rule_engine.py    가이드라인 룰 판정
  llm_labeler.py    LLM 라벨링 (골든셋 구축용)
  consensus.py      룰 ↔ LLM 합의
  judge.py          판정
```

키워드를 고치면 서빙 룰 엔진이 DB 기준으로 재구성됩니다(핫리로드).
재기동 없이 반영되는 것이 설계 의도입니다.

---

### FUN-024 — 검수 · 평가 · 배포 게이트

```
[운영 검수]
api/confirm.py                     POST /confirm · GET /review-queue · POST /relabel
  └ services/confirm_service.py
      └ modules/m6_evaluation/reviewer_trust.py   검수자 신뢰도
      └ repositories/classify_repo.py

[평가]
api/metrics.py                     GET /metrics/latest · /history · /confusion-matrix
modules/m6_evaluation/
  metrics.py            지표 — high_grade_fnr(고등급 미탐) 포함   ★ 핵심 KPI
  deploy_gate.py        회귀 시 배포 차단
  kill_gate.py          운영 경보
  calibration.py        temperature 보정
  confusion_matrix.py   혼동행렬
  locked_readiness.py   평가셋 준비도
  active_learning.py    능동학습 대상 선별
```

`metrics.py`의 **방향성 미탐(고등급→저등급)만 측정**하는 부분이 이 시스템의 핵심 KPI입니다.
과분류(저→고)는 의도적으로 제외합니다 — 설계 목표가 미탐 최소화이기 때문입니다.

---

## 2. 층별 책임 — 어디에 뭘 넣는가

| 층 | 하는 일 | **하지 않는 일** |
|---|---|---|
| `api/` | 요청·응답 계약, 인증·권한, 상태코드 | 비즈니스 판단, DB 직접 접근 |
| `services/` | 유스케이스 조율, 트랜잭션 경계 | 알고리즘 구현, HTTP 처리 |
| `modules/` | AI 파이프라인 알고리즘 (m1~m6) | HTTP, 인증, 요청 컨텍스트 |
| `adapters/` | 임베딩·벡터스토어·LLM·스토리지 경계 | 도메인 판단 |
| `repositories/` | DB 읽기·쓰기 | 비즈니스 규칙 |
| `schemas/` | Pydantic 계약 | 로직 |
| `workers/` | Celery 비동기 (학습 등 장시간 작업) | 동기 응답 |
| **루트 `*.py`** | 계층에 속하지 않는 **순수 로직** — `golden_tiers` · `golden_signoff` · `golden_builder` · `config` 등 | HTTP·DB·시계 의존 |

**루트에 새 파일을 두는 조건** — HTTP·DB·시계에 의존하지 않아야 합니다.
`golden_signoff.py`가 기준입니다: 순수 함수라 같은 입력에 같은 결과가 나오고, 그래서
감사에서 재현할 수 있습니다. 이 조건을 못 지키면 `services/` 또는 `modules/`가 맞는 자리입니다.

**m1~m6 번호는 파이프라인 순서**이고 기능분해도·DFD의 프로세스와 1:1로 대응합니다.

```
m1 합성 → m2 전처리 → m3 라벨링 → m4 학습 → m5 추론 → m6 평가
```

---

## 3. 오해하기 쉬운 지점

| 헷갈리는 것 | 실제 |
|---|---|
| `noop` provider로 골든셋을 만들 수 있나 | **없습니다.** `noop`은 등급 라벨러가 아니라 합성 문서 생성기(title/body 반환)라 라벨 파싱이 `S3/0.5`로 떨어지고 룰과 합의되지 않아 **전건이 보류**로 갑니다(2026-08-08 실측: 8건 중 gold 0 · uncertain 8). LLM이 없는 환경에서는 `POST /golden/jobs/register`(재라벨링 없는 슬레이트 등록)가 유일하게 도는 경로입니다 |
| 검수가 두 개 | **골든 검수**(`/golden/.../signoff`, 평가 정답 생성) ≠ **운영 검수**(`/confirm`, 재학습 교정) |
| 학습이 어디서 도나 | `training_service`는 게이트·등록만. 실제 학습은 `workers/tasks.py` → `m4_training/trainer.py` |
| 분류에 LLM을 쓰나 | 등급 결정 핫패스는 **LLM-free**. LLM은 골든셋 구축·`/answer`에서만 |
| `X-Actor-Role`이 권한을 정하나 | 아니오. 서버 `.env`의 `API_KEY_ROLE`이 정합니다. 헤더는 위조 차단을 위해 무시 |
| 오류 코드로 분기하면 되나 | 심볼릭 오류 코드는 **없습니다**. 연동은 **HTTP 상태코드**로 분기하십시오 |
| 스토리지가 MinIO인가 | 폐쇄망 기본은 **로컬 FS + AES-256-GCM**. MinIO는 optional extra |

---

## 4. 읽지 않아도 되는 것

| 대상 | 이유 |
|---|---|
| `scripts/_lab/`, `scripts/_*.py` | 실험·일회성. 공식 진입점은 [scripts/README.md](../scripts/README.md) |
| `api/answer.py`, `services/rag_answer_service.py` | RAG 질의응답 — **요건 외 부가 기능**. 코드는 존치하나 휴면 |
| `perf/` | 성능 시나리오 하니스. 기능 요건과 무관 |
| `dist/` | 폐쇄망 번들 산출물 (생성물) |

---

## 5. 화면과 코드의 대응

관리자 콘솔(`/demo/admin.html`)은 두 루프를 탭으로 나눠 놓았고,
각 카드 제목에 해당 요건 ID가 배지로 붙어 있습니다.

| 콘솔 탭 · 카드 | 요건 | 호출 |
|---|---|---|
| [운영] 1 분류 실행 | FUN-005 | `POST /classify` |
| [운영] 2 분류 검수 큐 | FUN-024 | `/review-queue` · `/confirm` · `/relabel` |
| [운영] R 유사 문서 검색 | **요건 외** | `POST /rag/search` |
| [모델] G1 골든셋 후보 빌더 | FUN-003 | `POST /golden/build` |
| [모델] N 합성 샘플 생성 | FUN-003 | `POST /synth/generate` |
| [모델] 3 재학습 트리거 | FUN-004 | `POST /train` |
| [모델] 4 모델 서빙 · 배포 | — (운영) | `/admin/model/activate` · `/reload` |
| [모델] 5 모델 메트릭 | FUN-024 | `/metrics/latest` |
| [설정] E 등급체계 관리 | FUN-005-① | `PUT /schema/grades` |
| [설정] T 라벨링 · 태깅 규칙 | FUN-023-④ | `/admin/keywords` |
| [관제] ◎ 운영 관제 대시보드 | — (운영) | `/admin/dashboard` |

시연 스크립트도 같은 대응을 stdout에 찍습니다 —
`scripts/demo_e2e_8010.py`(루프 A) · `scripts/demo_e2e_golden.py`(루프 B).
