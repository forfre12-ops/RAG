# 37. 운영점 실측 — B1·C-esc·C-eval (2026-06-19)

doc/36 본개발 잔여 "운영점 값 확정"을 **실 모델 + 실 홀드아웃**으로 측정한 결과.
- 모델: `artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9` (KF-DeBERTa, RTX 5070 Ti)
- 홀드아웃: `datasets/gold_real/holdout_eval.jsonl` (실문서 109건 · TS17/S1 19/S2 30/S3 43)
- 도구: `scripts/eval_fnr_threshold_sweep.py`(원시 argmax) · `scripts/eval_serving_path.py`(서빙경로)

## 핵심 발견 — eval↔serving 갭이 ~9배 (C-eval 입증)

같은 모델·같은 홀드아웃을 **두 경로**로 측정:

| 경로 | 방향성 FNR | FNR_TS | FNR_S1 | F1 | Acc |
|---|---:|---:|---:|---:|---:|
| **원시 argmax** (기존 평가·스윕) | 0.167 | ~0.06 | ~0.68 | 0.632 | 0.679 |
| **서빙경로 argmax** (실배포 = `InferencePipeline.run`) | **0.018** | **0.000** | 0.053 | 0.686 | 0.706 |

서빙경로의 방향성 미탐이 원시 평가보다 **약 9배 낮다**(0.167→0.018). 차이는 서빙에만 있는
**룰 기반 FNR-safe override + most-severe 청크 집계**가 모델 argmax가 놓친 고등급 문서를
끌어올리기 때문 — 설계대로 **안전 방향**. 이 override 효과는 룰 기반(학습 무관)이라 누출
우려가 없어 갭 자체는 견고하다.

**함의**: FNR을 모델 argmax(또는 DB 저장 예측)로 측정하면 **배포 실제 안전성을 크게 과소평가**한다.
모든 FNR/게이트 판정은 `evaluate_via_serving`(C-eval)로 서빙경로에서 측정해야 한다.

## C-esc escalation τ 운영점 (서빙경로 기준)

서빙경로에서 τ를 낮춰가며(고등급 적극 승격):

| τ | 방향성 FNR | FNR고등급avg | F1 | Acc | 해석 |
|---|---:|---:|---:|---:|---|
| **argmax (기본)** | 0.018 | 0.026 | 0.686 | 0.706 | override 덕에 이미 FNR_TS=0, 전체 FNR≤0.05 충족 |
| **0.35 (권장 opt-in)** | 0.009 | **0.000** | 0.658 | 0.679 | 고등급 미탐 **완전 0**(TS·S1 모두) · F1 −2.8%p |
| 0.25 | 0.000 | 0.000 | 0.604 | 0.624 | 전 등급 미탐 0이나 F1 −8%p |
| 0.15 | 0.000 | 0.000 | 0.562 | 0.578 | 과분류 과다 |

**원시 스윕의 추천 τ=0.10은 배포엔 틀린 값이다.** 그 추천은 override를 안 거친 원시
argmax(FNR 0.167)를 FNR≤0.05로 낮추려다 나온 것이라, override가 이미 처리하는 미탐을
중복으로 잡으려 τ를 과하게 낮춰 **과분류(검수부하 0.53)** 를 유발한다. 서빙경로에서 보면
override만으로 이미 FNR_TS=0이므로 τ는 그렇게 낮출 필요가 없다.

## 권장 운영점 (확정)

- **기본: `classifier_escalation_tau=None`(argmax) 유지** — 서빙경로 FNR=0.018, **FNR_TS=0.0**,
  전체 FNR이 목표(≤0.05) 충족. 추가 조정 불필요가 기본선.
- **고등급 미탐 0 강제(보수적 운영): `CLASSIFIER_ESCALATION_TAU=0.35`** — TS·S1 미탐 모두 0,
  비용은 F1 −2.8%p(검수부하 소폭↑). 발주처가 "고등급은 단 한 건도 못 놓침"을 요구하면 이 값.
- **τ ≤ 0.25 비권장** — 미탐 한계효용은 0이고 F1·정밀도만 급락.

## B1 실측 — 범용약어 weight 배수 (서빙경로, τ=argmax)

`rule_high_risk_weight_multiplier`를 1.0→0.0으로 스윕(배수마다 파이프라인 재생성):

| 배수 | 방향성 FNR | FNR_TS | FNR_S1 | F1 | Acc |
|---:|---:|---:|---:|---:|---:|
| **1.00 (현재 기본)** | 0.018 | 0.000 | 0.053 | **0.686** | 0.706 |
| 0.80 | 0.037 | 0.000 | 0.053 | 0.685 | 0.706 |
| 0.60 | 0.055 | 0.000 | 0.158 | 0.657 | 0.688 |
| 0.40 | 0.083 | 0.000 | 0.210 | 0.640 | 0.679 |
| 0.00 (부스트 off) | 0.092 | 0.059 | 0.210 | 0.631 | 0.670 |

**발견**:
- **FNR_TS는 배수 0.4까지 0 유지** — TS recall은 범용약어 부스트에 비의존(모델+타 룰이 잡음).
  배수 0(완전 비활성)에서만 FNR_TS=0.059로 상승.
- **FNR_S1은 민감** — 배수↓ 따라 0.053→0.21로 악화. 즉 범용약어는 **S1 recall에 load-bearing**.
- F1도 배수 1.0에서 최고(0.686), 낮출수록 단조 하락.

**결론 (정정)**: 이 실 홀드아웃에서 범용약어 down-weight는 **FNR↑·F1↓만 유발 — 순손해**.
"범용약어 단독 과분류"가 net 결함으로 나타나지 않는다(백로그의 "과분류는 FNR-safe 방향이라
안전 결함 아님" 자기진단과 일치). **B1 권장: `rule_high_risk_weight_multiplier=1.0` 유지.**
레버는 갖추되, 현 증거로는 내리지 않는다. 향후 *공개 S3 문서에 범용약어가 다수 섞여 과분류가
실측되는 홀드아웃*이 확보되면 그때 이 스윕으로 재판단(메커니즘은 준비됨).

## 재현

```bash
# 원시 argmax 스윕
python scripts/eval_fnr_threshold_sweep.py \
  --model-dir artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9 \
  --gold datasets/gold_real/holdout_eval.jsonl --report reports/fnr_threshold_sweep_clean.md
# 서빙경로 실측 (τ 비교)
python scripts/eval_serving_path.py \
  --model-dir artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9 \
  --gold datasets/gold_real/holdout_eval.jsonl --taus 0,0.35,0.25,0.15 \
  --report reports/serving_path_eval_clean.md
# B1 범용약어 배수 스윕 (서빙경로)
python scripts/eval_serving_path.py \
  --model-dir artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9 \
  --gold datasets/gold_real/holdout_eval.jsonl --multipliers 1.0,0.8,0.6,0.4,0.0 \
  --report reports/b1_multiplier_sweep_clean.md
```

근거 산출물(`poc/reports/`, gitignore): `fnr_threshold_sweep_clean.{md,json}` ·
`serving_path_eval_clean.{md,json}` · `b1_multiplier_sweep_clean.{md,json}`

## 요약 — 운영점 확정

| 항목 | 확정값 | 근거 |
|---|---|---|
| escalation τ | **None(argmax) 기본**, 고등급 미탐0 강제 시 **0.35** | 서빙 FNR_TS=0 이미 충족; τ=0.35는 S1까지 0(F1 −2.8%p) |
| 범용약어 배수 | **1.0 유지** | 낮추면 FNR↑·F1↓만 발생(순손해) |
| FNR 측정 경로 | **서빙경로(`evaluate_via_serving`) 필수** | 원시 argmax는 배포 FNR을 9배 과대평가 |
