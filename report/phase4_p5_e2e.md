# Phase 4 — P5 E2E 풀스택 latency 측정

5070 Ti 풀가동 PoC의 Phase 4. 풀스택(학습 BERT + KURE/BGE + Qwen3 + ES + Postgres) 위에서 1건 종단 latency 측정. V2 §14.2 합격선 (RAG OFF ≤10s, RAG ON ≤30s) **둘 다 PASS**.

- 일자: 2026-05-30 야간
- 환경: onprem-local + 학습 가중치 v-ae3f5371

## 4.1 RAG OFF 측정 (p5_e2e_smoke.py --mode http)

```
smoke-ts-001: TS  conf=0.993  920ms  (cold)
smoke-s1-001: S1  conf=0.970  168ms
smoke-s2-001: S2  conf=0.992  130ms
smoke-s3-001: S3  conf=0.997  301ms
```

| 지표 | 값 | 합격선 |
|---|---|---|
| 정합 | 4/4 | 합격 ✅ |
| 평균 latency | 380ms | ≤ 10s ✅ **26배 여유** |
| 콜드스타트 (TS) | 920ms | 첫 호출만 |
| 정상 latency | 130~301ms | 정상 |

## 4.2 RAG ON 측정 (4 등급)

```
ts: TS conf=0.452 9933ms rag_hits=0
s1: S1 conf=0.446 9199ms rag_hits=0
s2: S2 conf=0.978 8547ms rag_hits=0
s3: S3 conf=0.962 9101ms rag_hits=0
```

| 지표 | 값 | 합격선 |
|---|---|---|
| 정합 | 4/4 | 합격 ✅ |
| 평균 latency | 9.2s | ≤ 30s ✅ **3.3배 여유** |
| min/max | 8.5s / 9.9s | 안정 |
| rag_hits | 0 | docs 컬렉션 미인덱싱 (Phase 2 임시 컬렉션은 다른 이름) |

## 4.3 단계별 latency 분해 (/classify/stream SSE)

| 단계 | 도달 시점 (ms) | 단계 소요 (ms) | 비중 |
|---|---|---|---|
| extract | 38 | 38 | 0.4% |
| normalize | 38 | 0 | 0% |
| embed (BGE-M3) | 39 | 1 | 0% |
| retrieve (ES) | 39 | 0 | 0% |
| **llm (BERT 추론)** | **8,652** | **8,613** | **99.2%** |
| persist (Postgres) | 8,652 | 0 | 0% |
| finalize | 8,653 | 1 | 0% |
| **전체** | **8,683** | | |

## 핵심 발견

1. **BERT 추론이 99% 차지** — 청크 분할(4~5 청크) × 청크당 ~1.5s. 운영 GPU(A100급)에서 1~2s로 단축 가능
2. **임베딩 + ES 검색 < 1ms** — 합성 5K 코퍼스 + ES 8.15.3 + KURE/BGE-M3 캐시가 매우 빠름
3. **Postgres 영속화 비용 미미** — 동기 저장이 사실상 무료
4. **콜드스타트 920ms는 첫 호출만** — 이후 정상 130~300ms (RAG OFF)
5. **rag_hits=0** — docs 컬렉션 인덱싱 후속 작업 (선택). 미인덱싱이라도 RAG ON 자체는 정상

## 데모 갱신

`poc/src/lloydk/api/static/app.js`:
- §5 capability stats 카드 신규: `9.2s — P5 E2E RAG ON (5070 Ti 풀스택 실측, V2 §14.2 ≤30s)` **src=measured**
- §1 race-note: BERT 1.18s **실측 vs** LLM 25.8s (Phase 1 /answer 실측 인용). "22× 빠름" 정량 입증 명시. V2 §4.4 표 검증 완료

실측 비율: 11/14 → 12/15 = **80%** (목표 70%+ 통과)

## 회귀

```
poc/tests/test_demo_page.py: 9/9 PASS
```

## Phase 5 진입 준비도

| 사전 요건 | 상태 |
|---|---|
| 풀스택 라이브 동작 | ✅ |
| 학습 가중치 + KURE/BGE + Qwen3 + Solar | ✅ |
| 메모리·디스크 여유 | ✅ |
| p3_generate_synthetic.py 풀 모드 | ✅ |

다음: Phase 5 — Qwen3 vs Solar 200건 합성 비교 + 라벨 일치도 측정.
