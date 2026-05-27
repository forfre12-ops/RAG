# 운영 Runbook — KOIPA AI 영업비밀관리시스템 (로이드케이 AI 파트)

작성일: 2026-05-28
버전: **v1.1** (W10·W11 PSH 알림 대응 절차 §8 신규 추가)
대상: KL 운영팀 / KOIPA 보안팀 / Lloydk DevOps
관련: [doc/04 모듈 설계](04_AI코어_모듈_상세설계.md) · [doc/12 폐쇄망 배포](12_폐쇄망_배포_설계.md) · [doc/13 ES 전환](13_벡터DB_ES_전환_계획서.md) · [doc/10 위험관리대장](10_위험관리대장.md)

## v1.1 변경 사항 (W10·W11)
- §8 **PSH 알림 대응 절차** 신규 — PR/main push 시 PSH가 KPI 측정 + 회귀 자동 탐지로 CI exit 1 처리. 운영팀 1차 대응 30초 행동·KPI별 책임 모듈 매핑·롤백 기준·false positive 가이드 포함
- §9·§10·§11·§12로 기존 §8·§9·§10·§11 시프트 (회신 의존 절차·정기 검토·연락망·결정 요약)
- 관련 코드: [`poc/src/lloydk/perf/`](../poc/src/lloydk/perf/) (scenarios·harness·regression·pushgateway), [`scripts/run_perf_scenarios.py`](../poc/scripts/run_perf_scenarios.py)
- 관련 보고서: [doc/20a KPI 매트릭스](20a_시나리오_성능_KPI매트릭스.md), [doc/20 HTML 보고서](20_시나리오_성능_보고서.html), GitHub Pages publish

## v1.0 변경 사항 (W7)
- §6 백업·복원: 실 스크립트 링크 추가 (`scripts/backup_postgres.py`, `backup_es_snapshot.py`, `backup_minio_mirror.py`)
- §6.4 DR 시나리오: `scripts/dr_restore_check.py` 자동 검증 추가, JSON 리포트 산출
- §1 관측성: Prometheus·Grafana·Loki 스택 가동 (`infra/observability/`), 대시보드 + 알람 규칙
- §2 장애 대응: Prometheus 지표 기반 자동 알람 (`alert_rules.yml`)
- pytest 289/289 PASS — W7 관측성·백업 18 신규 테스트 포함

---

## 0. 본 문서의 범위

본 Runbook은 **운영 단계에서 발생할 수 있는 시나리오별 대응 절차**를 정의합니다. PoC·개발 단계 절차는 [README](../poc/README.md)를 참고합니다.

핵심 6개 시나리오:
1. **장애 대응** (서비스 다운·지연·에러 급증)
2. **모델 재학습** (능동학습 누적 → 신모델 배포)
3. **모델 롤백** (신모델 합격선 미달 → 직전 모델 복귀)
4. **인덱스 재구성** (임베딩 모델 교체·매핑 변경)
5. **백업·복원** (스냅샷·재해 복구)
6. **운영 일상 점검** (일/주/월 단위)

---

## 0.1 관측성 스택 (W7)

운영 환경에 다음 스택이 함께 가동되어야 일상 점검·장애 대응이 가능합니다.

**가동 명령**:
```bash
cd poc
# 기본 인프라
docker compose up -d postgres elasticsearch minio redis mlflow
# 관측성 스택 (Prometheus + Grafana + Loki + Promtail + 익스포터 3종)
docker compose -f docker-compose.yml -f infra/observability/docker-compose.observability.yml up -d
```

**접속**:
| 서비스 | URL | 용도 |
|---|---|---|
| Grafana | http://localhost:3000 (admin/lloydk_dev_grafana) | 대시보드 `Lloydk Overview` |
| Prometheus | http://localhost:9090 | 메트릭 쿼리·알람 규칙 |
| Loki | http://localhost:3100 (Grafana 경유) | 모든 컨테이너 로그 (영업비밀 마스킹) |
| Lloydk API metrics | http://localhost:8000/api/v1/metrics-prom | 자체 노출 메트릭 |

**핵심 메트릭**:
- `lloydk_requests_total{route, status}` — 요청 카운터
- `lloydk_request_duration_seconds_bucket{route}` — 지연 히스토그램
- `lloydk_request_exceptions_total{type}` — 처리되지 않은 예외
- `lloydk_active_learning_pending_underclass` — 보안 미탐 누적 (핵심 KPI)
- `lloydk_active_learning_pending_total` — 전체 미소비 corrections

**알람 규칙**: [`infra/observability/alert_rules.yml`](../poc/infra/observability/alert_rules.yml)
- ApiDown / PostgresDown / ElasticsearchDown (P0/P0/P1)
- ApiLatencyP95High (>30s for 5m, P1) → §2.4 대응
- ApiErrorRateHigh (5xx > 5%, P1)
- ActiveLearningUrgent (underclass ≥ 10) → §3 재학습 트리거

---

## 1. 일상 점검 체크리스트

### 1.1 매일 (자동 알림 + 수동 확인)

| 항목 | 확인 방법 | 임계 |
|---|---|---|
| API 헬스 | `GET /healthz` | 200 응답, uptime > 0 |
| 분류 요청 수 | Prometheus `lloydk_classify_total` | 전일 대비 ±30% 이내 |
| FNR (미탐율) | Grafana 대시보드 | < 5% |
| 평균 분류 지연 (p95) | 동상 | < 3초 (RAG OFF), < 30초 (RAG ON) |
| ES 클러스터 상태 | `GET /_cluster/health` | green 또는 yellow |
| Postgres 연결 풀 | `pg_stat_activity` | active < max_connections * 0.8 |
| MinIO 디스크 | `mc admin info` | < 85% 사용률 |
| 능동학습 큐 | Redis `LLEN active_learning_queue` | < 1000 |

### 1.2 매주

| 항목 | 작업 |
|---|---|
| 미탐 사례 검토 | Grafana 이상 분류 보드 → 50건 sampling 수동 검수 |
| 모델 성능 추세 | MLflow 모델 비교 대시보드 — 1주 추세 검토 |
| 능동학습 큐 처리율 | 큐 인입 vs 검수 처리 — 인입 > 처리 시 재라벨 인력 보강 |
| 디스크 증가율 | ES `indices.store.size_in_bytes` 7일 추세 → 1년 추정 |
| 로그 retention | Loki rotation 정상 동작 확인 |

### 1.3 매월

| 항목 | 작업 |
|---|---|
| ES 스냅샷 검증 | 무작위 1개 인덱스를 staging 환경에 복원 → count 일치 확인 |
| 모델 재학습 트리거 | 능동학습 누적 ≥ 500건이면 [§3 재학습](#3-모델-재학습) 절차 시작 |
| 사용자 사전 갱신 | `userdict_ko.txt` 분기별 도메인 용어 추가 (KOIPA 가이드 개정 반영) |
| 라이선스 만료 | Elastic Platinum (사용 시) — 30일 전 알림 → 폐쇄망 반입 절차 시작 |
| 보안 패치 검토 | Trivy 스캔 결과 → critical/high CVE 식별, 다음 번들에 포함 |

---

## 2. 장애 대응

### 2.1 장애 등급 정의

| 등급 | 정의 | 대응 시간 |
|---|---|---|
| **P0 (긴급)** | 서비스 완전 다운 (전체 사용자 영향) | 15분 이내 1차 대응 |
| **P1 (높음)** | 핵심 기능 일부 불가 (분류 50% 실패 등) | 1시간 이내 |
| **P2 (보통)** | 보조 기능 영향 (대시보드·리포트 지연) | 4시간 이내 |
| **P3 (낮음)** | 사용자 영향 없는 백그라운드 이슈 | 1영업일 |

### 2.2 P0 — API 전체 다운

```
증상: /healthz 응답 없음 또는 5xx
```

**1차 대응 (5분 이내)**:
```bash
# 1. 컨테이너 상태
docker compose ps
docker compose logs api --tail 100

# 2. 의존 서비스 헬스
python scripts/verify_infra.py

# 3. 재시작 (대부분 1차에서 해결)
docker compose restart api worker
```

**원인 분류 (10분 이내)**:
| 증상 | 원인 | 대응 |
|---|---|---|
| `OOMKilled` | 메모리 부족 | docker compose.yml의 mem_limit 증가 또는 노드 메모리 확장 |
| `Connection refused: postgres` | DB 다운 | postgres 컨테이너 재시작 + `pg_isready` 확인 |
| `elasticsearch: ConnectionError` | ES 다운 | ES 컨테이너 재시작 + JVM heap 확인 |
| `ImportError` | 의존성 누락 | 마지막 배포 번들의 install.sh 재실행 |

**에스컬레이션**: 15분 내 미해결 시 → Lloydk PM + KL DevOps 동시 호출

### 2.3 P1 — 분류 정확도 급락 (FNR > 10%)

```
증상: Grafana 알람 — "FNR_5min > 0.10"
```

**1차 대응**:
```bash
# 최근 1시간 미탐 사례 추출
curl -s "http://api:8000/api/v1/metrics/recent?window=1h&type=fnr" | jq

# 모델 버전 확인 (롤백 후보)
curl -s http://api:8000/healthz | jq .model_version
```

**원인 분류**:
| 원인 | 진단 | 대응 |
|---|---|---|
| 신모델 배포 직후 | 배포 < 1일 + FNR 점프 | **즉시 롤백** ([§4](#4-모델-롤백)) |
| 입력 분포 변화 | 도메인 키워드 분포 급변 | 능동학습 큐 우선순위 ↑, 재학습 일정 앞당김 |
| RAG 인덱스 stale | guide 갱신 후 alias 미스왑 | [§5 인덱스 재구성](#5-인덱스-재구성) §5.3 alias 검증 |
| 추출 품질 저하 (HWP·PDF) | P4 메트릭 누락률 급증 | extractor 폴백 체인 확인 (rhwp → pyhwp → LibreOffice) |

### 2.4 P1 — 분류 지연 급증 (p95 > 30초)

| 원인 | 진단 | 대응 |
|---|---|---|
| ES kNN num_candidates 부족 | EsStore 로그 `num_candidates: 100` | 강필터 환경에선 `k*50` 자동 산정 ([es_store.py](../poc/src/lloydk/adapters/vectorstore/es_store.py)) |
| GPU 다른 워크로드 점유 | `nvidia-smi` | 학습 잡 중단 → 추론 GPU 확보 |
| Celery 워커 부족 | Redis `LLEN celery` 증가 | `docker compose up -d --scale worker=N` |
| ES JVM GC 빈도 | `_nodes/stats/jvm` | heap 증가 또는 `int8_hnsw` 양자화 |

### 2.5 P0/P1 사후 보고서 (RCA)

P0/P1 장애 종결 후 24시간 이내 다음 보고서 작성 (`reports/incidents/INC-YYYYMMDD-NNN.md`):

```markdown
# INC-2026MMDD-NNN

- 등급: P0/P1
- 발생: 2026-MM-DD HH:MM
- 종결: 2026-MM-DD HH:MM
- 영향: 영향 시간·사용자 수·실패 요청 수
- 근본 원인: ...
- 즉시 대응: ...
- 영구 조치: ...
- 재발 방지: doc/10 위험관리대장에 R-T/R-S로 등재
```

---

## 3. 모델 재학습

### 3.1 트리거 조건

다음 중 하나라도 충족 시 재학습 절차 시작:

- **능동학습 누적 ≥ 500건** (관리자 재라벨 확정본)
- **FNR 추세 ≥ 5%p 상승** (지난 4주 평균 대비)
- **KOIPA 가이드 개정** (FUN-002 신버전 등록)
- **등급체계 변경** (예: 4단계 → 5단계)
- **마지막 학습 후 6개월 경과** (정기 갱신)

### 3.2 재학습 절차

```
1. [데이터 준비]
   - PG: 능동학습 큐에서 confirmed 라벨 export → datasets/labeled_active_YYYYMMDD/
   - 기존 학습셋과 stratified split (재학습용 70%, 검증 15%, 테스트 15%)

2. [학습 실행]
   - poc/scripts/p1_train_classifier.py --mode full --epochs 5 \
       --train datasets/labeled_active_YYYYMMDD/train.jsonl
   - MLflow에 자동 등록 (run_name=v{N+1}-YYYYMMDD)

3. [검증]
   - hold-out test set으로 F1·FNR 측정
   - 합격선: F1-macro ≥ 0.75, FNR ≤ 5%
   - 기존 모델 대비 △Recall ≥ 0 (성능 회귀 금지)
   - 미달 시 학습 데이터·hyperparameter 재조정 후 재실행

4. [스테이징 배포]
   - MLflow Model Registry: stage=Staging
   - 운영 트래픽 10%만 신모델로 (Canary)
   - 1주 모니터링: FNR·지연·사용자 피드백

5. [Canary 결과 판정]
   - 합격: 100% 전환 → Production
   - 미달: 자동 롤백 → 학습 재실행

6. [Production 승격]
   - MLflow: stage=Production
   - 이전 모델: stage=Archived (3개월 보존 후 삭제)
   - 변경 로그 + 검증 리포트 → 발주처 보고
```

### 3.3 폐쇄망 환경 추가 절차

[doc/12 §7.2](12_폐쇄망_배포_설계.md) 참조. 외부망에서 학습 → 가중치 매체 반입 → 운영망 MLflow import.

---

## 4. 모델 롤백

### 4.1 롤백 트리거

- Canary 단계 FNR 합격선 미달
- Production 배포 후 24시간 내 P1 장애 발생
- 외부 보고된 보안·법적 이슈

### 4.2 즉시 롤백 절차 (5분 이내)

```bash
# 1. MLflow에서 직전 Production 모델 ID 확인
curl -s http://mlflow:5000/api/2.0/mlflow/registered-models/get \
  -d 'name=kf-deberta-classifier' | jq '.registered_model.latest_versions[] | select(.current_stage=="Archived") | .version' | head -1

# 2. 환경변수 또는 config로 모델 버전 강제 지정
docker compose exec api sh -c 'export MODEL_VERSION=v1.2.3 && supervisorctl restart api'

# 3. 헬스체크 + 분류 1건 테스트
curl http://api:8000/healthz | jq .model_version    # v1.2.3 확인
curl -X POST http://api:8000/api/v1/classify \
  -H 'X-API-Key: ...' -d '{"doc_id":"test","content":"테스트"}'

# 4. Grafana 알람 OFF 처리 + INC 보고서 시작
```

### 4.3 ES 인덱스 롤백 (alias 스위칭)

모델 교체와 별개로 RAG 인덱스도 롤백 필요한 경우:

```bash
# 현재 alias가 가리키는 인덱스 확인
curl -s http://elasticsearch:9200/_alias/secrets-guides-koipa | jq

# 이전 인덱스로 스위칭 (예: v2 → v1)
curl -X POST http://elasticsearch:9200/_aliases -H 'Content-Type: application/json' -d '{
  "actions": [
    {"remove": {"index": "secrets-guides-koipa-kure-v2", "alias": "secrets-guides-koipa"}},
    {"add":    {"index": "secrets-guides-koipa-kure-v1", "alias": "secrets-guides-koipa"}}
  ]
}'
```

→ `EsStore.swap_alias()` 메서드를 호출하는 운영 스크립트로 자동화 가능.

---

## 5. 인덱스 재구성

### 5.1 트리거

- 임베딩 모델 교체 (예: KURE-v1 → KURE-v2)
- 매핑 변경 (Nori 사용자 사전·HNSW 파라미터)
- 차원 변경 (KURE 1024 → ko-sroberta 768) — **별도 인덱스 필수**
- 스키마 마이그레이션

### 5.2 무중단 재구성 절차

```
1. [신규 인덱스 생성]
   PUT /secrets-guides-koipa-kure-v2
   { 매핑은 doc/13 §4.2 기준, dims 새 모델에 맞춤 }

2. [재인덱싱]
   - guide 청크 전체 → 새 모델로 임베딩 → _bulk
   - ES scroll+bulk 패턴 또는 ES reindex API (동일 클러스터 내)
   - 또는 GuideService를 통한 일괄 재업로드

3. [검증]
   - count 일치
   - 100건 sampling 검색 결과 비교 (이전 vs 신규)
   - 합격선: top-3 일치율 ≥ 90%

4. [alias 스위칭]
   POST /_aliases
   {"actions": [
     {"remove": {"index": "secrets-guides-koipa-kure-v1", "alias": "secrets-guides-koipa"}},
     {"add":    {"index": "secrets-guides-koipa-kure-v2", "alias": "secrets-guides-koipa"}}
   ]}

5. [모니터링]
   - 1주 운영 후 이전 인덱스 close (검색 불가, 저장만)
   - 1개월 후 delete
```

### 5.3 alias 정상성 검증

```bash
# 모든 alias가 운영 인덱스를 가리키는지
curl -s http://elasticsearch:9200/_cat/aliases?v

# 예상 출력:
# alias                       index                                  ...
# secrets-guides-koipa        secrets-guides-koipa-kure-v1          ...
# secrets-docs-koipa          secrets-docs-koipa-kure-v1            ...
# secrets-synth-koipa         secrets-synth-koipa-kure-v1           ...
```

---

## 6. 백업·복원

### 6.1 백업 대상·주기

| 대상 | 주기 | 보존 | 위치 |
|---|---|---|---|
| Postgres (메타·라벨·이력) | 일 1회 (자정) | 30일 | MinIO `lloydk-backup/pg/` |
| Elasticsearch 스냅샷 | 일 1회 | 30일 | S3 또는 NFS (E6 회신) |
| MinIO 객체 (모델·문서) | 주 1회 | 90일 | 별도 NAS |
| MLflow Artifacts | 일 1회 | 1년 | MinIO `mlflow/` 통째 |
| `userdict_ko.txt` | 변경 시 | 영구 (Git) | 운영망 내 Git 미러 |
| 설정 (docker-compose·.env) | 변경 시 | 영구 | 운영망 내 Git 미러 |

### 6.2 Postgres 백업 절차

**자동화 스크립트**: [`scripts/backup_postgres.py`](../poc/scripts/backup_postgres.py)

```bash
# 일별 자동 백업 + MinIO 적재 + 30일 retention 정리
python poc/scripts/backup_postgres.py --upload

# cron 등록 (운영망)
0 2 * * * cd /opt/lloydk/poc && /usr/bin/python scripts/backup_postgres.py --upload

# 수동 복원
docker compose exec postgres pg_restore -U lloydk -d lloydk -c /tmp/lloydk-YYYYMMDD.dump
```

### 6.3 Elasticsearch 스냅샷 절차

**자동화 스크립트**: [`scripts/backup_es_snapshot.py`](../poc/scripts/backup_es_snapshot.py)

```bash
# 저장소 자동 등록 (미존재 시) + 일일 스냅샷 + retention 정리
python poc/scripts/backup_es_snapshot.py

# 저장소만 등록 (초기 설정)
python poc/scripts/backup_es_snapshot.py --ensure-only

# cron 등록 (운영망)
30 2 * * * cd /opt/lloydk/poc && /usr/bin/python scripts/backup_es_snapshot.py

# 수동 복원 (운영에서는 별도 스크립트 작성 권장)
curl -X POST http://elasticsearch:9200/_snapshot/lloydk_repo/snap-YYYYMMDD-HHMMSS/_restore \
  -H 'Content-Type: application/json' \
  -d '{"indices": "secrets-guides-koipa-*", "rename_pattern": "(.+)", "rename_replacement": "restored_$1"}'
```

**전제**: 운영 ES에 `repository-s3` 플러그인 설치 + MinIO 버킷 `lloydk-es-snapshots` 사전 생성.

### 6.4 재해 복구 (DR) 시나리오

**시나리오: 운영망 전체 다운 (디스크 손상)**

```
1. 새 호스트 준비 (docker compose + GPU + 메모리)
2. 가장 가까운 폐쇄망 번들 ([doc/12](12_폐쇄망_배포_설계.md)) install.sh 실행
3. Postgres 복원:
   - 가장 최근 pg_dump → pg_restore
4. ES 복원:
   - 스냅샷 저장소 등록 → /_snapshot/.../snap-YYYYMMDD/_restore
5. MinIO 복원:
   - 백업 NAS에서 poc/scripts/backup_minio_mirror.py 역방향 또는 mc mirror
6. MLflow 복원:
   - artifacts MinIO에서 자동 (DB만 복원되면 OK)
7. 모델 로드 + verify_infra.py 전체 GREEN 확인
8. 트래픽 점진 전환 (10% → 50% → 100%)
```

**RTO/RPO 목표 + 자동 검증**:
- RTO (복구 시간): **4시간 이내**
- RPO (데이터 손실): **1일 이내** (백업 주기)
- **일일 자동 검증**: [`scripts/dr_restore_check.py`](../poc/scripts/dr_restore_check.py)

```bash
# 매일 백업 후 즉시 실행 — RTO·RPO 준수 여부 자동 검증
python poc/scripts/dr_restore_check.py

# JSON 리포트는 reports/dr/dr_check_YYYYMMDD-HHMMSS.json에 적재
# 실패 항목 있으면 exit 1 → Grafana 알람과 연동

# cron 등록 (백업 완료 후)
0 3 * * * cd /opt/lloydk/poc && /usr/bin/python scripts/dr_restore_check.py || alert-cmd
```

검증 항목:
- `pg_backup_recency`: 최신 pg_dump 24h 이내 존재
- `es_snapshot_recency`: 최신 ES snapshot 24h 이내 존재
- `minio_mirror_recency`: MinIO mirror 7일 이내 갱신
- `infra_health`: PG·ES·MinIO·Redis 모두 응답 GREEN

---

## 7. 보안 사고 대응

### 7.1 등급별 분류 오류 (고등급 → 저등급 미탐)

```
증상: 1급 비밀 문서가 3급 공개로 분류됨
```

**즉시 대응**:
1. 해당 문서 ID 격리 (`/classify/lock?doc_id=...`)
2. 영향 범위 확인: 같은 모델·같은 RAG 인덱스로 분류된 최근 100건 재추론
3. 발주처 보안팀 즉시 보고
4. 원인 분석:
   - 모델 버전 (롤백 후보)
   - RAG 인덱스 stale 여부
   - 사용자 사전 누락 (도메인 용어 분해 오류)
5. 임시 조치: 해당 doc_type 범주는 분류 신뢰도 임계 ↑ 또는 수동 검수 강제
6. 영구 조치: 능동학습 큐 최우선 → 재학습 → 배포

### 7.2 데이터 유출 의심

```
증상: 외부 IP에서 비정상 다량 분류 요청
```

1. ES audit log + API 접근 로그 확인
2. 해당 API Key 즉시 폐기 (`POST /api/v1/auth/revoke`)
3. 발주처 보안팀 + KL 통합 보안 + Lloydk PM 동시 호출
4. 영향 범위 산출 → 발주처 보고

---

## 8. PSH 알림 대응 절차 (W10·W11 신규)

PSH(Performance Scenario Harness)는 매 PR/main push 시 `poc-ci.yml`의 `test` 잡에서 `scripts/run_perf_scenarios.py --mode dryrun --fail-on-miss --regression-threshold 30 --fail-on-regression`을 실행합니다. KPI 합격선 미달 또는 직전 회차 대비 30% 이상 회귀 시 **CI exit 1**로 PR 머지가 차단됩니다.

본 §8은 PSH FAIL/회귀 알림을 받았을 때 운영팀(Lloydk AI 엔지니어·KL DevOps)이 무엇을 보고 어떻게 대응할지 정의합니다.

### 8.1 알림 수신 시 1차 행동 (30초 이내)

PSH FAIL/회귀 알림은 다음 3채널 중 하나로 도착합니다:

| 채널 | 위치 | 용도 |
|---|---|---|
| **PR 코멘트** | 해당 PR 하단에 자동 게시 (`actions/github-script@v7`) | KPI PASS/FAIL/SKIP 요약 + 회귀 표 + FAIL KPI 목록 |
| **GitHub Pages** | `https://forfre12-ops.github.io/rag/` (main 푸시만) | doc/20 HTML 보고서 + perf JSON 누적 (`/perf/*.json`) |
| **CI artifact** | Actions run → `scenario-perf-report-{sha}` (90일 보관) | doc/20 HTML + `poc/reports/perf/perf_{ts}_{sha}.json` 전체 |

**30초 안에 다음 3개만 확인하세요**:

```
1. PR 코멘트의 "FAIL KPIs" 표 → 어느 KPI(S1.x·S5.x·S9.x 등) 떨어졌나
2. "회귀 탐지 (≥30%)" 표 → 측정값이 직전 회차 대비 ↑/↓ 얼마나 변했나
3. PR 코멘트 헤더의 Commit SHA → 직전 main 대비 어떤 변경이 있었나 (git log)
```

→ 1번이 핵심 KPI(S1.4 FNR·S1.3 F1·S5.4 Recall@5)면 즉시 §8.4 롤백 검토. 그 외는 §8.2 매핑 표로 책임 모듈 식별 후 디버깅.

### 8.2 KPI별 책임 모듈 매핑

PSH KPI는 시나리오 단위로 정의되며, FAIL 시 책임 모듈로 즉시 진입할 수 있도록 다음 매핑을 유지합니다.

| 시나리오 | KPI | 1차 책임 모듈 | 2차 책임 (의존) | 디버그 명령 |
|---|---|---|---|---|
| **S1.1~S1.2** p50·p95 latency | latency | [`m5_inference`](../poc/src/lloydk/modules/m5_inference/) | api/routes/classify, services/classify | `pytest tests/test_classify_router.py -v` |
| **S1.3** F1-macro | 정확도 (trained_model) | [`m4_training`](../poc/src/lloydk/modules/m4_training/) | datasets/labeled, MLflow 모델 등록 | `make p1-full` (full 모드) |
| **S1.4** FNR (TS→하위) | 미탐율 (trained_model) | [`m3_labeling`](../poc/src/lloydk/modules/m3_labeling/) + [`m4_training`](../poc/src/lloydk/modules/m4_training/) | seeds.py v3·Nori 사전 | seeds.py 키워드 누락 확인 → `seed_keywords.py` |
| **S1.5** 응답 스키마 정합 | bool | api/schemas/classify.py | router 응답 직렬화 | `pytest tests/test_classify_router.py::test_schema -v` |
| **S2.1~S2.3** async/batch | latency·throughput·bool | api/routes/classify_async, [`workers/`](../poc/src/lloydk/workers/) | Redis Celery 큐 | docker compose logs worker |
| **S3.1~S3.4** confirm·relabel | latency·정합 | api/routes/confirm, [`repositories/corrections.py`](../poc/src/lloydk/repositories/) | PG corrections 테이블 | `pytest tests/test_corrections_repo.py -v` |
| **S4.1~S4.4** schema/grades | bool·latency | api/routes/schema_grades, services/schema_grades | PG schema_versions | `pytest tests/test_schema_grades.py -v` |
| **S5.1~S5.3** 가이드 업로드·인덱싱 | latency·count·throughput | [`m4_training/rag_indexer.py`](../poc/src/lloydk/modules/m4_training/rag_indexer.py), services/guide | adapters/embedding (KURE·hash), adapters/vectorstore (es_store·inmemory) | `make migrate-dry` |
| **S5.4** Recall@5 | 비율 (es + trained_model) | [`m5_inference/rag_search.py`](../poc/src/lloydk/modules/m5_inference/), adapters/vectorstore/es_store | ES 인덱스 매핑·KURE-v1 가중치 | `pytest tests/test_rag_es.py -v` |
| **S5.5** GET 가이드 200 | bool | api/routes/guide, services/guide | PG guides 테이블 | `pytest tests/test_guide_router.py -v` |
| **S6.1~S6.5** 합성 플로우 | latency·라벨일치·USD·bool | [`m1_synthesis`](../poc/src/lloydk/modules/m1_synthesis/), services/synth | adapters/llm (anthropic·openai·noop·vllm·ollama) | `make p3` |
| **S7.1~S7.3** URGENT_RETRAIN | bool·latency (PG) | [`m6_evaluation/active_learning.py`](../poc/src/lloydk/modules/m6_evaluation/), api/routes/train | PG corrections threshold | `pytest tests/test_active_learning.py -v` |
| **S8.1~S8.4** metrics·CM | bool·latency (PG) | api/routes/metrics, services/metrics | MLflow 클라이언트 (`metrics router`) | `pytest tests/test_metrics_router.py -v` |
| **S9.1~S9.3** 적대적 FNR | 비율 | [`m3_labeling`](../poc/src/lloydk/modules/m3_labeling/) + [`m5_inference`](../poc/src/lloydk/modules/m5_inference/) | seeds.py·Nori 사전·변형 일관성 | seeds.py 시드 보강 |
| **S10.1~S10.3** RAG 인용 충실도 | grounded_ratio | [`m5_inference/rag_search.py`](../poc/src/lloydk/modules/m5_inference/), services/classify (evidence) | adapters/vectorstore, evidence 절단 로직 | `pytest tests/test_rag_grounded.py -v` |
| **S11.1~S11.3** 부하 | error_rate·latency·throughput | api/middleware, ASGI worker pool | Redis Celery, Postgres connection pool | docker compose up -d --scale worker=N |
| **S13.1~S13.3** 테넌트 격리 | count·비율·bool | services/guide·classify (tenant_id 필터), adapters/vectorstore | audit_log 미들웨어 | `pytest tests/test_tenant_isolation.py -v` |
| **S16.1~S16.4** 권한·인증 거부 | bool·latency | [`api/_auth.py`](../poc/src/lloydk/api/_auth.py) | X-API-Key·X-Actor-Role 헤더 검증 | `pytest tests/test_auth.py -v` |
| **S17.1~S17.3** 감사 로그 무결성 | 비율·bool (PG) | [`repositories/audit.py`](../poc/src/lloydk/repositories/), audit 미들웨어 | PG audit_log 테이블 | `pytest tests/test_audit_repo.py -v` |
| **S18.1~S18.3** 폐쇄망 번들 | bool·ms | [`scripts/build_offline_bundle.py`](../poc/scripts/build_offline_bundle.py) | manifest yaml/json/CHECKSUMS 산출 | `make bundle-dry` |

**규칙**: 동일 시나리오의 KPI가 동시에 여러 개 FAIL이면 1차 책임 모듈부터 진입. 단일 KPI FAIL이면 PR diff에서 해당 모듈 변경 여부 우선 확인.

### 8.3 회귀 ≥30% 발견 시 대응 분기

PSH는 `compare=le`(latency·FNR) KPI 측정값이 증가하거나 `compare=ge`(recall·throughput) KPI 측정값이 감소하면 회귀로 판정합니다.

| 회귀 폭 | 대상 KPI | 대응 |
|---|---|---|
| **30~50% 단일 KPI** | 비핵심 (S1.1·S1.5·S2.1·S3.1·S3.2 등 latency 계열) | 코드 디버깅 후 PR 보완 — 머지 차단 유지 |
| **30~50% 핵심 KPI** | S1.4 FNR · S1.3 F1 · S5.4 Recall@5 · S9.2 적대적 FNR · S13.1 교차 노출 | 즉시 §8.4 롤백 기준 평가 |
| **≥50% 단일 KPI (어떤 시나리오든)** | — | 머지 차단 + Lloydk AI 엔지니어 즉시 호출 |
| **≥30% 동시 다발 (3개 이상 KPI)** | — | 머지 차단 + §8.4 롤백 기준 평가 |

### 8.4 롤백 기준 (직전 main commit으로 revert)

다음 중 하나라도 충족 시 직전 main commit으로 즉시 revert PR 제출:

1. **핵심 KPI 회귀 ≥30%**: S1.4 FNR(↑), S1.3 F1(↓), S5.4 Recall@5(↓), S9.2 적대적 FNR(↑), S13.1 교차 노출(↑)
2. **다발 회귀 ≥3개 KPI** + 원인 모듈 1시간 내 식별 불가
3. **S11.1 error_rate >10%** (운영 부하 시나리오에서 5xx 폭증)
4. **S18.1·S18.2 번들 무결성 FAIL** + 폐쇄망 반입 일정 임박

**revert 명령**:
```bash
# 직전 main commit으로 revert PR
git checkout main
git pull
git revert HEAD --no-edit
git push origin main
# 또는 PR 통해 머지
gh pr create --title "revert: PSH 회귀로 main 직전 commit 복귀" --body "PSH report: <artifact URL>"
```

→ revert 후 5분 내 PSH 재실행 GREEN 확인 → §2.5 RCA 보고서 작성.

### 8.5 False positive 식별 가이드

dryrun 환경에선 일부 KPI가 본질적으로 변동성이 큽니다. 단발성 FAIL을 무조건 회귀로 보지 말고 다음 패턴을 점검하세요.

| KPI | 변동 원인 | False positive 판단 기준 | 권장 행동 |
|---|---|---|---|
| **S1.1 p50 latency** | dryrun in-process 호출, GitHub Actions runner 부하 편차 ±30% | 직전 5회 평균 대비 표준편차 1σ 이내 + 합격선(≤500ms) 통과 | 동일 PR 재실행 (re-run job) |
| **S5.1 업로드 latency p95** | hash 임베딩 + InMemory cold start 영향 | 첫 회차 1~2회 warmup 시간이 포함된 경우 | re-run 후에도 FAIL이면 진짜 회귀 |
| **S5.3 인덱싱 throughput** | dryrun ≥0.3 chunks/s는 너무 보수적 (doc/20a §5.5 임계 갭) | full 모드 임계 ≥5 적용 후 통과 시 dryrun 임계 갭 이슈 | doc/20a §5.5 mode-aware threshold 결정 가속 |
| **S11.2 부하 p95 latency** | GitHub Actions runner CPU 격차로 ±50% 흔함 | 동일 PR re-run 시 다른 값 → runner 의존 | re-run 2~3회 평균으로 판정 |
| **S2.2 batch throughput** | in-process 실행이라 50~200 docs/s 사이 변동 | 어쨌든 ≥5 합격선은 통과 | 합격선 통과 + 회귀만 ≥30% → 동일 PR 재실행 |
| **S6.2 라벨 일치도** | noop provider 템플릿 기반 — 룰 라벨러 키워드 시드 동기화 안 되면 100%→90% 흔들림 | seeds.py 변경 없는데 회귀 발생 → noop 템플릿 시드 검토 | `poc/src/lloydk/adapters/llm/noop_provider.py` 시드 정렬 확인 |

**규칙**:
- 단일 KPI 단발성 FAIL → 동일 PR re-run 1회 시도 (Actions UI의 "Re-run failed jobs")
- 2회 연속 동일 KPI FAIL → false positive 아님, 진짜 회귀
- **핵심 KPI(S1.3·S1.4·S5.4·S9.2·S13.1)는 false positive 가정 금지** — 1회 FAIL도 즉시 §8.4 평가

### 8.6 PSH 자체 장애 (CI 인프라 이슈)

PSH 코드 자체가 ERROR(시나리오 함수 예외)나 import 실패로 죽는 경우:

```bash
# 로컬 재현
cd poc
pip install -e ".[dev]"
python scripts/run_perf_scenarios.py --mode dryrun --no-probe -v

# 회귀 비교 비활성 + KPI 미달만 검사
python scripts/run_perf_scenarios.py --mode dryrun --fail-on-miss --no-regression-check
```

→ PSH 코드 결함은 일반 PR 흐름으로 수정 (revert 대상 아님). `poc/src/lloydk/perf/` 변경 PR은 별도 detail review.

---

## 9. 회신 의존 절차 (v1.0 확정 대기)

다음 항목은 KL E1~E9 회신 후 확정:

| 절 | 회신 변수 | 확정 사항 |
|---|---|---|
| §6.3 스냅샷 저장소 경로 | E6 | S3 endpoint or NFS mount |
| §6 백업 retention 정책 | E8 | docs / synth / audit 각 보존 기간 |
| §7 보안 사고 보고 채널 | K1 인증 시스템 | LDAP/SSO 통합 시 사용자 추적 강화 |
| §1 모니터링 통합 | K1 KL 인프라 | Grafana vs KL 측 SIEM 통합 |
| §2.5 RCA 보고 양식 | 발주처 표준 | 보호원 PMS 양식 vs 본 양식 |

---

## 10. 정기 검토 일정

| 주기 | 검토 항목 |
|---|---|
| 주 1회 | §1 일상 점검 결과 리뷰 미팅 (KL+Lloydk 30분) |
| 월 1회 | §3 재학습 트리거 평가 + §4 모델 성능 추세 |
| 분기 1회 | §6 백업 복원 훈련 (실제 staging 복원 1회) + §1 디스크 증가 추세 |
| 반기 1회 | 전체 Runbook 갱신 + 신규 시나리오 추가 |
| 연 1회 | DR 훈련 (운영망 시뮬레이션 다운 → 복구) + 라이선스 갱신 검토 ([doc/14](14_OSS_라이선스_보고서.md)) |

---

## 11. 비상 연락망 (회신 후 채움)

| 역할 | 담당자 | 연락처 | 1차 대응 시간대 |
|---|---|---|---|
| Lloydk PM | ___ | ___ | 평일 9~18시 |
| Lloydk AI 엔지니어 | ___ | ___ | on-call (24/7) |
| KL 개발 PM | ___ (K4 회신) | ___ | 평일 9~18시 |
| KL DevOps | ___ | ___ | on-call |
| KOIPA 보안팀 | ___ | ___ | 평일 9~18시 |
| 클라우드 GPU 임대 (R-Q1 c 시나리오) | AWS Korea / Lambda Labs | ___ | 24/7 |

---

## 12. 결정 사항 요약 (v1.1)

1. **6개 핵심 시나리오** 절차화 — 장애·재학습·롤백·인덱스 재구성·백업·보안
2. **P0~P3 4단계 장애 등급** + 대응 시간 SLA 명시
3. **재학습 트리거 5가지** + Canary 10% → 100% 점진 배포
4. **alias 스위칭 기반 무중단 인덱스 재구성**
5. **백업 주기**: PG/ES 일 1회, MinIO 주 1회, retention 30~365일
6. **RTO 4시간 / RPO 1일** 재해 복구 목표
7. **PSH 알림 대응 절차** (§8 신규) — 30초 1차 행동 · KPI×모듈 매핑 19종 · 회귀 30%/50% 분기 · 핵심 KPI 5종 false positive 가정 금지 · revert 기준
8. **정기 검토**: 주/월/분기/반기/연 — 빈도별 점검 표준화

본 Runbook은 회신 후 v1.0으로 확정. v1.1은 W10·W11 PSH 도입에 맞춰 §8 신규. **연 1회 갱신 + 큰 사건 후 즉시 갱신 + PSH 시나리오 추가 시 §8.2 매핑 표 동기화**.
