# Phase 2 — KURE / BGE-M3 / dragonkue 3-way 임베딩 실측

5070 Ti 풀가동 PoC의 Phase 2 결과. **BGE-M3 dense 1순위 확정**, 합성 5K 환경 천장 0.7222 3-way 재확인.

- 일자: 2026-05-30
- 환경: onprem-local + ES 8.15.3 + Nori
- 측정 스크립트: `poc/scripts/p2_compare_embeddings.py --mode full --backends es --hybrid --models bge-m3,KURE,dragonkue`
- 코퍼스: `datasets/synthetic_5k` (5,000건)
- 쿼리: 시드 키워드 18종 (4 등급 × 4.5)
- 산출: `reports/p2_3way_2026-05-30.md`

---

## 측정 결과 (6 조합)

| 임베딩 | 검색 | Recall@5 | p50 ms | p95 ms | avg ms | 인덱싱 ms |
|---|---|---|---|---|---|---|
| **BAAI/bge-m3** | **dense** | **0.7222** | **111** | 126 | 108 | 2,667 |
| BAAI/bge-m3 | hybrid | 0.7222 | 172 | 203 | 176 | (재사용) |
| nlpai-lab/KURE-v1 | dense | 0.6667 | 113 | 131 | 111 | 2,314 |
| nlpai-lab/KURE-v1 | hybrid | 0.7222 | 175 | 182 | 175 | (재사용) |
| **dragonkue/BGE-m3-ko** | dense | 0.7222 | 117 | 124 | 113 | 2,221 |
| **dragonkue/BGE-m3-ko** | hybrid | 0.7222 | 170 | 179 | 171 | (재사용) |

verdict: **FAIL** (V2 §14.2 합격선 0.80 미달)

---

## 핵심 발견 (정직)

### 1. 5/6 조합이 Recall 0.7222 동일 — 합성 5K 천장 그대로

[project_koipa_p2_intent_proof.md](memory/project_koipa_p2_intent_proof.md)에 정량 입증된 사실 — "진짜 천장 원인은 합성 5K 코퍼스 본문 boilerplate. 합격선 도달은 발주처 LLM API 키 → P3 풀 합성 → 본문 다양화 → 0.80 도달 시도가 결정적" — **진짜 임베딩(hash 폴백 아님)에서도 그대로 재현**. 알고리즘 한계 X, 데이터 한계 O.

### 2. KURE-v1 dense는 BGE-M3 대비 -0.0555 (역전)

V2 §5 가정 ("KURE-v1 1순위, BGE-M3 베이스") 과 우리 측정값이 다름:
- 외부 KURE 공식 벤치([github.com/nlpai-lab/KURE](https://github.com/nlpai-lab/KURE)): KURE 0.687, BGE-M3 0.675 (KURE +0.012)
- 우리 합성 5K: BGE-M3 0.7222, KURE 0.6667 (BGE-M3 +0.0555)

**해석**: 외부 벤치(MIRACL/Ko-StrategyQA)와 우리 합성 5K 코퍼스 분포가 다름. 보일러플레이트 합성에서는 BGE-M3의 다국어 일반성이 KURE의 한국어 fine-tune 대비 더 안정. V2 1순위 가정 갱신 필요.

### 3. KURE는 hybrid에서만 dense 동등 회복

| KURE 모드 | Recall@5 |
|---|---|
| dense | 0.6667 |
| hybrid (BM25+RRF) | 0.7222 (+0.0555) |

→ KURE의 dense 약점이 BM25로 보완됨. BGE-M3와 dragonkue는 dense에서 이미 0.7222라 hybrid 추가 우위 없음. **hybrid는 KURE 사용 시 한정 효과**.

### 4. dragonkue/BGE-m3-ko 한국어 fine-tune 무효

dragonkue (한국어 RAG fine-tune)와 base BGE-M3 모두 0.7222 동일. 합성 5K 환경에서는 한국어 특화 fine-tune이 추가 우위를 못 만듦. AutoRAG 금융 벤치(NDCG +0.09 보고)와는 다른 결과.

### 5. 검색 latency: dense 110ms / hybrid 170ms

| 모드 | p50 | p95 |
|---|---|---|
| dense | 110~117ms | 124~131ms |
| hybrid | 170~175ms | 179~203ms |

V2 §14.2 합격선 ≤ 200ms (5만 청크 기준). **우리 5K 환경에서는 모두 합격선 내** (dense 우위).

---

## 1순위 확정

| 후보 | 종합 평가 | 선택 |
|---|---|---|
| **BAAI/bge-m3** | dense 0.7222 + p50 110ms(가장 빠름) + 안정적 인덱싱 + 다국어 일반성 | ✅ **1순위** |
| dragonkue/BGE-m3-ko | BGE-M3 동등, 우위 없음. 한국어 fine-tune 효과 0 | 2순위 (실문서 분포 변화 시 재평가) |
| KURE-v1 | dense 0.6667 미달, hybrid 의존 | 3순위 (실문서 도착 후 외부 벤치 우위 회복 가능성 검증) |

`.env`는 이미 `EMBEDDING_MODEL=BAAI/bge-m3`로 설정됨 (Phase 1).

---

## 합격선 미달 보고

V2 §14.2 P2 합격선 **Recall@5 ≥ 0.80** 미달.

### 정량 입증 (이미 확보)

[project_koipa_p2_intent_proof.md](memory/project_koipa_p2_intent_proof.md)에서 의도형 쿼리 30종이 시드 쿼리 대비 오히려 -0.067~-0.133 하락한 사실을 정량 입증함. **천장 원인은 알고리즘이 아닌 합성 코퍼스 본문 boilerplate**.

본 Phase 2 측정은 그 입증을 **진짜 임베딩(KURE/BGE-M3/dragonkue)**으로 재확인했음. hash 폴백이 아닌 5070 Ti GPU 추론으로 실측한 값도 동일 천장 → "임베딩 한계 X, 데이터 한계 O" 결론 강화.

### 합격선 도달 결정 경로

발주처 자원 도착 순서:
1. **LLM API 키 (Q5)** 도착 → P3 풀 LLM 합성 800건 추가 → 본문 다양화 → Recall 갱신 시도
2. **실문서 (Q4)** 도착 → 실 KOIPA 분포로 재측정 → 외부 벤치 우위 회복 가능성 검증
3. **둘 다 도착 X** → 현 0.7222 천장 그대로, 발주처 보고 시 합성 한계 정량 입증으로 사유 명시

---

## 데모 콘솔 갱신

`poc/src/lloydk/api/static/app.js` capability stats:
- 신규 카드 1: `0.7222 — Recall@5 BGE-M3 dense ES (P2 3-way 실측, 합성 5K 천장)` **src=measured**
- 신규 카드 2: `111ms — 검색 latency p50 (BGE-M3 dense ES, 실측)` **src=measured**
- BERT 0.05~0.2s 참고값은 Phase 3 학습 후 실측치로 교체 예정

데모 §5 stat grid 14 카드 중 실측(src-measured) 비율: 9/14 (64%) → **다음 단계 목표 70%+**

---

## 회귀 안정성

```
poc/tests/test_demo_page.py: 9/9 PASS (2.4s)
```

데모 카드 갱신에도 안정.

---

## Phase 3 진입 준비도

| 사전 요건 | 상태 |
|---|---|
| 5070 Ti GPU 학습 가능 (sm_120) | ✅ Phase 0 검증 |
| labeled 5K 데이터셋 | ✅ train 3500 / val 748 / test 752 |
| transformers + autograd | ✅ Phase 0 BERT 추론 검증 |
| `scripts/p1_train_classifier.py --mode full` | ✅ |
| KF-DeBERTa-base 자동 다운 | Phase 3 첫 실행 시 |
| MinIO `lloydk-models` 버킷 (가중치 보관) | ✅ Phase 1 생성 |

다음: **Phase 3 — KF-DeBERTa-base 합성 5K 학습 + P1 F1/FNR 측정**.

예상 소요: 1일 (학습 30분 ~ 2시간 + 평가 + 데모 wiring).
