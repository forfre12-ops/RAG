# 남은 작업 (Remaining Work) — Lloydk AI Engine PoC

> 소스 + 기획문서/RTM/요구사항을 8개 병렬 감사로 대조해 도출한 144개 발견을 중복제거·통합한 목록.
> **모든 수치/판정은 합성 골든셋(OOD) 기준** — 실데이터(human_review) 검증 전에는 정식 수치가 아니다.
> 최초 작성: 2026-06-24. 갱신 시 항목 상태(체크박스)만 토글.

---

## ⚡ 우선순위 Top 8 — 실(非-dryrun) 운영 배포를 실제로 막는 것

- [ ] **1. 인간검증 골든셋 0/40** — `make release-gate` 하드 블로커. human_review 라벨 0건 → strict 릴리스 불가, 모든 게이트 임계 재보정의 전제.
- [ ] **2. 실데이터(회원사 실문서) 미확보** — 유일한 🔴 차단 자원(doc16 §1.3, 결정 A5). 인간 골든셋·실문서 FNR/Recall·베타 전부 종속. (외부 의존)
- [~] **3. 온도 보정(T) 파이프라인** — 🟡 **코드 배선 완료(2026-06-24)**: trainer가 val per-row logits를 `val_logits.jsonl`로 덤프 → calibrate가 자동탐색해 `temperature.json` 산출 → 서빙 자동로드. **남음:** 실 학습 1회 실행으로 temperature.json 생성·배치(실데이터/GPU 후).
- [ ] **4. FNR KPI 미달(≤5% 목표 vs 실측 17~22%)** — 후보 전모델 F1<0.75. 코드 아닌 실 S1/TS 데이터 양 문제.
- [~] **5. 안전 게이트 + escalation τ** — 🟡 **부분 ON(2026-06-27 d1aac27)**: `agreement_gate_enabled`+`classifier_escalation_tau=0.30`을 onprem-local·full-train 프로파일 기본 ON으로 전환(정밀도 63→81%·미탐 메커니즘 활성). **남음:** lite 프로파일은 여전히 OFF; `metadata_floor`·`model_secondopinion_llm` 2종은 아직 기본 OFF(켤 근거 테스트는 배치 D1 확보, 임계 재보정은 human_review 후).
- [ ] **6. 모델 버전 드리프트 — 사실은 문서(HTML)만 stale** — 🔎 재확인(2026-06-24): `.env`/`.env.example`/`.env.prod.example` **3종 모두 이미 v-dd3abab9(clean)** 을 가리키고, F1 0.686/FNR 0.018도 **그 clean 모델**의 서빙경로 실측. 코드는 정합. **남음:** 모델카드/테스트전략 HTML이 아직 v-f9b5cedb로 단정 → 정정 + **활성모델 A/B 결정**(A=v-dd3abab9 유지[증거부합] / B=step3 승격 시 .env 되돌림+0.686 재측정). `.env.prod.example` 주석 'v-437ec196 채택'은 아티팩트 부재 모순.
- [~] **7. API 비동기 Celery 발사** — 🟡 **코드 배선 완료(2026-06-24)**: 브로커 가용 감지 시 `classify_async/train_classifier_task/synthesize_batch.delay()` 발사, 미가용/테스트는 in-process 보존, 거짓 'queued' 표기 정정. **남음:** 라이브 redis+worker로 async 경로 1회 검증, 운영 callback webhook은 tasks.py 후속.
- [ ] **8. ES→PG 마이그레이션 §03 게이트 미실행** — PgVectorStore가 EXPERIMENTAL 미검증 스캐폴드. 실 PG·NL쿼리 게이트 전 ES 폐기 불가.

---

## ✅ 2026-06-24 처리 완료 (병렬 6스트림)

lite pytest **922 passed / 0 failed** (14 skip 환경성). 변경된 스트림 코드 = 아래.

| 스트림 | 매핑 | 상태 |
|--------|------|------|
| calib | Top-3 / C-4 | 🟡 코드완료 (trainer val_logits→calibrate→temperature.json 자동배선). 실 학습 실행만 남음 |
| celery | Top-7 / A-2 | 🟡 코드완료 (.delay() 발사 + 브로커감지 폴백 + 'queued' 정정). 라이브 redis 검증만 남음 |
| citation | F | ✅ `answer_enforce_citations` config 정식 필드화 + answer.py 배선 |
| adapters | F / B-reranker | ✅ get_reranker noop 폴백 + storage(seaweedfs/minio) 키 위생 일관화 |
| m2split | F | ✅ M2 PreprocessPipeline split_v2 배선 (heading_path 메타) |
| docdrift | D-5 / Top-6 | 📋 보고만 — Top-6 재프레이밍 참조 (HTML stale + 모델 A/B 결정 필요) |
| D-1 | Dockerfile extras | ✅ (선행 처리) |

---

## 🔁 2026-06-25 재대조 (현재 코드 vs 목록 — 10클러스터 감사)

> 2026-06-24 이후 커밋(테넌트 전면 제거·ES→PG 단일화·골든 빌더·감사 HMAC 문서화)으로 다수 항목이 stale.
> 아래는 현재 코드 직접 대조 결과. 이후 작업은 이 절을 기준으로 한다.

### stale 정정 (목록이 코드보다 뒤처짐 — done_since / superseded)

| 항목 | 정정 |
|------|------|
| A-1 | `test_pg_store.py 0건`은 틀림 — 5 PASS. default 이미 `pg`, ES는 core/prod/airgap compose 제거. **partial**(잔여=라이브 PG NL 게이트 실행만). |
| Top-3/C-4 | 온도보정 코드 체인 **완결**(trainer val_logits→calibrate 자동탐색→서빙 자동로드, test 통과). C-4 'dump 안 함'은 사실 아님 → **done_since**. 잔여=실 학습 1회(GPU). |
| Top-7/A-2 | `.delay()` 발사·브로커감지 폴백·'queued' 정정 3서비스 완료 → **done_since**. 잔여=라이브 redis 검증. 배치는 의도적 in-process(건별 격리). |
| D-2 | base·airgap compose에 redis+worker+beat(drift/rollback/outbox/active-learning/partitions) 전부 배선 → **done_since**. 잔여=prod override beat·src마운트. |
| D-9 | OTel(setup_tracing)·Prometheus/Grafana/Loki 풀스택·25+메트릭 존재 → **done_since**. 'OTEL_/PROM_ env 키 없음'은 os.getenv 직접사용이라 오판. |
| D-10 | dr_drill.py 실 10-stage 드릴+RTO 게이트+exit2, test 통과 → placeholder 아님(**partial**). 잔여=실 RTO 리허설(인프라). |
| D-13 | per-tenant RLS는 테넌트 전면제거로 **superseded**. 저장암호화는 B 항목으로 단일화. |
| B-JWT | `_jwt_auth.py` confused-deputy fail-fast + iss/aud 거부 + RS256/exp/nbf + startup 호출 완료 → **done_since**. 잔여=.env.prod.example 주석해제. |
| D-3 | `seed_active_model_version.py` 멱등 구현·의존성 해소 → 지금 **커밋 가능**(현재 untracked). |
| F/M2 split_v2 | pipeline.py chunk()/finalize() 둘 다 split_v2 호출 → **done_since**. |
| F/citation | `answer_enforce_citations` 정식 필드+answer.py enforce 배선 → **done_since**. |
| F/storage 키위생 | minio/seaweedfs 양쪽 `_norm_key` 전 경로 적용 → **done_since**(단 폐쇄망=local이라 운영 적용성은 superseded). |
| F/m4_training shim | query_expansion·rag_indexer는 정상 backward-compat shim → 버그 아님(재분류). |

### 신규 발견 (목록에 없던 것)

- **NEW-H1 (HIGH)** — drift 모니터 실배포 영구 no-op: `sample_vectors`가 inmemory_store.py:107에만 존재, pg_store.py 0건. default=pg 전환으로 celery beat 15분 drift 태스크가 매 주기 빈 표본 skip. → **do_now rank1**.
- **NEW-H2 (HIGH)** — P0 `AuditChainBroken` 등 alert 7종이 미정의 메트릭 참조로 영구 미발화: alert_rules.yml ↔ prom_metrics.py 정의 0건(`audit_chain_broken_total`·`classify_correct/total`·`celery_queue_length`·`outbox_dlq_total`·`pii_masked_total`·`documents_ingested_total`). → **do_now rank4**.
- **NEW-H3 (HIGH)** — 활성 서빙 모델 v-dd3abab9 무보정(T=1.0) 서빙: model_dir에 temperature.json 부재 → MEMORY 'T≈3' 위반. (blocked: GPU/실데이터로 calibrate 실행 필요.)
- **NEW-M1** — `serving_eval.py:61-67` run() 예외→pred='TS'가 고등급 정답 크래시를 '정탐'으로 둔갑시켜 FNR 은폐 → deploy_gate 미탐모델 통과. → **do_now rank2**.
- **NEW-M2** — `deploy_gate` 최초배포 절대 FNR floor 부재(fnr 10%도 통과). → **do_now rank3**.
- **NEW-M3** — `import_review_corrections.py:281` `Document(tenant_id=...)` 테넌트 제거 후 런타임 깨짐(9월 승급 DB 경로, 미테스트). → do_now(배치B).
- **NEW-M4** — 고객사 연동 API 4종 + `tb_self_assessments`가 코드에 전무 + 본 목록 A절 누락(KL ICD 대기).
- **NEW-M5** — `ElasticsearchDown`(P1)·`elasticsearch-exporter`가 제거된 ES 가리켜 영구 false page. → do_now(배치B).

### 🔨 배치 A 완료 (2026-06-25, 코드만으로 가능한 안전 구멍 — lite 948 passed)

- [x] rank1 NEW-H1 — `PgVectorStore.sample_vectors` 구현 (+ `_parse_vec`, test_pg_store 5건 추가) → drift 모니터 실배포 활성화
- [x] rank2 NEW-M1 — `serving_eval` 실패 pred='TS'→최저심각도(미탐 집계)로 수정 (고등급 크래시가 FNR 거짓 inflate 차단), 회귀테스트 교체
- [x] rank3 NEW-M2 — `deploy_gate` `first_deploy_fnr_high_max` floor 추가(기본 None=비파괴) + config `deploy_gate_first_deploy_fnr_high_max` + training_service 배선 + test 4건
- [x] rank4 NEW-H2 — prom_metrics 7종 정의 + 배선(audit_chain verify_chain·document_ingest·mask_pii·outbox DLQ·celery LLEN 프로브); classify_correct/total은 정의만(정답 필요, FnrSpike NaN 무발화)

### 🔨 배치 B 완료 (2026-06-25, 대형 리팩터 드리프트 정리 — lite 950 passed)

- [x] B1 NEW-M3 — `import_review_corrections.py` tenant_id 6곳 제거(`Document(tenant_id=)` 런타임 깨짐 해소) + write_to_db 페이크세션 회귀테스트 2건
- [x] B2 — `config.py` onprem-local/full-train `storage_backend` minio→**local** + docstring ES→PG + `.env.onprem-local` ES/MINIO 블록 제거
- [x] B3 — vectorstore `__init__.py`·`pg_store.py` docstring/주석 default=**pg** 정정(라이브-PG NL 재검증 caveat는 유지)
- [x] B4 — observability compose·prometheus·alert에서 죽은 ES(exporter/job/ElasticsearchDown) 제거; airgap compose minio·mlflow 제거 + **로컬FS storagedata 볼륨**(/app/.storage) 추가
- [x] B5 — `build_offline_bundle`: BundlePolicies.vector_backend_default→pg, --compose 기본→airgap(api/worker image 추출), ES size·env MINIO→STORAGE_BACKEND=local; test 갱신. dry-run 검증 통과
- ⚠️ 보류: airgap **postgres 제거(KL 제공)**는 deploy-db 토폴로지 VP 7항목 확인 대기 → 미변경

### 🔨 배치 D 완료 (2026-06-25, 게이트/골든 운영화 — lite 961 passed)

- [x] D1 NEW-S1 — agreement_gate·model_secondopinion_llm·metadata_floor **flag-ON 단위테스트 8건**(tests/test_safety_gates_flag_on.py) → 운영에서 켤 근거 확보(데이터 불요, monkeypatch+스텁)
- [x] D2 rank12 — `scripts/promote_golden_candidates.py` 신설(빌더 후보→정본 게이트 승격 명시적 호출부) + CLI 글루 테스트 3건. promote_candidates가 라이브러리 함수로만 존재하던 갭 해소
- [x] D3 rank15 — `GoldenBuildRequest.out_dir` 기본 `datasets/gold_real`→`datasets/gold_real/builds`(정본과 산출물 분리)
- [x] D4 D-3 — `seed_active_model_version.py` import·repo 메서드 검증 + METRICS 하드코딩 시드 명시 주석(커밋 준비 완료)

### 🔨 배치 C 완료 (2026-06-25, 보안/운영 강화 — lite 966 passed)

- [x] C1 D-4 — 감사체인 HMAC: `Settings.audit_chain_secret` 필드 + 운영(poc_mode=full) **startup fail-fast**(assert_production_credentials, NFR-SEC-01) + secrets_manager 후보 추가 + `verify_audit_chain_tick` Celery 태스크/일별 beat(broken>0→P0 AuditChainBroken). 테스트 정합 3건
- [x] C2 B-storage-enc — `STORAGE_ENCRYPTION_ENABLED/KEY` .env.prod.example·.env.onprem-local 추가(영업비밀 평문저장 방지, fail-fast는 기존 배선)
- [x] C3 calib-NEW-1 — train→calibrate **자동연결**: trainer가 학습 직후 val logits로 temperature.json 자동 산출(서빙 자동 로드, MEMORY T≈3) + 온도 로직 `m6_evaluation/temperature.py`로 일원화(스크립트 중복 제거) + Makefile `calibrate` 타깃 + 테스트 5건

> ✅ **코드-가능 배치 A/B/C/D 모두 완료.** 이후 잔여는 전부 외부 의존(human_review 실라벨·실문서 9월·라이브 PG/redis/GPU·DR 리허설·PII NER 가중치) 또는 발주처/VP 결정(A안 환산표·고객사 API ICD·airgap postgres 토폴로지)으로, 본 저장소 코드만으로는 더 진행 불가.

---

## 🔁 2026-06-27 재검증 (방향↔코드 6영역 직접 대조 + 코드 패치)

> 최근 의사결정(FNR-safe 운영점·로컬FS·pgvector·테넌트 제거·무반출 교정·3-tier 골든)을 6영역 병렬 감사로 코드 직접 대조. 핵심 메커니즘은 전부 구현·배선 확인. 미완은 **기본값·비활성·실데이터** 3축에 집중. 아래는 직접 읽어 확정한 신규 건.

### 🔨 코드 패치 (2026-06-27, 폐쇄망 storage 기본값 정합 — 관련 61 테스트 green)

- [x] **NEW-H4 — 폐쇄망 local 배포가 운영 startup에서 차단되던 버그** — `assert_production_credentials()`가 `storage_backend`와 무관하게 `LLOYDK_MINIO_SECRET_KEY`를 무조건 요구([config.py](src/lloydk/config.py)) → onprem-local(storage=local·poc_mode=full)이 쓰지도 않는 minio 키 부재로 부팅 실패. **수정:** `storage_backend in (minio,seaweedfs,s3)`일 때만 요구.
- [x] **base storage_backend 기본값 minio→local** — 폐쇄망 결정 정합([config.py](src/lloydk/config.py)). dev compose(api/worker)는 `STORAGE_BACKEND=minio` 명시 고정([docker-compose.yml](docker-compose.yml))로 dev minio 경로 보존. lite-cloud 프로파일은 의도상 minio 유지.

### stale 정정 (재검증)

| 항목 | 정정 |
|------|------|
| Top-8 #5 | d1aac27(2026-06-27)로 onprem-local·full-train은 `agreement_gate`+`τ=0.30` **기본 ON** → 본 절 위 갱신 반영. 목록 본문이 "전부 OFF"였던 것은 stale. |
| 골든 승격/서명 REST 라우트 | **'미개발'이 아니라 의도된 설계** — `api/golden.py` docstring이 "human_review 승격은 별개 경로(import_review_corrections, 지재원 관리자)"로 명시. G4-html 검수 라우트(`/golden/jobs/{id}/review.html`)는 배선 완료. (감사 중 1차 오분류 정정) |

### 재확인된 잔여 드리프트 (코드로 안 고침 — 이유 있음)

- **라이브 `.env`가 `VECTOR_BACKEND=es`+`STORAGE_BACKEND=minio`** — dev 작업용 .env. 결정 방향은 프로파일(local+pg)이나, **ES→PG 재검증 게이트(Top-8 #8) 미실행** 상태라 .env를 pg로 돌리면 미검증 경로. 게이트 통과 후 전환(의도적 보류).
- **활성모델 v-dd3abab9 무보정(T=1.0)** — 활성 dir에 temperature.json 부재(temperature.json은 무관한 bpilot 모델에만). 커밋 d1aac27 "bpilot 2.0 복사금지"와 정합. 실 GPU 학습 1회로 생성 필요(NEW-H3·Top-3, 외부 의존).
- **es_store.py 잔존 `tenant_id` 매핑 필드** — 미사용·ES 폐기대상이라 저영향(정리 시 함께 제거).

---

## (A) 핵심 미완 기능 · 마이그레이션

- [ ] **A-1. ES→PG(pgvector+pg_bigm) 단일화** — `pg_store.py` EXPERIMENTAL 미검증 스캐폴드, test_pg_store.py 0건. 완료: ①이미지 오프라인 빌드 ②NL 재검증 쿼리셋 ③`revalidate_pg_lexical.py` ④R@5 nori~94% ±5pp ⑤default 전환+문서 반영. 갭: ts_rank IDF 없음(~80-85%)+tsvector `simple`. >10pp 퇴행 시 경로 ⓒ(ParadeDB/VectorChord-bm25).
- [ ] **A-2. API 비동기/배치 경로 Celery 미발사** — `training_service.py:266-273`, `synthesis_service.py:63`, `async_classify_service.py:58`. task는 `workers/tasks.py`에 이미 존재 → `.delay()` 배선만.
- [ ] **A-3. 합성 리뷰 큐·데이터셋 연계** — `/synth/queue` 승인/반려 미구현, 승인건 학습셋 버전 미연결, 가이드 retraining 트리거 항상 False(`guide_service.py:199`).
- [~] **A-4. FUN-005 "2단계 XAI" = 요건 오귀속(스킵)** — RTM 접지(2026-07-06): FUN-005는 "출처+콘텐츠 2단계 **분류**"(metadata_floor+분류기)로 **완료** 상태이며, "2단계 판정"을 "2단계 설명(XAI)"으로 혼동한 것. RTM/제안요청서(FUN-003·004·005·022·023·024)에 XAI·LLM 자연어 설명 요건 **없음**. stage-1 feature attribution(`api/explain.py`)로 족함. 요건-스코프 규율상 신규 LLM 설명기능 = 초과기능 → **스킵**.
- [ ] **A-5. A안 5요소 100점 → 등급 환산표 미정의/미구현** — RTM "미구현(선택)", K8 환산 로직 미정.
- [ ] **A-6. Secrets Manager GCP 백엔드 미구현** — env/vault/aws만.

---

## (B) 외부 의존성 · 설치 필요 (폐쇄망 사전 프로비저닝)

> ⚠️ **HWP 파서는 존재**(`extractor.py:93-111`, rhwp-python, test_hwp_extractor.py). 남은 건 구현이 아니라 의존성 프로비저닝.

- [x] **HWP/HWPX (`[hwp]`/rhwp)** — 구현·dev설치 완료. ~~Dockerfile.api가 extras 없이 빌드 → 이미지 미설치~~ → **2026-06-24 D-1 수정으로 `.[hwp,ocr,jwt]` 동봉**. 남음: 폐쇄망 rhwp wheel 사전 동봉 + 실 HWP 검증(UAT).
- [ ] **OCR (`[ocr]` pytesseract+pdf2image+Tesseract+kor+poppler)** — venv MISSING. 미설치 시 스캔PDF/이미지 → 빈 텍스트. (이미지엔 D-1로 포함). 폐쇄망: 엔진+kor.traineddata+poppler.
- [ ] **레거시 .xls (xlrd)** — 어떤 extra에도 미배선. 수동 `pip install xlrd` 또는 .xlsx 변환.
- [ ] **.doc(antiword) / 레거시 .ppt** — `.doc`는 시스템 antiword 필요; **.ppt는 지원 경로 자체 없음**(수동 변환).
- [ ] **임베딩 모델(KURE-v1/BGE-M3 ~2GB)** — 로드 실패 시 HashEmbedding 무성 폴백(검색 붕괴). 오프라인 가중치 동봉.
- [ ] **Reranker (`[reranker]`)** — 기본 noop. `get_reranker`가 import 실패 미catch → bge+미설치 시 raise(타 팩토리와 불일치). 효과 ~+1pp.
- [ ] **LLM provider** — 기본 noop(label_match ~25%). 실 provider 또는 온프렘 vLLM/Ollama.
- [ ] **온프렘 vLLM(Qwen3-14B@8001)** — airgap.yml이 `host.docker.internal:8001` 참조하나 어떤 compose에도 vllm 서비스 정의 없음.
- [ ] **Vault/AWS secrets(hvac/boto3)** — 둘 다 MISSING, env만 동작.
- [ ] **JWT (`[jwt]` PyJWT)** — 이미지엔 D-1로 포함. prod JWT 설정(issuer/audience/jwks) 주석처리 → 배선 필요.
- [ ] **저장 암호화** — `storage_encryption_enabled=False` → 영업비밀 원본 평문 저장. 활성화+키 프로비저닝.

---

## (C) 실데이터 · 실인프라 측정 미완 (평가/게이트 증거 공백)

- [ ] **C-1. 인간검증 골든셋 0/40 ★** — 리뷰 큐 120행 존재하나 review_decision 미기입·미임포트. 라벨 순환성 → 현 F1/FNR 미검증 OOD. 운영 준비도 CONDITIONALLY_READY.
- [ ] **C-2. 실문서(회원사) 미확보 ★** — 실문서 FNR/Recall/HWP 품질 "미측정". WBS 7월 착수 전제(A5·KL ICD).
- [ ] **C-3. 합성 데이터 파이프라인 미실행** — 5,000건 풀 확장 미실행(smoke 40건만), `datasets/labeled/` 부재 → `make p1` 불가.
- [ ] **C-4. 온도 보정 파이프라인 끊김** — trainer가 per-row logits dump 안 함 → calibrate 더미만. 완료: trainer val logits export → calibrate → temperature.json 배치.
- [ ] **C-5. 검색/응답 품질 게이트 데이터 부재** — `answer_gold` 부재, relevance gold 코드 밖(DB 메트릭 항상 None), P2 Recall@5 실문서 0.62~0.72 < 0.80.
- [ ] **C-6. holdout 누출 정제본 미커밋** — `clean_holdout_leakage.py`가 109건 중 67건(42%) 누출 발견, `.clean.jsonl` untracked, eval은 여전히 오염 원본 참조. → 커밋+게이트 배선.
- [ ] **C-7. 대규모 GPU full-train E2E 미수행** — 소형 7/7만 PASS. 운영점 값은 골든셋 500건 PR곡선 확정 전 미정.
- [ ] **C-8. 발주처 GPU 인프라 미확인** — A100×2 확정이나 CUDA/Driver/`--gpus all` 미확인.

---

## (D) 운영 · 릴리스 블로커

- [x] **D-1. Dockerfile.api extras 누락** — ~~OS 바이너리만 있고 Python 글루 미설치~~ → **2026-06-24 `pip install -e ".[hwp,ocr,jwt]"` 로 수정.** (검증: 직접 확인.)
- [ ] **D-2. Celery beat/worker·Redis 미배선** — `docker-compose.prod.yml`에 beat 없음 → drift·자동롤백·outbox·파티션 자동화 정지. Redis 부재 시 in-memory 폴백(멀티워커 미공유).
- [ ] **D-3. 프레시 배포 DB에 active ModelVersion 없음** → metrics/latest 404. `seed_active_model_version.py`(untracked) 수동 우회.
- [ ] **D-4. 감사체인 HMAC 비밀키 미설정(NFR-SEC-01)** — 키 없는 sha256 → 과거 row 재서명 가능. `.env.prod.example`에 키 라인 없음. fail-fast·정기 무결성검증 미구현.
- [ ] **D-5. 모델 버전 드리프트(문서 vs 코드)** — `.env` 주석("v-437ec196 채택")과 실제 활성(v-dd3abab9) 모순. 카드 재지정+승격 결정(A6).
- [ ] **D-6. 자동확정/롤백/메타floor/escalation τ 전부 기본 OFF ★** — τ=0.10이면 FNR 0.028(충족) vs 기본 argmax 0.167. 메커니즘 완성·테스트됨 → 활성화+임계 재보정(human_review 후). 부수: deploy_gate 최초배포 FNR floor 없음; serving_eval 예외 전부 TS 라벨 → FNR=0 거짓 inflate.
- [ ] **D-7. 드리프트 모니터 실배포 no-op** — `sample_vectors`가 InMemoryStore에만 구현, ES엔 없음. embedding 기본 'hash'.
- [ ] **D-8. 라이선스 운영전환 미결** — PyMuPDF AGPL([pdf-agpl] 잔존), MinIO AGPL 법무답변 미확정(SeaweedFS 어댑터는 추가).
- [ ] **D-9. 관측성(OTel/Prometheus) 미배선** — OTEL_/PROM_ env 키 없음, compose에 collector/pushgateway 없음.
- [ ] **D-10. 백업/DR 실측 미수행** — RTO 4h 리허설 0회, chaos/DR 시나리오 placeholder(`pass`). SLA·서킷브레이커·SLO 미정.
- [ ] **D-11. KOIPA 폐쇄망 매체 반입 절차 미확인** (외부 의존, blocked).
- [ ] **D-12. 오프라인 번들 운영문서 누락** — OPERATION.md/TROUBLESHOOTING.md 선언만, `poc/docs/`엔 INSTALL.md만.
- [ ] **D-13. 테넌트 RLS·기본 저장암호화 미적용(NFR-SEC-02)**.

---

## (E) 문서 / WBS / RTM 잔여 · 발주처 결정 미결

- [ ] **결정 미결 다수** — 발주처 A1~A7(A5/A7 제외 미결), KL K1~K8 전부 미결. 핵심: A2(v2.2 곱셈보정 활성), A3(90% 정확도 측정기준), A7(골든셋 라벨링 주체), R6(KL 보안표시 ICD — metadata_floor 입력 전제).
- [ ] **RTM "정밀화"(미완)** — FUN-004(실데이터 라벨·FNR 7~8월), FUN-024(검수자 큐·능동학습 폐곡선 실측).
- [ ] **FUN-003 스펙 편차** — 설계는 LangChain+PydanticOutputParser, generator.py는 자체 어댑터+수동 JSON. 기능 동등, KL 합의 필요.
- [ ] **FUN-022 검증 공백** — 5포맷 중 검증 증빙 3개만, XLSX/PPTX 커버리지 확인.
- [~] **FUN-024 "PDF 리포트" = 요건 오귀속(스킵)** — RTM 접지(2026-07-06): FUN-024는 "검수자 큐 배분·심층지표"(다단 게이트·메타floor·검수 라우팅·FNR 관리·운영 확정 환류 = 거버넌스 폐곡선, status=정밀화)이며 **PDF 출력 요건 없음**. RTM의 "PDF"는 입력 파싱포맷(FUN-022 HWP/DOCX/PDF/XLSX 추출)·요건출처 가이드 문서뿐. HTML+브라우저 인쇄로 족함 → PDF 출력 = **스킵**.
- [ ] **한국어 정규화 스택 미구현** — normalizer는 NFKC+regex+8단어 불용어만. `[nlp]` extra dead(미import).
- [ ] **Nori 사용자 사전 수치 불일치** — 본문 ~58항목 vs 노트 "1,002항목 동결".
- [ ] **doc/15 정합성 불일치** — pytest 수(385/570/912)·라우트 수(23/31) 문서간 불일치, 단일 출처 정리.
- [ ] **골든셋 v3 확장(인사·재무 S1 사각지대)** — 8월 예정·미착수(golden500 S1→S2 미탐 7건 전부 이 유형).

---

## (F) 코드 TODO · 스텁 · 부분구현 (저영향, 정리 대상)

- [ ] **rule_engine semantic 경로 dead code** — 출하 시드에 `pattern_type=semantic` 없음.
- [ ] **PII NER 마스킹 사실상 부재** — `_mask_with_ner`가 NER head 없는 `klue/bert-base` 지정 + `use_ner` 기본 False → 인명/기관명 미마스킹.
- [ ] **M2 split_v2 미배선** — RAG indexer는 v2, M2 PreprocessPipeline은 v1 → ingestion heading_path 메타 누락.
- [ ] **LLM labeler fallback 기본 OFF** — 모든 진입점 `use_llm_fallback=False`.
- [ ] **`_svm_confidence` 미검증 휴리스틱** — "골든셋 누적 후 상관 검증 필요" 마커.
- [ ] **m4_training query_expansion.py/rag_indexer.py** — 순수 backward-compat shim(실구현은 lloydk.rag.*).
- [ ] **storage 키 위생 불일치** — SeaweedFS `_norm_key`가 delete()에만; Minio는 정규화 가드 없음.
- [ ] **citation enforcement 기본 OFF + config 필드 없음** — `answer_enforce_citations`가 Settings에 미정의.
- [ ] **RAG answer 기본 결정론 폴백** — noop이면 `/answer`가 citation-list 스텁 반환.
- [ ] **InMemoryStore hybrid** — naive `\w+` 토크나이저(한글 통째), ES 장애 시 무성 저품질 폴백.

---

## (G) PoC 납품 범위 밖 (참고)

- **DriftKeeper 검색품질 자가교정 시스템** — 코드 0줄, 기획서(v0.1)만. 별도 사업 IP, "수요 미검증 → 풀빌드 금지"로 의도적 미착수. 본 PoC 범위 밖.
