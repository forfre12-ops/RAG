# 클라우드 배포·테스트 런북 (lite-cloud tier)

대상: CSAP 개발/스테이징 클라우드 VM에 배포 후 기능 테스트. **폐쇄망 운영(onprem-local)과 별개 tier**
(안전게이트=warn·저장암호화 OFF·GPU 없음·외부 LLM API). 하드닝 운영 배포는 `EXPORT_IMPORT_RUNBOOK.md` 참조.

전제: Docker + Docker Compose. 인터넷(이미지 빌드·외부 LLM API) 가용. GPU 불필요.

---

## 0. 왜 이 런북 — 코드는 배포 가능, 실 blocker는 "설정·프로비저닝"

`poc_mode=full`(lite-cloud 기본)은 startup 에서 자격증명·게이트를 **fail-fast** 검증한다. 아래를
안 채우면 부팅이 거부된다(무음 성공 방지). 순서대로 따르면 반나절 내 배포·스모크까지 된다.

---

## 1. 환경파일 준비 (BLOCKER A2)

`.env.lite-cloud` 를 복사해 실값을 채운다:

```bash
cp .env.lite-cloud .env.cloud
```

반드시 실제값으로 교체(placeholder면 부팅 거부):
- `API_KEY` — 관리자 API 키
- `ANTHROPIC_API_KEY` (또는 `OPENAI_API_KEY`) — 외부 LLM
- `LLOYDK_AUDIT_CHAIN_SECRET` — `openssl rand -hex 32` 로 생성 (NFR-SEC-01, 미설정=부팅 거부)
- `CORS_ALLOW_ORIGINS` — 포털/프런트 origin 명시 (`["*"]` 은 부팅 거부)
- `MINIO_SECRET_KEY` — 객체스토어 시크릿 (lite-cloud 는 storage_backend=minio)
- `CLASSIFIER_MODEL_DIR` — 컨테이너 내 모델 경로 (§3). 모델 없이 룰만 테스트하려면 주석 처리.
- `DATABASE_URL`/`REDIS_URL`/`MINIO_ENDPOINT` — 클라우드 호스트/네트워크에 맞게

> 저장 암호화는 lite-cloud 에서 OFF 라 `STORAGE_ENCRYPTION_KEY` 불필요.
> onprem-local(하드닝)로 배포한다면 `STORAGE_ENCRYPTION_KEY` 도 필수(없으면 부팅 거부·ingestion 500).

## 2. 기동 — **명시적 compose 경로 + --env-file** (BLOCKER A4)

`docker-compose.override.yml` 은 로컬 dev 전용(GPU·Ollama 강제)이라 자동 머지되면 GPU 없는 VM 에서
실패한다. 반드시 override 제외 + `--env-file` 로 올린다. **`ENV_FILE=` 와 `--env-file` 을 같은 파일로**
줘야 dev `.env` 누수(admin/training/실키·잘못된 provider)가 완전히 차단된다(prod overlay 가
env_file 을 !override 로 끊고 서비스 env_file 을 `${ENV_FILE:-.env.prod}` 로 받음):

```bash
CF=".env.cloud"   # 채운 env 파일
BASE="-f docker-compose.yml -f docker-compose.prod.yml"
ENV_FILE=$CF docker compose --env-file $CF $BASE up -d postgres redis minio
# postgres 헬시 대기 후 ↓
ENV_FILE=$CF docker compose --env-file $CF $BASE run --rm api alembic upgrade head   # 마이그레이션 수동(A5)
ENV_FILE=$CF docker compose --env-file $CF $BASE up -d
```

- prod overlay(하드닝): **Dockerfile.api.prod**(non-root·gunicorn) · **불변**(./src·./scripts bind 없음,
  모델은 ro external volume) · **포트 최소노출**(api 만 127.0.0.1:8000, pg/redis/minio/mlflow 미노출 —
  리버스 프록시/내부망 전제) · HF 캐시는 non-root named volume(재다운로드 방지).
- `WEB_CONCURRENCY=1`(기본) 권장 — 멀티워커는 모델 reload 팬아웃 미구현이라 재기동으로 갱신.
- alembic 단일 head `a7b8c9d0e1f2`. 확장(vector/pg_trgm)은 마이그레이션이 생성(pgvector 이미지).

## 3. 분류 모델 공급 (BLOCKER A3)

`artifacts/` 는 이미지·git 밖(대용량)이라 **외부 볼륨**으로 넣는다. prod compose 는 `prod_artifacts`
(external·ro)를 `/app/artifacts` 에 마운트한다:

```bash
docker volume create lloydk_prod_artifacts
# 호스트의 모델을 볼륨에 적재 (헬퍼 컨테이너로 복사)
docker run --rm -v lloydk_prod_artifacts:/dst -v "$(pwd)/artifacts:/src:ro" alpine \
  sh -c "mkdir -p /dst/classifier_p1_retrain_v4_clean && \
         cp -r /src/classifier_p1_retrain_v4_clean/v-dd3abab9 /dst/classifier_p1_retrain_v4_clean/"
```

- `.env.cloud` 의 `CLASSIFIER_MODEL_DIR=/app/artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9` 와 경로 일치.
- 설정했는데 경로 부재면 **부팅 거부**(무음 rule-fallback 방지) — 의도된 fail-loud.
- 배포 후 최초 활성: 모델은 서빙 시 DB 활성본 우선 → 없으면 env(`CLASSIFIER_MODEL_DIR`) 폴백. 필요 시
  `POST /api/v1/admin/model/activate` 로 명시 활성.

## 4. 온도 보정 (SHOULD B1 — 서빙 품질)

active 모델에 `temperature.json` 이 없으면 T=1.0(미보정)으로 서빙 → OOD 과신·**TS/S1 무음 미탐**
위험(실측 TS FNR 0.32). 현재 배포 모델(v-dd3abab9)엔 `temperature.json`·`val_logits.jsonl` 이 없다.

- **(테스트 권장) 환경변수 대체**: `.env.cloud` 의 `CLASSIFIER_TEMPERATURE=3.0` 유지 (사전 실측 T≈3).
  서빙이 model dir 에 temperature.json 이 없을 때 이 값을 쓴다. 미보정(T=1.0) 위험을 즉시 제거.
- **(정식) 재보정**: `calibrate_classifier.py` 는 `--val` 에 **per-row logits+label_idx jsonl**(원문 아님)을
  요구한다. 이 모델엔 그 logits 가 없으므로, ① 다음 학습 때 trainer 가 자동 산출하는 `val_logits.jsonl`
  로 보정하거나(권장), ② holdout 에 모델을 추론시켜 logits 를 먼저 만든 뒤 보정한다. 산출된
  `<model-dir>/temperature.json` 은 서빙이 자동 로드(env 값보다 우선) — 이후 §3 볼륨 재적재.

## 5. 워커·관측 스택 기동 (SHOULD B3)

`up -d` 에 worker·beat 포함(compose 기본). Prometheus/Grafana 로 테스트를 지켜보려면 관측 스택도:

```bash
docker compose -f infra/observability/docker-compose.obs.yml up -d   # (있으면)
```

- worker·beat 가 떠 있어야 drift·outbox·partition·audit-chain 알람이 no-data 를 벗어난다.
- 라이브 FNR(`classify_correct/total`)은 사람 confirm/relabel 발생 시에만 증가(무트래픽=NaN, 정상).

## 6. 스모크 검증

```bash
# 헬스
curl -fsS http://<host>:8000/api/v1/healthz/ready   # 200 이어야(모델·DB·스토어 준비)
# E2E 분류 스모크 (200 + label∈{TS,S1,S2,S3} + ≤30s)  — p5 는 --url (--base-url 아님)
python scripts/p5_e2e_smoke.py --mode http --url http://<host>:8000 --api-key <API_KEY>
# 인수(acceptance) 샘플팩 — 전 포맷 파싱 + severity floor(고등급 미탐 없음) 검증
#   폐쇄망 번들: bash acceptance/run_acceptance.sh  /  레포 보유: make acceptance-test
python scripts/run_acceptance.py --mode http --base-url http://<host>:8000 --api-key <API_KEY>
```

- `/healthz/ready` 가 503 이면 원인 확인: 모델 미공급(rule_fallback)·DB·스토어(minio→local degrade).
- `/healthz/deep` 로 구성요소별 상태 확인.
- 인수 러너 판정: `PASS`/`FAIL`. `UNDER!`(고등급 미탐)·파싱실패는 veto — 배포 전 반드시 0 이어야 한다.

---

## 알아둘 한계 (테스트 해석용)

- **검수 큐 서버측 조회 (B2 — 해소됨)**: `GET /api/v1/review-queue` 로 DB 에 쌓인 needs_review/
  needs_second_review 대기 건을 서버측에서 FIFO 조회한다(reviewer 권한, 페이지네이션). admin 콘솔의
  "DB 검수 큐 불러오기"가 이 API 를 호출 — 브라우저 세션과 무관하게 실제 대기 목록을 본다(구 '세션-only'
  한계 해소). SQL 직접 확인(`documents.processing_status='needs_review'`)도 여전히 가능.
- **모델 reload 멀티레플리카 팬아웃 미구현 (NFR-OPS-01)**: 프로모션 후 `/admin/model/reload` 는
  로컬 프로세스만 갱신. 멀티워커/멀티레플리카면 재기동으로 갱신.
- **`.doc`(구 워드)**: 이미지에 antiword 미포함 → 해당 입력은 검수 라우팅(무음 미탐 아님). 필요 시 추가.
  HWP 표 셀은 `[hwp-tables]`(unhwp, MIT)로 **전 배포 이미지에 포함**되어 회수된다(구 pyhwp/AGPL 경로 폐기).
- **안전게이트 warn-only**: lite-cloud 는 agreement/metadata-floor 등이 OFF 여도 경고만(dev/staging
  의도). 하드닝 운영은 onprem-local tier 로 배포.

## 로컬 리허설에서 확인된 것 (2026-07-05, prod 경로 그대로 로컬 재현)

전체 경로 성공: non-root gunicorn 이미지 빌드 → fail-fast 통과 → 마이그레이션 → 볼륨 모델 로드
(`/healthz/ready` = model:loaded·db:ok·vectorstore:ok) → 실분류(label=TS·model_version=v-dd3abab9·
S/V/M 분해·evidence, 772ms). 리허설이 잡은 실 이슈:

- **[해결] `Dockerfile.api.prod` 빌드 불가였음** — ① editable 설치를 src 복사 전에 실행(egg_base 'src'
  없음) ② alembic/alembic.ini 미복사(마이그레이션 불가) ③ extras 에 hwp/ocr 없음(파싱 저하). 소스
  선복사 + alembic 복사 + `.[psh,otel,jwt,hwp,ocr]` 로 교정함.
- **[주의] non-root 이미지 + HF 캐시 경로 불일치** — prod 이미지는 uid1000(lloydk, home /home/lloydk)
  로 도는데 base compose 는 HF 캐시를 `/root/.cache/huggingface` 에 마운트 → 비-root 는 못 읽어
  임베더(bge-m3)를 매 기동 재다운로드(startup ~2.5분). 클라우드는 `HF_HOME` 을 쓰기가능한 마운트
  경로로 지정하거나 임베딩 모델을 이미지/볼륨에 미리 넣을 것.
- **[최적화] GPU torch 로 이미지 ~10GB** — CPU 배포인데 기본 torch 가 CUDA 휠(~4GB)을 끌어옴.
  CPU 전용 torch 휠(`--index-url .../cpu`)로 바꾸면 이미지·다운로드가 크게 준다(테스트엔 무영향).
- **[확인] 안전게이트 warn-only** — 기동 시 AGREEMENT/METADATA_FLOOR/… OFF 경고만(lite-cloud 의도),
  부팅 차단 없음. storage 는 classify 경로서 미접촉(ready 'skipped').

## 배포 전 최종 체크리스트

- [ ] `.env.cloud` — API_KEY·LLM 키·`LLOYDK_AUDIT_CHAIN_SECRET`·`CORS_ALLOW_ORIGINS`·MINIO_SECRET_KEY 실값
- [ ] 명시 compose 경로(`-f docker-compose.yml -f docker-compose.prod.yml`) — override 제외
- [ ] `alembic upgrade head` (단일 head)
- [ ] `prod_artifacts` 볼륨에 모델 적재 + `CLASSIFIER_MODEL_DIR` 경로 일치
- [ ] `temperature.json` 또는 `CLASSIFIER_TEMPERATURE` (미보정 방지)
- [ ] worker·beat·(관측 스택) 기동
- [ ] `/healthz/ready` 200 + p5 스모크 통과
- [ ] 인수(acceptance) 샘플팩 **PASS** (전 포맷 파싱 + 고등급 미탐 0 + 숫자 무손실) — `bash acceptance/run_acceptance.sh` / `make acceptance-test`
