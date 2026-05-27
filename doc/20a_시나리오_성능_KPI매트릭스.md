# 시나리오 성능 KPI 매트릭스

작성일: 2026-05-27 (W10 PSH 착수)
버전: v1.0
관련: [doc/19 KL 통합 8시나리오](19_KL_통합_8시나리오.md) · [doc/02 §3.3 PoC 합격선](02_기술스택_확정_및_PoC_계획.md) · [doc/20 시나리오 성능 보고서 HTML](20_시나리오_성능_보고서.html)

> **본 문서의 역할**: doc/19가 시나리오 동작 명세를 정의한다면, 본 문서는 **각 시나리오에 대한 정량 KPI·합격선·측정 방법**을 정의한다. PSH(Performance Scenario Harness)의 입력 명세이자, doc/20 HTML 보고서의 표시 항목 사양.

---

## 0. 측정 원칙

1. **dryrun / full 두 모드**:
   - **dryrun**: noop LLM + hash 임베딩 + inmemory 벡터 + TestClient in-process. 외부 의존 0. 합격선 판정 로직 자체 검증용. ~30초 내 완료.
   - **full**: 실 LLM/임베딩/ES/PG + 학습된 분류 모델 활성. GPU·API 키·docker 인프라 필요. 보고용 실측치 산출.
2. **Skip은 에러가 아니다**: 환경 미충족(`pg/es/redis/minio/llm/gpu/trained_model`)으로 시나리오가 안 돌면 `SKIP(reason)`으로 기록. 합격선 판정에서 제외하되 보고서에는 명시.
   - `trained_model`은 DB의 active ModelVersion이 비어있지 않은 model_uri를 보유한 경우 True. 환경변수 `LLOYDK_TRAINED_MODEL=1`로 강제 활성 가능 (CI용).
   - **F1·FNR·Recall@5**는 룰 라벨러 fallback에서 의미가 없어서 `trained_model` 의존으로 묶고 dryrun에서 자동 SKIP — 보고 노이즈 제거.
3. **반복 측정 + 백분위**: latency 계열은 N=10~20 반복(warmup 1~2회 제외), p50·p95·max 동시 기록. 변동 큰 시나리오는 N 상향.
4. **회귀 추적**: 매 실행 시 `poc/reports/perf/perf_{ts}_{sha}.json` 누적. 최근 5회와 비교 sparkline.
5. **환경 캡처**: Python·OS·CPU·RAM·GPU·docker 가용성·git SHA·pytest collected count를 모든 보고서에 동봉.

---

## 1. 시나리오 × KPI 매트릭스 (S1~S8)

전체 30개 측정점. **`핵심`** 표시는 보고서 상단 요약 위젯에 노출.

### S1. 단일 문서 분류 동기 (`POST /classify`)

| # | KPI | 단위 | 합격선 | 측정 방법 | 핵심 |
|---|---|---|---|---|:-:|
| S1.1 | p50 latency | ms | ≤ 500 | N=20 호출, RAG OFF, content 200자, 1~N 제외 후 백분위 | |
| S1.2 | p95 latency | ms | ≤ 5000 | 동일 N=20 | ✅ |
| S1.3 | F1-macro (4-class) | 비율 | ≥ 0.75 | hold-out 200건 (등급별 50건) 일괄 호출 → predicted vs target. **requires=trained_model** (dryrun 룰 fallback에서는 자동 SKIP) | ✅ |
| S1.4 | **FNR (TS→하위)** | 비율 | ≤ 5% | hold-out 200건 중 target=TS인 50건의 오분류율. **requires=trained_model** | ✅ **핵심 KPI** |
| S1.5 | 응답 스키마 정합 | bool | True | label·confidence·evidence·elapsed_ms 필드 존재 | |

### S2. 대용량 비동기 분류 (`POST /classify/async` + `POST /classify/batch`)

| # | KPI | 단위 | 합격선 | 측정 방법 |
|---|---|---|---|---|
| S2.1 | async 202 응답 latency | ms | ≤ 200 | N=10, job_id 반환 시간 |
| S2.2 | batch 5건 throughput | docs/s | ≥ 5 | batch 5건 처리 경과시간으로 환산 |
| S2.3 | job 폴링 정합 | bool | True | status ∈ {queued,running,done}, 최종 done 시 completed==total |

### S3. 관리자 확정·재라벨 (`POST /confirm` + `POST /relabel`)

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S3.1 | confirm 응답 latency p95 | ms | ≤ 300 | N=10 | |
| S3.2 | relabel 응답 latency p95 | ms | ≤ 300 | N=10 | |
| S3.3 | corrections 누적 정합 | bool | True | confirm 1회 + relabel 1회 후 DB count == 2 | **PG** |
| S3.4 | classifications.status='corrected' 전이 | bool | True | relabel 후 status 컬럼 검증 | **PG** |

### S4. 등급체계 변경 + 재학습 필요성 (`PUT /schema/grades`)

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S4.1 | GET grades 응답 | bool | True | 4등급 {TS,S1,S2,S3} 포함 | |
| S4.2 | 동일 grades PUT → requires_retraining=False | bool | True | 현재 grades 그대로 PUT | **PG** |
| S4.3 | 신규 코드 추가 PUT → requires_retraining=True | bool | True | grades에 새 코드 추가 PUT | **PG** |
| S4.4 | requires_retraining 응답 latency p95 | ms | ≤ 500 | N=5 | **PG** |

### S5. 가이드 문서 업로드 → RAG 인덱싱 (`POST /guide/documents`)

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 | 핵심 |
|---|---|---|---|---|---|:-:|
| S5.1 | 업로드+인덱싱 latency p95 | ms | ≤ 30000 | 텍스트 가이드 ~10KB N=5 | | |
| S5.2 | embedding_vector_count | count | > 0 | 응답 body 검증 | | |
| S5.3 | 인덱싱 throughput | chunks/s | ≥ 0.3 | chunk_count / elapsed. dryrun(hash+InMemory)에서는 도달 검증 수준, full(KURE-v1+ES)에서 실측 회귀 추적 목적 | | |
| S5.4 | **Recall@5** | 비율 | ≥ 0.80 | P2 평가셋 (가이드 + 쿼리 100쌍) 재활용. **requires=es + trained_model** (dryrun hash 임베딩에서는 자동 SKIP) | **ES + KURE** | ✅ |
| S5.5 | 후속 GET /guide/documents/{id} 200 | bool | True | 업로드 후 조회 | | |

### S6. 합성 문서 생성 → 검수 → 데이터셋 편입 (`/synth/*`)

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 | 핵심 |
|---|---|---|---|---|---|:-:|
| S6.1 | generate 202 응답 latency | ms | ≤ 500 | N=5, count=10 | | |
| S6.2 | **라벨 일치도** (target ↔ predicted) | 비율 | ≥ 90% | 생성 10건 × 4등급 = 40건, M3 룰 라벨러로 재라벨링 후 일치 비율 | | ✅ |
| S6.3 | 비용/건 | USD | ≤ $0.02 | full 모드 시 Anthropic 토큰 카운터 (dryrun=$0) | **LLM** | |
| S6.4 | 검수 큐 진입 | bool | True | `GET /synth/queue` 응답에 generated 건 포함 | | |
| S6.5 | review 후 dataset 연결 | bool | True | approve 1건 후 sample_documents.review_status='approved' | **PG** | |

### S7. URGENT_RETRAIN 트리거 (corrections × 10건 → `POST /train`)

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S7.1 | 임계치 도달 검증 | bool | True | relabel 10회 후 retrain_threshold 도달 응답 | **PG** |
| S7.2 | /train 트리거 응답 latency | ms | ≤ 1000 | N=3 | **PG** |
| S7.3 | TrainingRun.queued 레코드 생성 | bool | True | DB 조회로 queued 1건 존재 확인 | **PG** |

### S8. 운영 지표·CM 조회 (`/metrics/*`)

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S8.1 | `GET /metrics/latest` 200 + 스키마 | bool | True | 응답에 model_version·f1·fnr 포함 | |
| S8.2 | latest 응답 latency p95 | ms | ≤ 500 | N=10 | |
| S8.3 | `GET /metrics/confusion-matrix/{ver}` 응답 시간 p95 | ms | ≤ 30000 | N=3 | **PG** |
| S8.4 | history 페이지네이션 정합 | bool | True | offset/limit 동작 검증 | **PG** |

---

## 2. 상단 요약 위젯 (HTML 보고서 §0)

다음 5개를 카드 위젯으로 노출:

| 위젯 | 출처 | 표시 |
|---|---|---|
| **PASS 비율** | 전체 30 KPI 중 PASS 개수 | `28 / 30` + 색상 (≥90% 녹색·≥75% 황·미만 적) |
| **FNR (S1.4)** | 핵심 KPI | `0.0%` + 합격선 비교 |
| **F1-macro (S1.3)** | 핵심 KPI | `1.00` + 합격선 비교 |
| **S1.p95 latency** | S1.2 | `XXXms` + 합격선 비교 |
| **Recall@5 (S5.4)** | 핵심 KPI | `0.70` + 합격선 비교 |

---

## 3. 환경·재현 메타데이터 (HTML 보고서 §4·§5)

매 실행 시 다음 정보를 JSON에 동봉:

```yaml
env:
  python: "3.11.x"
  platform: "win32 / linux"
  cpu_count: 8
  ram_gb: 16
  gpu: "N/A | NVIDIA RTX A6000 48GB"
  git_sha: "88eb9b3"
  git_branch: "main"
  pytest_collected: 191         # 또는 301
  services:
    postgres: "UP | DOWN"       # SELECT 1 시도
    elasticsearch: "UP | DOWN"  # GET / 시도
    redis: "UP | DOWN"
    minio: "UP | DOWN"
  llm_provider: "noop | anthropic | local-openai"
  embedding_provider: "hash | kure-v1 | bge-m3"
  vector_backend: "inmemory | es"

run:
  mode: "dryrun | full"
  ts: "2026-05-27T14:32:11Z"
  duration_sec: 12.4
  cmd: "make perf-scenarios"
```

---

## 4. 회귀 추적 정책

- `poc/reports/perf/perf_{ts}_{sha}.json` 단조 누적 (삭제 금지, gitignore 대상)
- HTML 렌더 시 **동일 모드(dryrun 또는 full)의 최근 5회**를 sparkline으로 표시
- 핵심 KPI 4종은 항상 sparkline 노출: FNR · F1 · p95_latency · Recall@5
- 회귀 감지 규칙: 직전 대비 latency +20% 또는 정확도 -5%p 이상 → 보고서 §3에 "회귀 가설" 자동 기록

---

## 5. CI 정책

- **dryrun-mode**: GitHub Actions `poc-ci.yml`에 `perf-scenarios-dryrun` 잡 추가 — KPI 1건이라도 미달 시 **FAIL**
- **full-mode**: 수동 트리거 (`workflow_dispatch`) 또는 nightly. KPI 미달은 **WARN** (LLM 단가·지연 변동성 고려)
- 산출물 `doc/20_시나리오_성능_보고서.html`은 매 실행 시 artifact upload (90일 보관)

---

## 6. 변경 이력

- v1.0 (2026-05-27): W10 PSH 착수와 함께 신규 작성. S1~S8 × 평균 3.75 KPI = 30 측정점. 핵심 KPI 5종.
