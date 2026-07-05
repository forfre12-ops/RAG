# Lloydk AI 운영 절차서 (OPERATION)

대상: KOIPA 영업비밀관리시스템 폐쇄망 운영자 · 번들 `lloydk-airgap-bundle`
짝 문서: 설치는 [`INSTALL.md`](INSTALL.md), 장애 대응은 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md),
정본 운영 절차·책임경계는 `운영_런북`(HTML) 참조.

> 이 문서는 설치가 끝난 시스템의 **Day-2 운영**(기동·정지·모델 교체·검수·릴리스·관측·백업)을 다룬다.
> 모든 명령은 번들 루트에서, compose 별칭은 설치와 동일하게 잡는다:
> ```bash
> export COMPOSE="docker compose --env-file .env -f infra-config/docker-compose.airgap.yml"
> ```
> 엔드포인트는 전부 `/api/v1` 접두사. 변경성 호출은 `X-API-Key`(또는 JWT) 필요.

---

## 1. 일상 기동 · 정지 · 상태

```bash
$COMPOSE up -d postgres redis          # 의존성 먼저 (postgres healthy ~30s)
$COMPOSE up -d api worker beat         # 앱 (worker=전큐 구독, beat=단일)
$COMPOSE ps                            # 상태
$COMPOSE stop api worker beat          # 앱만 정지 (데이터 보존)
$COMPOSE down                          # 전체 정지 (named 볼륨은 보존)
```

- **worker**: `-Q classify,index,synthesis,learning,celery` 전 큐 구독 필수(compose 반영). 큐 누락 시 해당 작업 무소비.
- **beat**: 정확히 1개만. 정기 자동화(§7)의 발행기 — 누락 시 drift·롤백·outbox·파티션·감사검증이 전부 정지.
- 헬스: `curl -s http://localhost:8000/api/v1/healthz/ready`(의존성 실측, 503=미준비) · `/api/v1/healthz/deep`(파서·모델·임베더 프로브).

---

## 2. 분류 서빙 · 안전 게이트 상태 확인

운영 프로파일은 `LLOYDK_DEPLOY_PROFILE=onprem-local`(또는 `full-train`). 이 프로파일 기본값:

| 게이트 | 기본(onprem-local) | 역할 |
|---|---|---|
| `classifier_escalation_tau` | `0.30` | 저신뢰 예측 → 자동확정 대신 검수 라우팅 |
| `agreement_gate_enabled` | `True` | 룰등급≠모델등급 불일치 → 검수(자동확정 정밀도↑) |
| `metadata_floor_enabled` | `True` | KL 보안표시/접근범위로 등급 하한 상향(비밀관리성 보완) |
| `rule_fallback_min_evidence` | `0.9` | 단일 약한 키워드만으로 conf=1.0 자동확정 차단 |
| `classifier_temperature` | `3.0` | 서빙 보정(과신 완화). 활성 모델 dir의 `temperature.json`이 우선 |
| `require_safety_gates` | `True` | 위 게이트 중 하나라도 꺼지면 **기동 fail-fast** |

- 서빙은 의도적으로 **안전방향 과분류**(고등급 미탐 0 우선)다 — 정확 등급일치가 아니라 미탐 없음이 합격 기준.
- 스모크: `POST /api/v1/classify` (`{"doc_id":..,"content":..}`) → 등급 + confidence + `status`(needs_review면 검수 큐).
- 검수 대기 목록: `GET /api/v1/admin/escalation-held`. 관제: `GET /api/v1/admin/dashboard`.

---

## 3. 모델 활성 · 교체 · 롤백

활성 모델 교체는 **배포 게이트**를 통과해야 한다(회귀 방지 + locked-eval 준비도).

```bash
# 활성화 (감사 기록 + deploy gate + locked-eval 준비도 검사)
curl -s -X POST http://localhost:8000/api/v1/admin/model/activate \
     -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
     -d '{"model_version":"v-XXXXXXXX"}'
```

- 하드닝 프로파일에서는 `locked_gold_eval` 준비 전 활성화가 **차단**된다(`deploy_gate_manual_require_locked_eval`). 부득이 강제 시 `force=true` + `reason` 필수 → `model.activate.forced` 감사 체인 행이 남는다(무단 강제 불가).
- 준비도 확인: `GET /api/v1/admin/locked-readiness`.
- **reload vs activate**: `POST /api/v1/admin/model/reload`은 현재 워커의 싱글턴만 새로고침(멀티워커 팬아웃 없음 — 워커별로 호출하거나 롤링 재기동). `activate`는 활성 버전 자체를 바꾼다.
- **롤백**: 이전 `CLASSIFIER_MODEL_DIR`을 복원하고 api/worker 재기동. 이전 모델 디렉터리는 인수 완료까지 마운트 유지. 자동 롤백(§7)은 `auto_rollback_enabled=True`일 때만 실제 동작(기본은 판정·로깅).

---

## 4. 검수 · 교정 환류 루프

운영 업로드 중 자동확정 안 된 건은 검수 후 교정으로 학습에 환류한다(회원사 교정은 **평가셋 오염 차단**을 위해 gold_candidate로만 반입).

```bash
make human-review-queue                # 검수 큐 CSV 생성
# 검수자가 review_decision·reason_code·reviewer_id(실계정) 기입 후:
python scripts/import_review_corrections.py <queue.csv> --as-candidate --dry-run
python scripts/import_review_corrections.py <queue.csv> --as-candidate
```

- `reviewer_id`가 `ai_assist`·`llm_*`·공란·플레이스홀더면 **임포트 거부**(사람 서명만 human_review로 인정).
- `--as-candidate`: 회원사 반입을 `gold_candidate`로 격리(지재원 golden-build 기본 무변경). locked 평가 오염 방지.
- 골든 후보 승격: `scripts/promote_golden_candidates.py`. 골든 빌드: `POST /api/v1/golden/build` + 검수 HTML `GET /api/v1/golden/jobs/{id}/review.html`.

---

## 5. 릴리스 게이트 (배포 전 판정)

```bash
make operational-readiness             # P1/P2/data 준비도 리포트 (CONDITIONALLY_READY 가능)
make release-gate-pilot                # 파일럿 게이트 (하드 FAIL·회귀만 차단, 데이터천장 감사 waive)
make release-gate                      # 상용(GA) 엄격 게이트 (모든 gate PASS 요구)
```

- 두 게이트 모두 prereq에 **`adversarial-gate`**(golden_100 고등급 미탐 회귀)와 **`metamorphic-gate`**(스타일 패러프레이즈에 고등급 하향분류 = forward 회귀, fail-closed)를 강제한다.
- 메타모픽 forward 회귀는 데이터천장이 아니라 **안전 FAIL**이라 pilot에서도 waive 불가.
- GA 하드블로커: `human_review` 골든이 최소치(40) 미만이면 strict `release-gate` 차단(파일럿은 감사 waiver로 진행).
- 릴리스 산출물 해시: `make release-manifest`(게이트 통과 후 증빙 고정).

---

## 6. 관측 · 안전 알림

안전 신호(FNR 급증·감사체인 파손·킬게이트 발동·rule-fallback 서빙 등)는 **Prometheus/Grafana가 떠야 소비**된다(INSTALL §10.5).

```bash
export OBS="docker compose --env-file .env -f observability/docker-compose.observability.airgap.yml"
$OBS up -d
curl -s http://localhost:8000/api/v1/metrics-prom | head   # 앱 메트릭 노출 확인
```

- 발화 규칙: `http://<host>:9090/alerts` (airgap overlay가 `alert_rules.yml` 마운트).
- 대시보드: `http://<host>:3000` (Grafana). 통지 라우팅: `alertmanager`(9093) — 사내 채널은 `observability/alertmanager.yml receivers`에 추가.
- **킬게이트**: 고등급 미탐·검수피로·번복률 초과 시 발동 → 고등급 자동확정 억제(모니터·비파괴). `GET /api/v1/admin/dashboard`의 kill_gate 섹션에서 상태 확인.

---

## 7. 정기 자동화 (Celery beat)

beat가 발행하는 정기 작업 — 누락 시 아래가 전부 정지:

| 작업 | 주기 | 역할 |
|---|---|---|
| `active_learning_tick` | 30분 + 매일 03:00 스냅샷 | 검수 교정 소비 → 학습 후보 |
| `drift_tick` | 15분 | 운영 임베딩 drift 점검(초과 시 `lloydk_drift_alert=1`) |
| `auto_rollback_tick` | 60분 | 활성 모델 라이브 미탐 회귀 점검(`auto_rollback_enabled`면 실롤백) |
| `deliver_outbox_tick` | 60초 | KL webhook 콜백 송신(실패 지수백오프→DLQ) |
| `ensure_partitions_tick` | 매일 02:10 | 향후 3개월 월 파티션 보장(멱등) |
| `verify_audit_chain_tick` | 매일 03:30 | 감사체인 무결성(broken>0 → P0 AuditChainBroken) |

---

## 8. 백업 · DR

- **DB**: `pg_dump`(정기) + named 볼륨 스냅샷. 복구 후 `alembic upgrade head`로 스키마 정합 확인.
- **원본 스토리지**: `/app/.storage`(storagedata 볼륨) — 원본은 AES-256-GCM 암호화 저장. 키(`STORAGE_ENCRYPTION_KEY`) 별도 백업·에스크로.
- **감사체인**: 백업 전/후 `verify_audit_chain_tick` 수동 1회(무결성 증빙). HMAC 키(`LLOYDK_AUDIT_CHAIN_SECRET`) 유실 시 과거 행 재검증 불가.
- **DR 드릴**: `scripts/dr_drill.py`(10단계 + RTO 게이트). 목표 RTO 리허설은 인프라 확보 후 정기 수행.

---

## 부록 — 자주 쓰는 확인 명령

```bash
$COMPOSE ps                                            # 서비스 상태
curl -s http://localhost:8000/api/v1/healthz/ready     # 준비도(의존성 실측)
curl -s http://localhost:8000/api/v1/healthz/deep      # 파서·모델·임베더 프로브
curl -s http://localhost:8000/api/v1/metrics/latest -H "X-API-Key: $API_KEY"   # 최신 지표
$COMPOSE exec api python scripts/verify_infra.py       # 인프라 일괄 점검
$COMPOSE logs -f --tail=100 api worker                 # 로그
```
