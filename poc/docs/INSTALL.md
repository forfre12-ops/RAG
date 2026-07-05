# Lloydk AI 폐쇄망 설치 절차서 (INSTALL)

대상: KOIPA 영업비밀관리시스템 운영망(에어갭) · 번들 `lloydk-airgap-bundle`
짝 문서: 설계·인벤토리·책임경계는 `폐쇄망_설치_배포_설계서`, 운영 절차는 `운영_런북` 참조.

> 이 문서는 **운영자가 그대로 따라 치는 단계별 절차**다. 모든 명령은 번들 루트(`lloydk-airgap-bundle/`)에서 실행한다. 폐쇄망 전용 compose는 `infra-config/docker-compose.airgap.yml`(이미지 참조·beat 포함·named 볼륨)를 사용한다.

---

## 0. 사전 요건 (설치 전 확인)

| 항목 | 확인 명령 / 기준 |
|---|---|
| OS | Ubuntu 22.04 LTS |
| Docker | `docker version` (Engine 24+), `docker compose version` (v2) |
| GPU | `nvidia-smi` 인식 + `docker run --rm --gpus all nvidia/cuda:12.4.0-base nvidia-smi` 동작 |
| 커널 | `sysctl vm.max_map_count` ≥ 262144 (미만 시 `sudo sysctl -w vm.max_map_count=262144`) |
| 디스크 | Data SSD 여유 ≥ 70GB (번들 ~23GB + 관측성 이미지 ~2GB + 적재 + 데이터/메트릭 볼륨) |
| 포트 | 5432·6379·8000 (+ mTLS 443, + 관측성 9090·9093·3000·3100) 내부 가용 |

GPU가 없으면: `.env`에서 `POC_MODE`를 유지하되 임베딩 CPU 모드로 두고, compose의 `deploy.resources...devices`(nvidia) 블록을 주석 처리한다. 추론 성능이 40~60% 하락한다.

---

## 1. 반입 · 무결성 검증

```bash
cd lloydk-airgap-bundle
bash verify.sh                 # CHECKSUMS.sha256 대조 → "Checksums OK"
```

---

## 2. 이미지 적재 + (호스트) 의존성

```bash
bash install.sh                # docker images 적재 + (호스트 실행용) wheel·OCR 설치
docker images | grep -E 'lloydk|postgres|redis|nginx'   # 적재 확인
```

- 컨테이너 배포(본 절차)에서는 의존성·OCR이 **이미지에 이미 포함**되어 별도 설치가 불필요하다. `install.sh`의 wheel/OCR 단계는 호스트에서 스크립트를 직접 구동할 때만 의미가 있다.
- **torch**: GPU 환경에 맞는 휠은 이미지에 포함된다. 호스트 직접 실행 시에만 별도 설치:
  `pip install --no-index --find-links=python-deps/wheels torch-*.whl`
- **문서 파싱 선택 의존성**: HWP 표 셀은 `.[hwp-tables]`/`hwp5html`, PDF 표 행열은
  `.[pdf-tables]`/`pdfplumber`, Office 내부 이미지 OCR은 `.[ocr]`/Tesseract가 있어야 구조화된다.
  미설치 시 API는 추출을 계속하되 `parse.warnings`와 `/healthz/deep` extractor probe에 누락 사유를 표시한다.

---

## 3. 모델 · 플러그인 배치

폐쇄망은 런타임 다운로드가 불가하므로 **모델 가중치를 매체에서 미리 배치**한다.

```
bundle/models/
├── classifier-trained/      # 분류기 학습 가중치 + temperature.json (CLASSIFIER_MODEL_DIR)
└── hf/                      # HuggingFace 캐시 (KURE-v1, bge-m3 등 — HF_HOME 이 가리킴)
```

- `classifier-trained/`에 `temperature.json`이 없으면 서빙이 T=1.0(무보정)으로 동작 → 과신 위험.
- `hf/`에 임베딩 모델이 없으면 `HF_HUB_OFFLINE=1` 때문에 **기동 시 실패**(의도된 fail-loud). 빌드 호스트에서 `huggingface-cli download` 또는 `scripts/cache_kure_v1.py`로 미리 채워 반입한다.
- 벡터검색은 Postgres `pgvector`(dense) + bigram-tsvector `ts_rank`(어휘) 하이브리드로 통합 — 별도 검색엔진/Nori 플러그인 불요(의사결정_대장 §03 ⓑ). postgres 이미지는 `pgvector/pgvector:pg16`.

---

## 4. `.env` 작성

```bash
cp infra-config/.env.template .env
```

최소 필수값(onprem-local):

| 키 | 값 | 비고 |
|---|---|---|
| `IMAGE_TAG` | `1.0.0-rc1` | docker load 로 복원된 이미지 태그와 일치 |
| `POSTGRES_PASSWORD` | `<강한 값>` | **기본값 교체 필수** |
| `API_KEY` | `<강한 값>` | X-API-Key 인증 |
| `VECTOR_BACKEND` | `pg` | pgvector dense + bigram-tsvector ts_rank 하이브리드 |
| `POC_MODE` | `full` | 실모델 로드 (dryrun=mock) |
| `LLM_PROVIDER` | `vllm` | 로컬 LLM (또는 `ollama`) |
| `LOCAL_LLM_BASE_URL` | `http://<llm-host>:8001/v1` | §8 서버 endpoint |
| `LOCAL_LLM_MODEL` | `Qwen/Qwen3-14B` | |
| `EMBEDDING_MODEL` | `nlpai-lab/KURE-v1` | |
| `CLASSIFIER_MODEL_DIR` | `/models/classifier-trained` | 컨테이너 내 경로(모델 마운트) |

DB·Redis 엔드포인트는 compose가 컨테이너 네트워크 기준으로 자동 주입하므로 `.env`에 둘 필요 없다. 폐쇄망 번들은 원문·산출물을 로컬 파일시스템 볼륨에 저장하므로 MinIO·MLflow 엔드포인트를 설정하지 않는다.

---

## 5. 인프라 기동

```bash
export COMPOSE="docker compose --env-file .env -f infra-config/docker-compose.airgap.yml"
$COMPOSE up -d postgres redis
$COMPOSE ps        # postgres healthy 까지 대기 (~30s)
```

---

## 6. DB 마이그레이션 (19테이블 + 파티션 백필)

```bash
$COMPOSE run --rm api alembic upgrade head
```

- api 이미지에 `alembic.ini`·`alembic/`이 포함되어 컨테이너 내에서 그대로 실행된다.
- 신규 DB: 한 번으로 baseline + 후속 revision(핫패스 인덱스, 13개월 월 파티션 백필) 전체 적용.

---

## 7. 스토리지 · 검색 인덱스 초기화

- 스토리지: 별도 초기화 불요. `docker-compose.airgap.yml`의 `storagedata` named volume이 `/app/.storage`에 마운트되며, 원본은 설정에 따라 AES-256-GCM으로 암호화 저장된다.
- 벡터검색: 별도 초기화 불요. `tb_rag_vectors`(pgvector dense + bigram tsvector `ts_rank`)는 §6 `alembic upgrade`가 생성한다(vector 확장 포함). 가이드/문서 색인은 앱이 적재 시 자동 채움.
- 검색엔진(ES)·Nori 분석기·인덱스 템플릿 제거(의사결정_대장 §03 ⓑ) — pgvector + bigram-tsvector `ts_rank` 하이브리드로 통합. postgres 이미지(`pgvector/pgvector:pg16`)에 vector 확장 포함.

---

## 8. 로컬 LLM 서버 기동 (onprem-local)

별도 프로세스로 OpenAI 호환 endpoint를 띄운다(예: vLLM).

```bash
python -m vllm.entrypoints.openai.api_server \
       --model /models/hf/Qwen-Qwen3-14B --port 8001 --served-model-name Qwen/Qwen3-14B
#  또는: ollama serve  &&  ollama run qwen3:14b
curl -s http://localhost:8001/v1/models     # endpoint 확인 → .env LOCAL_LLM_BASE_URL 와 일치
```

---

## 9. 애플리케이션 기동 (api · worker · beat)

```bash
$COMPOSE up -d api worker beat
# (옵션) mTLS: $COMPOSE --profile mtls up -d nginx-mtls   # 인증서는 ./mtls/ 에 사전 배치
$COMPOSE ps
```

- **worker**는 `-Q classify,index,synthesis,learning,celery` 전큐를 구독해야 한다(compose에 반영됨).
- **beat**는 단일 인스턴스만 — drift·auto-rollback·outbox·파티션 롤오버 발행기. 누락 시 자동화가 전혀 동작하지 않는다.

---

## 10. 설치 검증

```bash
curl -s http://localhost:8000/api/v1/healthz/ready    # 의존성 실측 → 200 (503이면 미준비)
$COMPOSE exec api python scripts/verify_infra.py        # 인프라 일괄 점검

# 스모크 분류 (X-API-Key 필요)
curl -s -X POST http://localhost:8000/api/v1/classify \
     -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
     -d '{"doc_id":"smoke-1","content":"본 문서는 당사의 반도체 공정 영업비밀을 포함한다"}'
```

기대: 등급(TS/S1/S2/S3) + confidence 반환, `/ready` 200.

### 10.1 인수(acceptance) 샘플팩 검증 — 권장

번들에 동봉된 **인수 샘플팩**(전 포맷 TXT/PDF/DOCX/XLSX/XLS/PPTX/HWPX × 등급, 공개+합성 혼합)을
배포 API 에 올려 **파서·분류·안전 게이트**를 한 번에 검증한다. 판정 규율: 정확 등급일치가 아니라
**(1) 파싱 성공 + (2) 고등급 미탐 없음(severity floor)** — 서빙은 의도적으로 안전방향 과분류라 정확도로
합격/불합격하지 않는다.

```bash
# 번들 루트에서 (호스트, python 불요 — bash+curl. python3 있으면 JSON 정밀 파싱)
API_KEY="$API_KEY" BASE_URL=http://localhost:8000 bash acceptance/run_acceptance.sh
```

기대: `[acceptance] PASS: N docs, 0 veto`. `UNDER!`(고등급 미탐)·파싱실패가 1건이라도 있으면 **FAIL** —
`/healthz/deep` 로 원인(모델 미공급·보정 T=1.0·파서 extra 누락) 확인. 개발/lite 환경(레포 보유)에서는
`make acceptance-test`(in-proc, 숫자 무손실까지 전수 검증)로도 확인 가능.

---

## 10.5 관측성 스택 기동 (권장 — 안전 알림 소비자)

분류기·감사체인·서빙 게이트의 안전 신호(FNR 급증·감사체인 파손·킬게이트 발동·rule-fallback 서빙 등 알림 29종)는 **Prometheus/Grafana가 떠 있어야 소비**된다. 이 스택이 없으면 API의 `/api/v1/metrics-prom`은 노출되지만 아무도 스크랩·경보하지 않는다("안전하게 틀리고 빨리 배운다"의 관측 절반이 빈다).

번들에는 관측성 설정과 이미지가 `observability/`·`docker-images/obs-*.tar`로 동봉된다(빌드 시 `--skip-observability`로 제외 가능 — 그 경우 이 절 생략).

```bash
# 메인 스택(§9)이 먼저 떠서 lloydk-airgap_default 네트워크가 존재해야 한다.
export OBS="docker compose --env-file .env -f observability/docker-compose.observability.airgap.yml"
$OBS up -d
$OBS ps                                        # prometheus·grafana·loki·exporters healthy
curl -s http://localhost:9090/-/ready          # Prometheus ready
```

- **발화 알림 확인**: `http://<host>:9090/alerts` — `alert_rules.yml`의 규칙이 로드·평가된다(airgap overlay가 `alert_rules.yml`을 마운트하도록 교정됨. dev overlay는 이 마운트가 없어 규칙이 로드되지 않았다).
- **대시보드**: `http://<host>:3000` (Grafana, `.env`의 `GRAFANA_PASSWORD` 필수) — KPI·overview 대시보드 자동 프로비저닝.
- **통지 라우팅**: `alertmanager` 서비스가 overlay에 포함되어 Prometheus 발화 알림을 그룹·중복제거해 라우팅한다(`http://<host>:9093` Alertmanager UI). 폐쇄망 통지 채널은 환경마다 달라 기본은 미설정 상태 — 알림은 Alertmanager UI에 그룹핑되어 보이되 외부로 push되지 않는다. 사내 채널이 결정되면 `observability/alertmanager.yml`의 `receivers`에 `email_configs`(사내 SMTP) 또는 `webhook_configs`(메신저)를 추가하면 즉시 능동 통지된다.

---

## 11. 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `/ready` 503 | 의존성 미기동 | `$COMPOSE ps`로 postgres healthy 확인 (pgvector 이미지) |
| 기동 시 모델 다운로드 실패 | `HF_HUB_OFFLINE=1` + 모델 미배치 | `models/hf/`에 모델 캐시 반입(§3) |
| 분류 confidence 비정상 | `temperature.json` 부재 | 보정값 동봉 후 모델 재배치 |
| 큐 작업 미소비 | worker `-Q` 누락 / beat 미기동 | §9대로 worker 전큐 구독 + beat 단일 기동 |
| 마이그레이션 `alembic: not found` | 구버전 이미지(alembic 미동봉) | 최신 api 이미지 사용(Dockerfile에 alembic 포함) |
| 관측성 `network lloydk-airgap_default not found` | 메인 스택 미기동 | §9 먼저 실행 후 §10.5 관측성 up |
| Prometheus `/alerts` 비어있음 | 규칙 미로드 | airgap overlay 사용 확인(`alert_rules.yml` 마운트) — dev overlay는 마운트 없음 |
| 관측성 이미지 `no such image` | 빌드 시 obs 이미지 미저장(WARN) | 외부망서 pull 후 재빌드 또는 `docker-images/obs-*.tar` 수동 동봉 |

> 참고: Docker Desktop(WSL2)의 단일파일 마운트·136MB 바인드 디바이스 트랩은 **개발 PC 한정** 이슈이며, Linux 운영 서버(named volume)에는 해당하지 않는다.

---

## 부록 — 책임 경계 (R&R) 요약

- **발주처/KL 준비**: 서버(A100 80GB×2·Ubuntu 22.04)·폐쇄망·GPU 드라이버/CUDA/Container Toolkit·Docker·매체 반입 승인
- **로이드케이 수행**: §1~§10 전 과정 (반입·적재·구성·마이그레이션·초기화·기동·검증)
