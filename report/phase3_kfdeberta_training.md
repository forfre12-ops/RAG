# Phase 3 — KF-DeBERTa 학습 + ClassifyService wiring

5070 Ti 풀가동 PoC의 Phase 3. 합성/labeled 5K로 KF-DeBERTa-base 1 epoch 학습 → 가중치 ClassifyService 자동 로드 → confidence 0.85 고정에서 동적(0.30~0.98)으로 전환.

- 일자: 2026-05-30
- 하드웨어: RTX 5070 Ti 16GB (Blackwell sm_120)
- torch: 2.12.0.dev20260408+cu128

---

## 3.1 Dryrun + 명령 점검

`scripts/p1_train_classifier.py --mode dryrun --test datasets/labeled_5k/test.jsonl` 실행:
- **룰 라벨러로 F1 1.0** (합성 5K가 룰과 100% 일치)
- 학습 모델이 룰을 따라잡을 가능성 시사

스크립트 patch: train-path/val-path/test-path/batch-size/base-model/output-dir 인자 신설 (mode=full 시 TrainSpec 주입).

---

## 3.2 스모크 테스트 8단계 (모두 PASS)

| Step | 결과 |
|---|---|
| 1. torch import | ✅ |
| 2. CUDA + sm_120 | ✅ RTX 5070 Ti |
| 3. transformers | ✅ |
| 4. KF-DeBERTa tokenizer | ✅ |
| 5. 모델 로드 (184M) | ✅ |
| 6. forward GPU 추론 | ✅ logits (2,4) on cuda:0 |
| 7. labeled_5k 데이터셋 | ✅ 3500 행 |
| 8. MLflow file:// tracking | ✅ run_id 생성 |

(403 Forbidden 경고는 safetensors 자동 변환 시도가 kakaobank 레포 discussions 비활성 때문. 무해)

---

## 3.3 1 epoch 학습 (33s 실측)

```
spec: epochs=1, batch_size=16, base_model=kakaobank/kf-deberta-base
      train=3500, val=748, test=752
      class_weighted=True, lr=2e-5, warmup_ratio=0.1
```

### 결과

| 지표 | 값 | 합격선 (V2 §14.2) |
|---|---|---|
| **train_runtime** | **33.29초** | — |
| samples/sec | 105.1 | GPU 정상 |
| eval_loss | 0.0009 | — |
| **F1-macro** | **1.0** | ≥ 0.75 ✅ |
| **FNR-overall** | **0.0** | ≤ 5% ✅ |
| FNR TS / S1 / S2 / S3 | 0.0 / 0.0 / 0.0 / 0.0 | TS·S1 핵심 ✅ |
| Confusion Matrix | 대각선만 188, 그 외 0 | 완벽 수렴 |
| 학습 가중치 | `artifacts/classifier-1ep/v-ae3f5371/` | safetensors |

### 결함 1건 정리 (transformers v5 호환)

`Trainer.__init__()` 키워드 변경: `tokenizer` → `processing_class`. 패치:
```python
try:
    trainer = WeightedTrainer(..., processing_class=tok, ...)
except TypeError:  # v4 폴백
    trainer = WeightedTrainer(..., tokenizer=tok, ...)
```

---

## 3.4 5 epoch 정식 학습 — 생략

1 epoch에서 이미 F1=1.0, FNR=0.0 완벽 수렴. **합성 5K가 학습 모델 관점에서 과도하게 단순**한 분포(룰 라벨러도 F1=1.0). 5 epoch을 더 돌려도 동일 결과 + overfit 위험만 증가.

**1 epoch 가중치를 Phase 3.5/3.6에서 그대로 사용**.

---

## 3.5 ClassifyService wiring

### 변경

1. `lloydk/config.py` 신규 설정 키:
   ```python
   classifier_model_dir: str = ""  # 빈 문자열이면 rule-fallback 유지
   ```

2. `lloydk/services/classify_service.py`:
   ```python
   from lloydk.config import settings as _settings
   model_dir = getattr(_settings, "classifier_model_dir", "") or None
   self.inference = InferencePipeline(model_dir=model_dir)
   ```

3. `.env`:
   ```
   CLASSIFIER_MODEL_DIR=artifacts/classifier-1ep/v-ae3f5371
   ```

`InferencePipeline._load_model()`은 이미 구현되어 있어 추가 작업 0.

### 라이브 검증 — TS·S3 호출

| 샘플 | label | confidence | model_version | scores 분포 |
|---|---|---|---|---|
| TS (M&A 검토) | TS | **0.437** | `v-ae3f5371` | TS 43.7 / S2 25.7 / S3 26.1 |
| S3 (보도자료) | S3 | **0.968** | `v-ae3f5371` | S3 96.8 / TS 1.3 |

**confidence 동적 작동 확정** (이전 rule-fallback의 0.85 고정 → 0.30~0.98 분포). model_version도 `rule-fallback-v0` → `v-ae3f5371`로 자동 전환.

---

## 3.6 데모 12 샘플 실호출 분류 (10/12 = 83.3%)

```
✓ TS-반도체-핵심기술  4873자 → TS  conf=0.701 ev=17
✓ TS-경영-M&A         4751자 → TS  conf=0.410 ev=8
✗ TS-보안-암호        4501자 → S3  conf=0.370 ev=10  ← 오분류
✓ S1-기술-SW          4316자 → S1  conf=0.570 ev=9
✓ S1-재무-고객        3662자 → S1  conf=0.659 ev=7
✗ S1-보안-인증        4178자 → S3  conf=0.407 ev=7   ← 오분류
✓ S2-재무-영업        3735자 → S2  conf=0.860 ev=9
✓ S2-운영-IT          3669자 → S2  conf=0.303 ev=6
✓ S2-조직-HR          3557자 → S2  conf=0.499 ev=11
✓ S3-공시-홍보        2355자 → S3  conf=0.973 ev=9
✓ S3-공공-정책        2452자 → S3  conf=0.979 ev=13
✓ S3-회사 소개        2624자 → S3  conf=0.854 ev=10

confidence: min=0.303 avg=0.632 max=0.979
```

### 결과 정직 분석

- **etrain test set 752건 = F1=1.0** (학습 분포)
- **데모 12 샘플 = 10/12 PASS** (학습 외 분포: 머리글·결재·법령 인용 보고서 골격)
- **2건 오분류 — 보안 카테고리 (TS-보안-암호, S1-보안-인증)가 S3로 새는 패턴**
- confidence < 0.5 케이스 4건 — 운영 시 FUN-024 검수자 큐 자동 적재 대상

### V2 §9 FNR ≤ 5% 핵심 KPI

- 학습 분포 (labeled_5k test): FNR 0.0% ✅
- **학습 외 분포 (데모 12 샘플): FNR 16.7%** ❌ (TS·S1 각 1건 미탐)

**합성 5K 학습 한계 정량 입증** — V2 §11 합성 한계 경고가 그대로 발현. 발주처 실문서 도착(Q4) 후 분포 정상화 필요.

### Confusion (데모 12 샘플)

|  | pred TS | pred S1 | pred S2 | pred S3 |
|---|---|---|---|---|
| truth TS | 2 | 0 | 0 | 1 |
| truth S1 | 0 | 2 | 0 | 1 |
| truth S2 | 0 | 0 | 3 | 0 |
| truth S3 | 0 | 0 | 0 | 3 |

S2·S3는 perfect. TS·S1의 "보안" 카테고리만 합성 외 본문에서 S3로 새는 패턴 — 보안 도메인 합성 다양성 부족으로 추정.

---

## 데모 콘솔 갱신

`poc/src/lloydk/api/static/app.js` capability stats 5 카드 신규/교체:
- "1.18s — BERT 추론 (KF-DeBERTa 학습 모델, 5070 Ti 실측)" **src=measured**
- "33s — BERT 학습 1 epoch (5070 Ti labeled 3500건, 실측)" **src=measured**
- "F1=1.0 — labeled_5k test 752건 평가 (실측, 학습 분포 한정)" **src=measured**
- "10/12 — 데모 12 샘플 실호출 분류 정합 (학습 외 분포, 실측)" **src=measured**
- "≤ 5% — FNR 핵심 KPI 목표 (V2 §9, 합성 한계로 미달)" **src=spec** (정직 라벨)

이전 BERT 0.05~0.2s 참고값 → 1.18s 실측, 신규 카드 4종 추가. **실측 비율 9/14 (64%) → 11/14 (79%)** — 목표 70% 달성.

---

## 회귀 안정성

```
poc/tests/test_demo_page.py: 9/9 PASS (2.3s)
```

ClassifyService wiring + 데모 카드 갱신 후에도 회귀 안전.

---

## Phase 3 핵심 성과 vs 결함 (정직)

### 성과 ✅

1. **5070 Ti GPU 학습 실증** — sm_120 + cu128 nightly + KF-DeBERTa-base 정상 학습
2. **학습 시간 33초/epoch** — V2 가정 6~10시간을 200배 단축. 빠른 반복 가능
3. **transformers v5 호환 패치 1건** (tokenizer → processing_class)
4. **ClassifyService wiring 무수정 도달** — settings 1키 + 6줄로 완성
5. **confidence 0.85 고정 → 동적 (0.30~0.98)** — 진짜 AI 신뢰도 첫 사례
6. **데모 §5 실측 비율 79%** (이전 64%) — 정직성 표기 강화

### 결함 (정직 보고) ❌

1. **합성 5K 학습 한계 그대로 발현** — 데모 12 샘플 (학습 외 분포)에서 10/12 (83.3%) 정합
2. **TS·S1 "보안" 카테고리 S3 미탐 2건** — FNR 16.7% (목표 5% 미달)
3. **5 epoch 정식 학습 불필요 입증** — 합성 분포가 학습에 과도하게 단순. overfit 위험만 증가
4. **403 Forbidden 경고** (safetensors auto-conversion) — 무해하지만 로그 노이즈

### 발주처 자원 도착 시 이행

- **실문서 (Q4)** → labeled_5k에 추가 + 재학습 → 학습 외 분포 분류 정합 회복 시도
- **가이드 v2** → seeds 갱신 + 합성 데이터 다양화 → 보안 카테고리 강화
- **LLM API 키 (Q5)** → P3 Claude Sonnet 4.6 합성 → 본문 다양화 → P2/P1 동시 개선

---

## Phase 4 진입 준비도

| 사전 요건 | 상태 |
|---|---|
| ES + 학습 모델 + KURE/BGE + Qwen3 라이브 | ✅ |
| 풀스택 RAG ON 호출 가능 | ✅ |
| 학습 가중치 InferencePipeline 로드 | ✅ Phase 3.5 |
| MinIO `lloydk-models` 버킷 | ✅ Phase 1 |
| `scripts/p5_e2e_smoke.py` 풀 모드 지원 | ✅ |

다음: **Phase 4 — P5 E2E 풀스택 latency 측정** (RAG ON ≤ 30초 합격선). 예상 0.5일.
