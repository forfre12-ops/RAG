# 야간 무중단 작업 종합 보고서 — 2026-05-30

5070 Ti 풀가동 PoC 옵션 C 야간 자동 진행 결과 보고. 사용자 취침 후 자동 작업.

- 시작: 01:15 (Phase 4 P5 측정 진입)
- 예상 완료: 06:00~08:00 (Phase 5·7 완료 + 종합 보고)

---

## 사전 합의 결정 5건 (시작 전)

| # | 결정 | 채택 |
|---|---|---|
| 1 | LLM 1순위: kappa 차이 ≤ 0.05면 Qwen3 유지 | ✅ |
| 2 | 합성 분량: 200건 × 2 모델 (시간 절약) | ✅ |
| 3 | Phase 6 P4 HWP 보류 | ✅ |
| 4 | B2 재학습 보류 | ✅ |
| 5 | commit 정책: phase별 분리 | ✅ |

---

## 진행 단계 (시간순)

| 시각 | 작업 | 결과 | commit |
|---|---|---|---|
| 01:15 | Phase 4 P5 E2E 측정 | RAG OFF 380ms · RAG ON 9.2s · 4/4 정합 | TBD |
| 01:30 | B1 MLflow Dockerfile + SQLite backend | HTTP 200 OK | 04dffc9 |
| 01:35 | Phase 5.1 Qwen3 합성 시작 | (진행) 1.15분/건 | — |
| ~04:30 | Phase 5.1 Qwen3 200건 완료 (예상) | TBD | — |
| ~04:30 | Phase 5.2 Solar 합성 시작 (자동 전환) | overnight script | — |
| ~07:30 | Phase 5.2 Solar 200건 완료 (예상) | TBD | — |
| ~07:45 | Phase 5.3 라벨 일치도 측정 + 1순위 자동 확정 | TBD | TBD |
| ~08:00 | Phase 7 데모 §5 최종 갱신 + commit | TBD | TBD |
| ~08:15 | 종합 보고서 + 메모리 + 최종 commit | TBD | TBD |

---

## Phase 4 P5 E2E 측정 ✅ (완료)

| 모드 | 평균 latency | 합격선 | 결과 |
|---|---|---|---|
| RAG OFF | 380ms | ≤ 10s (V2 §14.2) | ✅ 26× 여유 |
| RAG ON | 9.2s | ≤ 30s | ✅ 3.3× 여유 |
| 정합 | 4/4 | TS·S1·S2·S3 | ✅ |

단계별 분해 (SSE):
- llm (BERT 추론) 99.2% (8,613ms/8,683ms)
- 나머지 (extract+normalize+embed+retrieve+persist+finalize) < 1%

---

## B1 MLflow 정착 ✅ (완료, commit 04dffc9)

- `poc/infra/mlflow/Dockerfile.mlflow` 신설: v2.16.2 + psycopg2-binary
- `docker-compose.yml`: image → build 전환, lloydk/mlflow:2.16.2-psycopg
- backend: SQLite로 변경 (우리 lloydk DB alembic head 충돌 회피)
- mlflow_data named volume 신규
- 검증: http://localhost:5000/health HTTP 200 OK

---

## Phase 5 Qwen3 vs Solar (진행 중)

### Qwen3-14B-Q4 200건 진행 상황 (자동 폴링)

TBD - 완료 후 자동 채워질 부분

### Solar-10.7B-Q4 200건 진행 상황 (Qwen3 완료 후 자동 시작)

TBD

### 1순위 자동 확정 결과

TBD

---

## Phase 7 부분 완료 (계속 진행)

- `doc/17_진척_대시보드.html` 갱신 (5070 Ti 풀가동 Phase 0~4 완료 행 추가)
- `doc/17_진척_대시보드_외부공유.html` 갱신 (톤다운 외부공유본)
- `doc/31_5070ti_자체측정_요약_1페이지.md` 신설 (발주처 회의 자료)
- 데모 §5 P5 카드 추가 (실측 비율 79% → 80%)

---

## 야간 자동화 인프라

- `scripts/overnight_solar_after_qwen3.sh` 신설:
  - Qwen3 완료 폴링 (최대 6시간) + 정체 감지
  - .env LOCAL_LLM_MODEL 자동 전환
  - Solar 합성 자동 시작
  - 완료 후 .env 복원

---

## 사용자가 아침에 확인할 것

1. **이 보고서** — 야간 진행 전체 흐름
2. `git log --oneline -10` — 야간 commit 4~6건
3. `doc/31_5070ti_자체측정_요약_1페이지.md` — 발주처 회의 자료
4. `/demo/` 라이브 진입 — §5 실측 비율 80%+ 확인
5. `report/phase4_p5_e2e.md` + `report/phase5_p3_comparison.md` — 상세 보고서
6. MEMORY.md — 야간 메모리 등록 확인

---

## 잠재 결함 (발생 시 보고)

발생 시 이 섹션 자동 갱신:

| 시각 | 사고 | 처리 |
|---|---|---|
| — | — | — |

---

## 5070 Ti 풀가동 PoC 누적 진척

| Phase | 완료 시점 | 핵심 산출 |
|---|---|---|
| 0 | 2026-05-30 00:30 | torch nightly + Nori + Qwen3·Solar 다운 (commit 14f38af) |
| 1 | 2026-05-30 01:00 | 4 컨테이너 healthy + Qwen3 첫 답안 (commit ad5f285) |
| 2 | 2026-05-30 01:10 | BGE-M3 1순위 0.7222 (commit 774e984) |
| 3 | 2026-05-30 01:15 | KF-DeBERTa 33s 학습 + wiring (commit 863f69b) |
| 4 | 2026-05-30 01:30 | P5 E2E RAG ON 9.2s (TBD commit) |
| B1 | 2026-05-30 01:45 | MLflow SQLite (commit 04dffc9) |
| 5 | 진행 중 | Qwen3 vs Solar 200건 비교 |
| 7 | 부분 완료 | doc/17 + 발주처 1페이지 |

---

**보고서 자동 갱신은 야간 작업 진행에 따라 추가됨.**
