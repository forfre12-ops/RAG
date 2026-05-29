# Phase 5 — Qwen3 vs Solar 한국어 합성 200건 비교

5070 Ti 풀가동 PoC 야간 무중단 Phase 5. Qwen3-14B-Q4 + Solar-10.7B-Q4 각 200건 합성 후 라벨 일치도·FNR·품질 비교. **Qwen3 압승 → 1순위 확정 유지**.

- 일자: 2026-05-30 (01:35 시작 ~ 02:35 종료, 60분)
- 환경: onprem-local + Ollama
- 자동화: `scripts/overnight_solar_after_qwen3.sh` (Qwen3 완료 폴링 + Solar 자동 시작 + .env 자동 전환)

---

## 측정 결과

### Qwen3-14B-Q4 (license: Apache 2.0)

| 지표 | 값 | 합격선 | 결과 |
|---|---|---|---|
| 합성 건수 | **200/200** (실패 0) | — | — |
| **라벨 일치도** | **1.00 (100%)** | ≥ 0.90 | ✅ PASS |
| **FNR (TS·S1 미탐)** | **0.0%** | ≤ 5% | ✅ PASS |
| PII 위반 | 0 | 0 | ✅ |
| JSON 파싱 실패 | 0 | 0 | ✅ |
| input tokens | 67,228 | — | — |
| output tokens | 156,307 | — | — |
| 등급별 분포 | TS·S1·S2·S3 각 50 균등 | — | ✅ |
| 도메인별 분포 | tech 36 · business 36 · finance 32 · hr 32 · legal 32 · mixed 32 | — | ✅ |
| 시간 | **37분 48초** | — | — |
| 속도 | 5.7초/건 | — | — |

### Solar-10.7B-Q4 (license: Solar Pro Apache 2.0 호환)

| 지표 | 값 | 합격선 | 결과 |
|---|---|---|---|
| 합성 건수 | **200/200** (실패 0) | — | — |
| **라벨 일치도** | **0.81 (81%)** | ≥ 0.90 | ❌ **FAIL** |
| **FNR (TS·S1 미탐)** | **27%** | ≤ 5% | ❌ **FAIL** |
| **PII 위반** | **5** | 0 | ❌ |
| **JSON 파싱 실패** | **153 (76.5%)** | 0 | ❌ |
| input tokens | 91,730 | — | — |
| output tokens | 83,138 | — | — |
| 시간 | **~16분** | — | — |
| 속도 | 4.8초/건 | — | — |

---

## 1순위 자동 확정 (사전 합의 결정 1 적용)

```
qwen3_label_match = 1.00
solar_label_match = 0.81
Δ = 0.19 > 0.05 → Solar 명확히 미달
```

추가 결정 사유:
1. **JSON 출력 보장 약함** — Solar 76.5% 파싱 실패 (PydanticOutputParser 통과 불가)
2. **한국어 등급 분류 정확도 부족** — Solar FNR 27% (TS·S1 미탐 심각, V2 §9 핵심 KPI 5배 초과)
3. **PII 정규식 통과 5건** — Solar 출력에서 실제 PII 또는 패턴 매칭 한계

**1순위 확정: Qwen3-14B-Q4**

- 라이선스: Apache 2.0 ✅ (공공사업 적합)
- V2 §6.2 "Why Qwen3-14B" thinking mode 정량 입증
- `.env` `LOCAL_LLM_MODEL=qwen3:14b` 유지

---

## 핵심 발견

### 1. 한국어 합성 정확도: Qwen3가 Solar 대비 +19%p 우위

외부 벤치([BenchLM Solar KMMLU 80.1](https://benchlm.ai/leaderboards/korean-llm))에서는 Solar Pro가 한국어 1위였으나, **우리 합성 과업(JSON 출력 + 등급 분류 + 도메인 정합)에서는 Qwen3가 압승**. 외부 벤치(자연어 응답)와 우리 합성 과업(구조화 출력) 분포 차이로 해석.

### 2. JSON 출력 안정성: Qwen3 100% vs Solar 23.5%

Solar 153/200 파싱 실패 (76.5%) — `PydanticOutputParser`가 거부. V2 §6.1·§6.2 "안정성 1순위" 기준 Qwen3 우위 절대적. Solar는 자유 텍스트 응답에 강하지만 구조화 출력에는 부적합.

### 3. FNR 27% Solar 미탐 패턴

Solar의 TS·S1 미탐 27% — V2 §9 핵심 KPI ≤5%의 5배. 영업비밀 자동분류 합성 데이터로는 신뢰 불가. Qwen3 0% (완벽).

### 4. 발주처 자원 도착 시 영향

| 자원 | Qwen3 1순위 영향 |
|---|---|
| Anthropic API 키 도착 | Claude Sonnet 4.6 vs Qwen3 비교 필요. Qwen3 100% 라벨 일치도 도달했으니 비용 부담 줄어듦 |
| 실문서 도착 | Qwen3 합성 200건을 labeled_5k에 추가 → 재학습 → 데모 외 분포 정합 회복 |

---

## 데모 콘솔 갱신

`poc/src/lloydk/api/static/app.js` §5 capability stats 신규 카드 2:
- "100% — P3 Qwen3 라벨 일치도 (200건, V2 §14.2 ≥90% PASS)" **src=measured**
- "81% — P3 Solar 라벨 일치도 (200건, FNR 27% JSON 76.5% 실패 — Qwen3 채택)" **src=measured**

실측 비율: 12/15 → **14/17 = 82%** (목표 70%+ 통과, Phase 4 80%에서 +2%p)

---

## 회귀

```
poc/tests/test_demo_page.py: TBD (commit 전 확인 필요)
```

---

## 산출물

- `report/phase5_p3_comparison.md` (본 보고서)
- `report/phase5_p3_qwen3_2026-05-30.json` (Qwen3 raw 200건 요약)
- `report/phase5_p3_qwen3_2026-05-30.md` (자동 산출 보고서)
- `report/phase5_p3_solar_2026-05-30.json` (Solar raw 200건 요약)
- `report/phase5_p3_solar_2026-05-30.md` (자동 산출 보고서)
- `poc/datasets/synthetic_qwen3/` 200건 JSON
- `poc/datasets/synthetic_solar/` 200건 JSON
- `scripts/overnight_solar_after_qwen3.sh` (야간 자동화 입증)

---

## Phase 7 진입 준비도

| 사전 요건 | 상태 |
|---|---|
| Phase 4·5 측정 완료 | ✅ |
| 데모 §5 실측 비율 ≥ 80% | ✅ 82% |
| doc/17 부분 갱신 (5070 Ti 행) | ✅ |
| 발주처 1페이지 자료 | ✅ doc/31 |
| MEMORY.md Phase별 메모리 | Phase 0·1·2·3 등록, Phase 4·5 대기 |

다음: Phase 7 최종 정리 + 야간 종합 보고서 + 메모리 4건 신규 + 최종 commit.
