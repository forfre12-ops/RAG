# v5_clean 재학습 게이트 결과 (2026-07-26)

- **후보 모델**: `artifacts/classifier_p1_v5_clean/v-fe4b386b` (KF-DeBERTa-base, 5ep, train_runtime 315s)
- **배포본 baseline**: `artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9`
- 재현: `python scripts/build_p1_v5_clean.py` → manifest.json 의 `train_command` → 아래 eval 스크립트
- 모델 가중치는 gitignore(artifacts/). 게이트는 재실행 가능(eval_p1_holdout.py / eval_real_public_fpr.py).

## Axis 1 — hardened holdout (42건, v5 train과 exact-text 누출 0 검증됨)
| 지표 | 배포본 v4 | v5_clean |
|---|---|---|
| f1_macro | 0.688 | **0.917** |
| S1 recall | 0.429 | **0.857** |
| S3 recall | 0.571 | **1.00** |
| high_risk→S3 (위험 미탐) | 0 | **0** |
| TS recall | 0.917 | 0.833 |

verdict: **PASS** (f1 +0.23, S1 +0.43, 안전 high_risk_to_s3=0 유지. TS 1건 하락은 S3 아닌 S1/S2 = 안전 문제 아님)

## Axis 2 — 공개문서 FPR (실제 공개문서 400건, 학습오염 1,196건 제외) ← 결정적
| 지표 | 배포본 v4 | v5_clean |
|---|---|---|
| 과분류율(공개→고등급) | 79% | **4%** (~20배↓) |
| 고등급 오탐(TS/S1) | 54% | **2.2%** (~24배↓) |
| S3 정답 비율 | 84/400 (21%) | **384/400 (96%)** |

verdict: **PASS**

## Axis 3 — 적대 공격셋 golden_100 (동음이의·약어치환·문맥분산 위장)
| 지표 | 배포본 v4 | v5_clean |
|---|---|---|
| high→S3 (진짜비밀을 공개S3로 유출 = 최악 위험미탐) | 1 | **1** (무회귀) |
| under-graded (고→하 어떤 하향이든) | 25 | 39 |
| accuracy | 0.31 | 0.31 |

verdict: **최악 안전지표 무회귀(1=1)**, 단 ⚠️ **적대 중간등급 위장에 v5가 더 잘 속음**(39>25).
과분류를 없앤 대가(공격적 상향으로 무마하던 v4 대비). 단 유출은 대부분 고→중이지 고→공개(S3)가 아니며,
운영선 룰엔진 source-prior·약어 검수라우팅·escalation veto(demo FIX-D/E)가 이 갭을 커버(방어심층).

## 종합
axis1·2 통과 + axis3 최악 안전 무회귀. 안전(high→S3 무증가) 유지하며 과분류 격감·S1 recall 2배.
⚠️ 트레이드오프: 적대 중간등급 위장에 v5가 더 취약 → **분류기 단독 아닌 룰+veto 방어심층 전제로 배포 권고**.
정직 캐비엇: axis1은 n=42 machine-silver(시나리오 템플릿 일부 공유 가능) → 실문서 기반 axis2가 결정적.
운영 관점: 배포본의 79% raw 과분류는 source-prior 게이트가 운영선 ~5%로 상쇄하나, v5는 **게이트 없이 raw 4%**
달성(게이트 의존↓) + **S1 recall 0.43→0.86(게이트가 못 고치는 축)** = 진짜 운영 이득.

## 배포
**자동 배포 없음.** 스왑은 사람이 `CLASSIFIER_MODEL_DIR=artifacts/classifier_p1_v5_clean/v-fe4b386b` 로 바꾸고
`scripts/build_offline_bundle.py`(해시핀·parity 게이트)로 재빌드해 결정. v5_clean은 강력한 배포 후보.
