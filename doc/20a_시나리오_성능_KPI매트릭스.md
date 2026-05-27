# 시나리오 성능 KPI 매트릭스

작성일: 2026-05-27 (W10 PSH 착수)
버전: v1.2
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

### S9. 적대적·모호 문서 FNR 스트레스

W10 신규 — KOIPA 핵심 미션 "TS 미탐 최소화" 노이즈 내성 검증.

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 | 핵심 |
|---|---|---|---|---|---|:-:|
| S9.1 | 변형 일관성 (consistency) | 비율 | ≥ 0.70 | 원문 ↔ 변형 N=10 케이스 × 3 변형의 label 일치율. dryrun 룰 fallback 기준 보수적 합격선 | | |
| S9.2 | **적대적 FNR (TS→하위)** | 비율 | ≤ 0.05 | TS 케이스 변형 후 미탐율. **requires=trained_model** (dryrun SKIP) | trained_model | ✅ |
| S9.3 | confidence 변동 표준편차 | 비율 | ≤ 0.20 | 변형 전후 confidence stdev | | |

### S11. 부하 시나리오 (Concurrent Stress)

W10 신규 — 운영 시 다수 기업·동시 호출 대응.

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S11.1 | 동시 50 error_rate | 비율 | ≤ 0.01 | threading.Thread 50, 5 RPS × 10초. HTTP 5xx + Exception 비율 |  |
| S11.2 | 동시 50 p95 latency | ms | ≤ 8000 | 동일 조건 |  |
| S11.3 | throughput | req/s | ≥ 5 | 총 응답 수 / 경과시간. dryrun 기준 (full 모드는 ≥ 20) |  |

### S13. 멀티 테넌트 격리

W10 신규 — K5 자체결정 (N개사 멀티) — 데이터 누출 사고 방지.

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S13.1 | **교차 노출 횟수** | count | == 0 | tenant_B 응답 본문에서 tenant_A 식별자(`ALPHA-7`, tenant-A 키워드) 검색 횟수 | |
| S13.2 | audit tenant_id 정합 | 비율 | ≥ 0.99 | audit_log.tenant_id == request 헤더 X-Tenant-Id 일치율 | PG |
| S13.3 | 가이드 인덱스 분리 | bool | True | tenant_A 가이드 검색 결과에 tenant_B doc_id 0건, 역도 동일 |  |

### S10. RAG 인용 충실도

W11 신규 — 환각 인용 방지. 응답 evidence가 입력 본문/가이드에 실제 존재하는가.

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S10.1 | **grounded_ratio** | 비율 | ≥ 0.70 | evidence 텍스트 중 입력 본문/가이드 substring 존재 비율 (N=5 호출 평균) |  |
| S10.2 | evidence_count | count | > 0 | return_evidence=true 시 evidence 배열 길이 (min) |  |
| S10.3 | label-evidence 일관성 | 비율 | ≥ 0.80 | evidence가 분류 label과 같은 등급 키워드를 포함하는 비율 |  |

### S16. 권한·인증 거부

W11 신규 — 잘못된 API Key·role mismatch 일관된 거부.

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S16.1 | 잘못된 키 401 응답 | bool | True | X-API-Key=wrong 시 status 401 |  |
| S16.2 | 키 누락 거부 | bool | True | 헤더 누락 시 status ∈ {401, 422} |  |
| S16.3 | 거부 응답 p95 latency | ms | ≤ 200 | 인증 실패 빠른 회귀 (N=10) |  |
| S16.4 | 정상 키 200/201 | bool | True | 컨트롤 — 정상 키는 통과 |  |

### S17. 감사 로그 무결성

W11 신규 — 모든 KL 호출이 audit_log에 actor/role/tenant로 기록.

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S17.1 | **audit_count 정합** | 비율 | ≥ 0.95 | (audit 카운트 / 호출 카운트) — N=5 호출 후 동일 actor_id 조회 | **PG** |
| S17.2 | actor_role 일치 | 비율 | ≥ 0.99 | audit 행의 actor_role == 헤더 X-Actor-Role | **PG** |
| S17.3 | timestamp 단조 증가 | bool | True | 동일 actor 행들의 created_at이 호출 순서와 일치 | **PG** |

### S18. 폐쇄망 번들 무결성

W11 신규 — manifest + CHECKSUMS 결정론·완전성.

| # | KPI | 단위 | 합격선 | 측정 방법 | 의존 |
|---|---|---|---|---|---|
| S18.1 | manifest 존재 | bool | True | dry-run 후 manifest.yaml + manifest.json + CHECKSUMS.sha256 3종 존재 |  |
| S18.2 | manifest 결정론 | bool | True | 2회 dry-run의 artifacts 리스트 동일 (build_tag 제외) |  |
| S18.3 | dry-run 시간 | ms | ≤ 30000 | 1회 dry-run 경과시간 |  |

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

## 5.5 임계 갭 분석 (dryrun ↔ full)

같은 KPI가 dryrun과 full 환경에서 의미가 다른 경우가 있습니다. 단일 threshold로 둘 다 평가하면 한쪽이 과대/과소 판정됩니다. v1.1에서 도큐먼트만, 코드 분기는 v1.2 결정 후 적용 예정.

| KPI | 현 임계 | dryrun 해석 | full 해석 | 권장 |
|---|---|---|---|---|
| **S1.3 F1-macro** | ≥ 0.75 | 룰 라벨러 키워드 매칭만으로 정렬된 평가셋에서 우연 통과 가능 | KF-DeBERTa 학습 후 본격 합격선 | `requires=["trained_model"]` 적용됨 — dryrun SKIP. 그대로 유지 |
| **S1.4 FNR(TS→하위)** | ≤ 0.05 | 룰 fallback에선 키워드 외 의미 무시라 0.2~0.4 흔함 | 보안 미탐 핵심 KPI | `requires=["trained_model"]` 적용됨 — dryrun SKIP |
| **S5.3 인덱싱 throughput** | ≥ 0.3 chunks/s | hash 임베딩 + InMemory: 모델 로드 비용 없이 1~2 chunks/s 가능 | KURE-v1 + ES: cold 0.4~1.5, warm 10~30 chunks/s | 너무 보수적 — full에선 ≥ 5 chunks/s가 맞음. mode-aware threshold 후보 |
| **S5.4 Recall@5** | ≥ 0.80 | hash 임베딩에선 우연 매칭 0.3~0.6 | KURE-v1 + 검증셋에서 본 합격선 | `requires=["es","trained_model"]` 적용됨 — dryrun SKIP |
| **S2.2 batch throughput** | ≥ 5 docs/s | in-process 즉시 실행이라 50~200 docs/s | Celery 실가동 시 5~20 docs/s | mode 무관, 그대로 |
| **S5.1 업로드+인덱싱 p95 latency** | ≤ 30000 ms | hash 임베딩 + InMemory: 100~500 ms | KURE-v1 cold start: 10~30 s 가능 | 그대로 (full도 마지노선) |
| **S6.3 비용/건** | ≤ $0.02 | LLM 미가용이라 SKIP | Claude Sonnet 4.6 기준 $0.005~0.015 | `requires=["llm"]` 적용됨, 그대로 |

### 결론
- 대부분의 핵심 KPI는 `requires`로 dryrun에서 자동 SKIP 처리됨 — 추가 분기 불필요
- **S5.3 throughput만 mode-aware threshold가 정확** (dryrun ≥ 0.3 / full ≥ 5)
- v1.2 결정 시: `KPI.threshold`를 `dict[mode, value]`로 확장하거나, 시나리오 함수에서 mode별 ctx.record 키 분리

## 5.6 Prometheus pushgateway 연동 (선택)

운영 환경에서 매 PSH 실행 결과를 Grafana 대시보드로 누적 추적할 수 있도록 옵션 제공.

### 사용법
```bash
# CLI
python scripts/run_perf_scenarios.py --mode dryrun --push-prom http://pushgw:9091

# 또는 환경 변수
PROM_PUSHGATEWAY_URL=http://pushgw:9091 \
  python scripts/run_perf_scenarios.py --mode full
```

### 노출 메트릭
| 메트릭 | 라벨 | 의미 |
|---|---|---|
| `lloydk_psh_summary_pass` | mode, git_sha | PASS 건수 |
| `lloydk_psh_summary_fail` | mode, git_sha | FAIL 건수 |
| `lloydk_psh_summary_skip` | mode, git_sha | SKIP 건수 |
| `lloydk_psh_summary_total` | mode, git_sha | 전체 KPI 수 |
| `lloydk_psh_pass_rate` | mode, git_sha | PASS / total |
| `lloydk_psh_kpi_measured` | kpi_id, scenario, unit, compare, mode, git_sha | 측정값 |
| `lloydk_psh_kpi_threshold` | kpi_id, scenario, unit, compare, mode, git_sha | 임계값 |
| `lloydk_psh_kpi_passed` | kpi_id, scenario, mode, git_sha | 1=PASS, 0=FAIL, -1=SKIP, -2=ERROR |

### Grafana 쿼리 예시
```promql
# PASS 비율 시계열 (mode=full만)
lloydk_psh_pass_rate{mode="full"}

# 특정 KPI 추세 (FNR)
lloydk_psh_kpi_measured{kpi_id="S1.4"}

# FAIL인 KPI 즉시 식별
lloydk_psh_kpi_passed == 0
```

### 정책
- 전송 실패는 silent — PSH 자체 결과·CI exit code에 영향 없음
- 폐쇄망 환경: pushgateway 미가용이면 단순히 옵션 미사용 (기본값 빈 문자열)
- prometheus_client 의존성 없이 stdlib urllib만 사용 — 추가 패키지 없음

## 5.7 회귀 자동 탐지 (v1.2 신규)

PSH는 직전 회차 대비 측정값의 상대 변화율로 **회귀(regression)**를 자동 탐지합니다. 임계값 절대 통과 여부와 별개로, "이전보다 N% 이상 악화" 패턴을 잡아냅니다.

### 사용법
```bash
# 30% 이상 악화 시 보고서에 표기만 (exit 0)
python scripts/run_perf_scenarios.py --mode dryrun --regression-threshold 30

# CI 게이트: 회귀 1건이라도 발견 시 exit 1
python scripts/run_perf_scenarios.py --mode full \
  --regression-threshold 20 --fail-on-regression
```

### 판정 로직 (poc/src/lloydk/perf/regression.py)
| 조건 | 동작 |
|---|---|
| `compare=le` (latency, FNR) + 측정값 증가 | 회귀 후보 (↑) |
| `compare=ge` (recall, throughput) + 측정값 감소 | 회귀 후보 (↓) |
| `unit ∈ {bool, count}` | 비교 제외 (이산값) |
| 현·이전 어느 쪽이라도 SKIP/ERROR | 비교 제외 |
| `prev=0` | 비율 계산 의미 없어 제외 |
| 변화율 < threshold_pct | 회귀 아님 |

### JSON 출력
`reports/perf/perf_*.json`의 `regressions` 키에 배열로 저장:
```json
[
  {
    "kpi_id": "S3.2",
    "name": "relabel p95 latency",
    "unit": "ms",
    "prev": 39.1,
    "curr": 115.0,
    "delta_pct": 194.9,
    "direction": "up",
    "compare": "le",
    "threshold_pct": 30.0
  }
]
```

## 5.8 GitHub Actions PR 코멘트·Pages publish (v1.2 신규)

- **PR 코멘트**: 각 PR마다 `actions/github-script@v7`이 `perf_latest_dryrun.json`을 읽어 KPI 요약·회귀 표·FAIL 목록을 코멘트로 자동 게시
- **GitHub Pages**: main 푸시 시 `doc/20_시나리오_성능_보고서.html`을 `peaceiris/actions-gh-pages@v4`로 gh-pages 브랜치에 publish (`/index.html` + `/perf/*.json`)
- 조건: PR 코멘트는 `github.event_name == 'pull_request'`, Pages는 main 푸시만

---

## 6. 변경 이력

- v1.2 (2026-05-28): §5.7 회귀 자동 탐지 + §5.8 PR 코멘트·Pages publish. S5.2/S5.3 dryrun 가드(vc>0일 때만 record). pyproject `[psh]·[evaluation]` extras 그룹화 + matplotlib base 명시. 신규 18 회귀 테스트 (test_perf_regression). 전체 회귀 348→385 (+B1 BM25 19, B2 semantic 5, E1·E2 9, A3 18, C1 정리 −일부, ...).
- v1.1 (2026-05-28): §5.5 임계 갭 분석 + §5.6 pushgateway 연동 추가. trained_model 자원 플래그로 S1.3·S1.4·S5.4 자동 SKIP 적용. PSH 자체 단위 테스트 50+ 추가 (perf_harness 23 + perf_render 16 + perf_pushgateway 11). HTML §2.5 전체 KPI 추세 표(임계선 sparkline) 신규.
- v1.0 (2026-05-27): W10 PSH 착수와 함께 신규 작성. S1~S8 × 평균 3.75 KPI = 30 측정점. 핵심 KPI 5종.
