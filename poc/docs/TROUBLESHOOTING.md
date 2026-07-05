# Lloydk AI 장애 대응 (TROUBLESHOOTING)

대상: KOIPA 영업비밀관리시스템 폐쇄망 운영자 · 번들 `lloydk-airgap-bundle`
짝 문서: 설치 [`INSTALL.md`](INSTALL.md) · 운영 [`OPERATION.md`](OPERATION.md).

> INSTALL §11이 설치 단계 증상을, 이 문서는 **운영 중 장애**를 증상→진단→조치로 다룬다.
> 설계 원칙은 "안전하게 틀리고, 빨리 배우고, 명확히 멈춘다" — 대부분의 실패는 미탐 방향으로
> 무너지지 않도록 fail-secure(최고등급+검수) 또는 fail-loud(기동 거부)로 처리된다.

---

## 1. Postgres 미가용 / 연결 타임아웃

**증상**: `/api/v1/healthz/ready` 503. 심하면 분류/검수 요청이 **~100초 멈췄다가**
`sqlalchemy.exc.OperationalError: connection timeout expired (localhost:5432)`.

**진단**:
```bash
$COMPOSE ps postgres                                   # healthy 인가
$COMPOSE exec postgres pg_isready -U "$POSTGRES_USER"  # accepting connections?
```

**조치**:
- postgres 컨테이너 재기동(`$COMPOSE up -d postgres`), 볼륨 마운트·`POSTGRES_PASSWORD` 확인.
- 방화벽/네트워크로 **거부가 아니라 무응답**이면 커넥트가 기본 타임아웃까지 매달린다. `DATABASE_URL`에
  `?connect_timeout=3`(초)을 붙여 빠르게 실패하도록 하면 대기 시간이 줄어든다.

> ⚠️ **알려진 견고성 갭(추적 중)**: 분류 경로의 DB 영속화(`_try_persist`)는 best-effort로
> `SQLAlchemyError`를 삼키지만, 분류 경로의 일부 부가 DB 조회는 연결 타임아웃을 그대로 표면화해
> 분류가 지연·실패할 수 있다(원래 등급 판정은 DB 없이 가능). 운영에선 **PG 가용성을 우선 확보**하고
> `connect_timeout`을 짧게 두어 완화한다. 근본 수정(부가 조회 전면 fail-open)은 별도 하드닝 항목.

---

## 2. Redis 미가용 → 폴백 / 멀티워커 기동 거부

**증상 A(단일 워커)**: 로그에 `redis 연결 실패 — in-memory 폴백`. 동작은 계속되나 워커 재기동 시 job 상태 유실.

**증상 B(멀티워커)**: 기동이 즉시 `RuntimeError: WEB_CONCURRENCY>1(멀티워커) 운영인데 async job 저장소가
in-memory 폴백` (또는 동일 취지의 idempotency 메시지)으로 **fail-fast**.

**원인**: `WEB_CONCURRENCY>1`인데 redis가 없으면 job/idempotency가 프로세스-로컬 메모리로 폴백 → 워커 간
가시성이 깨진다(워커 A 제출 job을 워커 B가 못 봄, 변경성 POST 중복 처리). 안전을 위해 기동을 막는다.

**조치**: `REDIS_URL`을 가용 redis로 설정(`$COMPOSE up -d redis` 후 재기동). 단일 프로세스로만 운영할
경우 `WEB_CONCURRENCY=1`로 두면 메모리 폴백이라도 일관.

---

## 3. 모델 미공급 / `CLASSIFIER_MODEL_DIR`

**증상**: 기동 시 `CLASSIFIER_MODEL_DIR`이 설정됐으나 경로 없음 → **fail-fast**(무음 rule-fallback 방지).
경로는 있으나 비어 있으면 rule-only 분류로 저하(경고).

**진단**: `/api/v1/healthz/deep`의 model 프로브 · `ls $CLASSIFIER_MODEL_DIR`(model.safetensors·config.json).

**조치**: INSTALL §3대로 `models/classifier-trained/`에 학습 가중치 배치. 컨테이너 마운트 경로와 `.env` 값 일치 확인.

---

## 4. 분류 confidence 과신 / `temperature.json` 부재

**증상**: confidence가 비정상적으로 1.0에 가깝고 OOD 문서에 과신.

**원인**: 활성 모델 dir에 `temperature.json`이 없어 서빙이 T=1.0(무보정)로 동작.
(onprem-local 프로파일은 `classifier_temperature=3.0`으로 완충하나, 모델별 보정값이 최선.)

**조치**: 학습 시 산출된 `temperature.json`을 모델 dir에 동봉하거나 `make calibrate CAL_MODEL_DIR=<dir>`로 재산출.

---

## 5. 검색 붕괴 / 임베더 폴백

**증상**: 검색·RAG 품질 급락, 로그에 HashEmbedding 관련 경고.

**원인**: 임베딩 모델(KURE-v1/BGE-M3) 로드 실패 → 무성 HashEmbedding 폴백(검색 무의미). 폐쇄망은
`HF_HUB_OFFLINE=1`이라 모델 미배치 시 **기동 실패**(fail-loud)가 정상.

**조치**: INSTALL §3대로 `models/hf/`에 임베딩 캐시 반입. `HF_HOME`이 그 경로를 가리키는지 확인.

---

## 6. LLM provider 미가용

**증상**: `/api/v1/answer`가 스텁 응답, LLM 2차판정/합의 라벨 품질 저하.

**원인**: `LLM_PROVIDER`가 noop이거나 로컬 LLM endpoint 무응답. (등급 결정 핫패스는 LLM-free라 분류 자체는 계속됨.)

**조치**: OPERATION §해당 없음 — INSTALL §8대로 vLLM/Ollama 기동, `LOCAL_LLM_BASE_URL`/`LOCAL_LLM_MODEL` 정합.
`curl http://<llm-host>:8001/v1/models`로 endpoint 확인.

---

## 7. 릴리스 게이트 차단

| 게이트 메시지 | 원인 | 조치 |
|---|---|---|
| `[release-gate] BLOCKED (hard)` human_review | `human_review` 골든 < 40 | 실검수 서명 채움(`ai_assist`/`llm_*` 금지). 파일럿은 `release-gate-pilot` 감사 waiver. |
| `[metamorphic-gate] BLOCKED: forward regression` | 스타일 패러프레이즈에 고등급이 하향분류(고등급 미탐) | 데이터천장 아님 = **안전 회귀**. 해당 등급(특히 S1 인사·재무: M&A 실사·자금조달·임원보상) 학습 보강 후 재판정. pilot도 waive 불가. |
| `[metamorphic-gate] FAIL: not measurable` | 리포트 미생성/표본<min_n | `make metamorphic-gate`를 배포 모델로 실제 실행(리포트 미생성=green 아님). |
| adversarial-gate FAIL | golden_100 고등급 미탐 baseline 초과 | 회귀 원인 모델/설정 확인 후 롤백 또는 재학습. |

---

## 8. 감사체인 파손

**증상**: `lloydk_audit_chain_broken_total > 0`, P0 `AuditChainBroken` 알림.

**원인**: 과거 감사 행 변조·삭제·재배열, 또는 HMAC 키(`LLOYDK_AUDIT_CHAIN_SECRET`) 불일치.

**조치**: `verify_audit_chain_tick` 수동 실행으로 파손 구간 특정. 키 회전 이력·백업 대조. 운영(poc_mode=full)은
키 미설정 시 기동 자체가 fail-fast하므로, 파손은 대개 데이터 무결성 사건 — 보안 절차에 따라 보고.

---

## 9. 큐 작업 미소비 / 자동화 정지

**증상**: async 분류·학습·drift·롤백·outbox가 진행 안 됨.

**원인**: worker `-Q` 큐 누락, 또는 **beat 미기동**(정기 발행기 부재).

**조치**: OPERATION §1·§7 — worker가 `classify,index,synthesis,learning,celery` 전 큐 구독, beat 정확히 1개 기동 확인.

---

## 10. 관측성 알림 미발화

**증상**: 안전 사건이 나도 Grafana/Alertmanager에 아무것도 안 뜸.

**원인**: 관측성 스택 미기동, 또는 dev overlay 사용(alert_rules 미마운트), 또는 obs 이미지 미동봉.

**조치**: INSTALL §10.5 — airgap overlay로 관측성 up, `http://<host>:9090/alerts`에 규칙 로드 확인.
통지 채널은 `observability/alertmanager.yml receivers`에 사내 SMTP/webhook 추가.

---

> 여기서 해결되지 않는 증상은 `$COMPOSE logs api worker beat`와 `/api/v1/healthz/deep` 프로브 출력을
> 첨부해 로이드케이에 에스컬레이션한다.
