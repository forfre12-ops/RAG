# 라이브 검증 기록 — async 경로 · PG NL 쿼리 (2026-08-12)

REMAINING_WORK.md 에 2026-06-24부터 열려 있던 두 항목을 **실제 구동 스택에서** 닫았다.
코드가 배선돼 있다는 것과 실제로 돈다는 것은 다르고, 그동안은 앞의 것만 확인돼 있었다.

대상: 이중 배포 스택(지재원 `:8000` · 고객사 `:8001`), 가동 5일차.

---

## 1. Celery async 경로 (Top-7 / A-2) — ✅ 통과

기존 상태: "코드 배선 완료(2026-06-24). **남음: 라이브 redis+worker 로 async 경로 1회 검증**"

### 확인한 것

```
워커 생존       celery inspect ping    → celery@b878fcb14e48: pong · 1 node online
등록 태스크     celery inspect registered → 12종
                (classify_async · train_classifier · golden_build · drift_tick ·
                 nightly_incremental_retrain_tick · auto_rollback_tick · outbox · …)
```

### 실제 발사 → 처리

```
POST /api/v1/classify/async   → {"job_id":"fe9f0b11-…","status":"queued"}
GET  /api/v1/classify/jobs/…  → {"status":"done","total":1,"completed":1}
```

워커 로그(같은 job):

```
Task koipa.classify_async[3484473d-…] received
classify done: doc_id=live-async-verify-… label=Grade.S1 confidence=0.793 status=needs_review
Task koipa.classify_async[3484473d-…] succeeded in 7.856s
```

**브로커를 실제로 경유했다** — API 가 큐에 넣고 별도 컨테이너의 워커가 집어 처리했다.
in-process 폴백이 아니다(폴백이면 워커 로그에 안 찍힌다).

### 부수 확인 — 안전 게이트가 라이브에서 발화했다

결과가 `status: needs_review` 로 나왔고 사유가 함께 실려 있다:

```
rule_grade   S3
model_grade  S1
decision_path  escalation (룰 S3 · 모델 S1 → 검수 라우팅)
warnings     agreement-gate: model=S1 vs rule=S3 disagree on non-public grade
             — routed to human review (conf alone insufficient)
```

confidence 0.793 은 자동확정 임계를 넘지만 **룰과 불일치해 사람 검수로 갔다.**
이것이 설계 의도이며(conf 단독 자동확정 금지 — golden500 AUROC 0.58), 문서상 주장이
아니라 실제 동작으로 확인됐다.

추정 요인은 `secrecy=2 · value=2 · management=0` 으로, `grade_from_svm` 상 S1 에
대응하는 조합이다 — 모델이 요인 조합을 맞게 잡았다.

⚠ `persistence skipped: doc_id 가 UUID 아님` 경고도 함께 떴다. 시험용 doc_id 를 문자열로
준 탓이고 분류 자체에는 영향이 없다. 운영 경로는 UUID 를 쓴다.

---

## 2. PG NL 쿼리 게이트 (A-1 / §03) — ✅ 통과

기존 상태: "default 이미 pg, ES 는 compose 제거. **잔여 = 라이브 PG NL 게이트 실행만**"

```
확장            vector v0.8.3 (그 외 pg_trgm 1.6 · uuid-ossp 1.1 · plpgsql 1.0)
적재            tb_chunks_2026_08 709행 · tb_rag_vectors 2행
서빙 설정       vector_backend=pg · embedding_provider=hf(실 임베더) · search_mode=hybrid
                embedding_model=nlpai-lab/KURE-v1
```

실제 질의:

```
POST /api/v1/rag/search  {"query":"시장진입 전략과 영업비밀 보호 방안",
                          "namespace":"uploads","top_k":3}
→ count 2 · elapsed_ms 434 · score 1.0 / 0.5
```

**pgvector 하이브리드 경로가 순위를 매겨 결과를 돌려줬다.** ES 없이 동작한다.

⚠ 색인 규모가 작다(rag_vectors 2행). 이 검증이 보증하는 것은 **경로가 동작한다**는 것이지
검색 품질이 아니다. 품질 수치는 별도 코퍼스로 재야 한다.

---

## 3. 이 검증이 보증하지 않는 것

- **정확도·검색 품질** — 위 두 건은 경로 동작 확인이다. 분류 정확도는
  `docs/GAMRI_NARRATIVE_2026-08-31.md`, 평가셋 상태는 `datasets/proxy_eval/ROLE.md` 참조.
- **부하·동시성** — 단건 발사다. PER-002(속도·자원) 검증은 별도다.
- **고객사 스택** — `:8000`(지재원)에서만 확인했다. `:8001`(고객사) 은 동일 이미지이나
  프로파일이 다르므로 필요 시 별도 확인.

---

관련: `REMAINING_WORK.md` Top-7 / A-1 · `docker-compose.dual.yml`
