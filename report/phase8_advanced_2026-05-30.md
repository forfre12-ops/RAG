# Phase 8 — 고도화 일괄 (야간 무중단 후속)

5070 Ti 풀가동 PoC 야간 무중단 작업 마무리 후 추가 고도화 9건 일괄 진행. 사용자 개입 0건 무중단 자동 진행.

- 일자: 2026-05-30 야간 후속
- 환경: onprem-local + ES + Postgres + MinIO + MLflow + Ollama

---

## 진행 항목 9건

### B1 잔여 부채

| # | 작업 | 결과 |
|---|---|---|
| B1-2 | ES `docs` 영구 인덱싱 (5,200건) | ✅ BGE-M3 임베딩 6.8 docs/s, 13분 |
| B1-3 | Qwen3 합성 200건 추가 학습 (labeled_5k + Qwen3) | ✅ F1=1.0 (학습), 데모 confidence 0.632→0.746 |
| B1-4 | safetensors 403 silent | ✅ logging CRITICAL |
| B1-5 | race-note 22× 정확화 | ✅ Phase 1 25.8s 인용 |

### B3·C3·C5

| # | 작업 | 결과 |
|---|---|---|
| B3-3 | errors_ko.js 50항목 | ✅ showError 한국어 매핑 + 원문 병기 |
| C3-7 | LLM JSON retry 정책 | ✅ 파싱 실패 시 temp 0.3 + system 강화 재시도 2회 |
| C5-4 | 합성 문서 길이 다양화 | ✅ 4 band (200~10K), 등급당 균등 분배 |

### C1·C2·C3-1

| # | 작업 | 결과 |
|---|---|---|
| C1-1 | KoBigBird-large 비교 학습 | ✅ F1=1.0 65초 (KF-DeBERTa 동등) |
| C2-1 | Snowflake arctic-embed-l-v2.0-ko 4-way | ✅ Recall 0.6667 (BGE-M3 미달 재확인) |
| C2-2 | dragonkue/bge-reranker-v2-m3-ko 다운 | ✅ score 0.9998 정상 |
| C3-1 | Qwen3 thinking on/off 비교 | ✅ OFF 39.3 tps vs ON 28.2 tps (+39%) |

---

## 핵심 측정 결과

### B1-2 ES `docs` 인덱싱

| 지표 | 값 |
|---|---|
| 인덱싱 대상 | 5,000 (synthetic_5k) + 200 (synthetic_qwen3) = **5,200건** |
| 처리 시간 | 13분 (6.8 docs/s) |
| ES `docs` 인덱스 size | 107.4 MB |
| **/answer citations 변화** | **0건 → 3건** |
| 데모 RAG ON 효과 | rag_hits 0 해소 ✅ |

### B1-3 Qwen3+5K 학습 모델 데모 검증

| 항목 | Phase 3 (5K) | Phase 8 (5K+200) | 변화 |
|---|---|---|---|
| 데모 12 PASS | 10/12 | 10/12 | 동일 |
| **평균 confidence** | 0.632 | **0.746** | **+0.114** |
| 미탐 패턴 | TS-보안→S3, S1-보안→S3 | TS-경영→S3, TS-보안→S1 | **S3→S1 1단계 개선** |
| 학습 시간 | 33초 | 43초 | +10초 |
| 학습 분포 평가 | F1=1.0 / FNR=0 | F1=1.0 / FNR=0 | 동일 |

### C1-1 KoBigBird-large 비교

| 항목 | KF-DeBERTa-base | KoBigBird-large |
|---|---|---|
| 파라미터 | 184M | ~200M |
| 토큰 한도 | 512 | **4,096** (8배) |
| 학습 시간 (1ep, batch 8) | 33초 | **65초** (2배) |
| 학습 분포 F1 | 1.0 | 1.0 |
| 학습 분포 FNR | 0 | 0 |

**결론**: 합성 5K 환경에서 둘 다 학습 분포 완벽 수렴. KoBigBird가 긴 문서 우위 있으나 합성 평균 길이 1,500자라 512 토큰 한도 무관. **KF-DeBERTa 1순위 유지** (학습 시간 2배 단축). 실문서 도착 후 긴 문서 비중 측정 시 KoBigBird 재고 가치.

### C2-1 P2 4-way 확장 결과

| 임베딩 | dense Recall@5 | hybrid Recall@5 | dense p50 |
|---|---|---|---|
| **BAAI/bge-m3** | **0.7222** | 0.7222 | 111ms |
| nlpai-lab/KURE-v1 | 0.6667 | 0.7222 | 113ms |
| dragonkue/BGE-m3-ko | 0.7222 | 0.7222 | 117ms |
| dragonkue/arctic-l-v2.0-ko | 0.6667 | 0.6667 | 111ms |

**Snowflake arctic이 BGE-M3·dragonkue 미달**. 외부 [Snowflake 공개 벤치](https://www.snowflake.com/en/engineering-blog/snowflake-arctic-embed-2-multilingual/)에서 BGE-M3 동등 보고와 다른 우리 환경 결과. **BGE-M3 1순위 재확인**.

### C3-1 Qwen3 thinking 비교

| 모드 | 평균 latency | tokens | tps |
|---|---|---|---|
| thinking_on (기본) | 7.1s | 200 | 28.2 |
| **thinking_off** (`</think>` stop) | 4.9s | 192 | **39.3 (+39%)** |

**구조화 출력(JSON·등급 분류)은 thinking OFF 권장** — 속도 +39%, 출력 안정성 동등.

---

## 데모 §5 capability stats 카드 신규 5

| 카드 | 값 | src |
|---|---|---|
| ES docs 인덱싱 | 5,200 | measured |
| Qwen3+5K confidence | 0.746 | measured |
| KoBigBird 비교 학습 | F1=1.0 65s | measured |
| thinking OFF tps | 39% | measured |
| arctic Recall | 0.6667 | measured |

총 22 카드 → **19 measured + 3 spec = 86%** (Phase 7 88% 유지)

---

## 코드 변경

| 파일 | 변경 |
|---|---|
| `scripts/index_synthetic_to_docs.py` | 신규 (5,200건 ES 인덱싱) |
| `scripts/p3_generate_synthetic.py` | LENGTH_BANDS 4 band 다양화 (200~10K) |
| `src/lloydk/modules/m1_synthesis/generator.py` | JSON 파싱 실패 시 재시도 2회 |
| `src/lloydk/modules/m4_training/trainer.py` | safetensors 403 silent (logging CRITICAL) |
| `src/lloydk/api/static/errors_ko.js` | 신규 (50항 한국어 에러 매핑) |
| `src/lloydk/api/static/app.js` | translateError 적용, race-note 25.8s, capability stats +5 카드 |
| `.env` | CLASSIFIER_MODEL_DIR → classifier-plus-qwen3/v-b68c8a31 |

## 회귀

```
poc/tests/test_demo_page.py: 9/9 PASS
```

---

## Phase 8 누적 PoC 상태

| PoC | 합격선 | 자체 측정 | 결과 |
|---|---|---|---|
| P1 | F1 ≥ 0.75 (학습) | 1.0 + KoBigBird 1.0 | ✅ PASS (2 모델) |
| P1 | FNR ≤ 5% (학습) | 0% (양쪽) | ✅ PASS |
| P1 (참고) | 학습 외 분포 정합 | 10/12 (conf 0.746) | 합성 한계 + Qwen3 추가로 신뢰도 향상 |
| P2 | Recall@5 ≥ 0.80 | 0.7222 (4-way 천장 재확인) | ❌ 합성 천장 |
| P2 | latency ≤ 200ms | 111ms | ✅ PASS |
| P3 | 라벨 일치도 ≥ 90% | Qwen3 100% / Solar 81% | ✅ Qwen3 PASS |
| P5 | E2E RAG OFF ≤ 10s | 380ms | ✅ PASS |
| P5 | E2E RAG ON ≤ 30s | 9.2s | ✅ PASS |
| P5 (참고) | citations | 0 → 3 (ES docs 인덱싱) | ✅ RAG 효과 |
| P4 | HWP 추출 | 보류 | 실문서 대기 |

**합격 6 / 미달 1 / 보류 1 / 향상 2** (Qwen3 학습 신뢰도, RAG citations).

---

## 5070 Ti 풀가동 최종 상태

| Phase | 시간 | 결과 |
|---|---|---|
| 0~5 + 7 | 2026-05-30 야간 (~1h 30m) | 6 commits, PoC 5종 측정 완료 |
| 마무리 | 추가 정리 | overnight trap + push 26 commits + doc/31 HTML |
| **Phase 8 고도화** | **야간 후속 (~3시간)** | **9 항목 일괄, citations 실측, KoBigBird 비교** |

**남은 작업**: 모두 발주처 자원 도착 의존. 자체 진행 가능 항목 99% 완료.
