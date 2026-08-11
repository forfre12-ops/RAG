# 프록시 코퍼스 길이 누출 — 등급이 글자 수로 결정된다 (2026-08-11)

> **인수인계 문서.** 프록시 골든/학습 파이프라인은 병행 세션이 작업 중이고 나는 그 코드를
> 설계하지 않았다. 이 문서는 **측정 결과와 그 함의만** 담는다. 생성기·분할기 수정은 하지 않았다.

## 한 줄

`direct_authored` 계열 코퍼스는 **등급별로 고정 길이 템플릿**을 쓰고 있어, 본문을 한 글자도
읽지 않고 글자 수만 세면 학습셋 100% · 봉인 평가셋 96% 가 맞는다. **이 셋으로 잰 모든 정확도
수치는 분류 능력의 증거가 아니다.**

## 실측 (2026-08-11)

재현:

```bash
python scripts/audit_dataset_leakage.py --lengths \
  datasets/proxy_gold/training_runs/direct-authored-catalog-training-v4_3-20260809/train_documents.jsonl \
  datasets/proxy_gold/training_runs/direct-authored-catalog-training-v4_3-20260809/validation_documents.jsonl \
  datasets/proxy_gold/training_runs/direct-authored-catalog-training-v4_3-20260809/calibration_documents.jsonl \
  datasets/proxy_eval/direct_authored_proxy_eval_split.v2_2/development_200.jsonl \
  datasets/proxy_eval/direct_authored_proxy_eval_split.v2_2/final_800.locked.jsonl
```

| 셋 | n | 길이-only 1NN | tell 종 | tell 커버리지 | 등급문자열 |
|---|---:|---:|---:|---:|---:|
| v4_3 학습문서 | 2,362 | **1.000** | 91 | 0.897 | 0 |
| v4_3 검증문서 | 316 | **1.000** | 91 | 0.908 | 0 |
| v4_3 보정문서 | 322 | **1.000** | 91 | 0.916 | 0 |
| dev200 (split v2_2) | 200 | **0.940** | 13 | **1.000** | 0 |
| **final800 봉인** (split v2_2) | 800 | **0.960** | 12 | **1.000** | 0 |

무작위 기대값 0.250 · 게이트 한계 길이-only 0.55 · tell 커버리지 0.10 → **5개 셋 전부 초과.**

### 길이 밴드가 등급별로 갈려 있다

```
v4_3 학습문서   S3 1201~3196 │ S2 3205~3269 │ TS 3273~3328 │ S1 3483~3579   ← 겹침 0
dev200          S3 2225~2241 │ S2 2242~2258 │ S1 2256~2274 │ TS 2269~2284   ← 밴드 폭 16자
final800 봉인   S3 2218~2243 │ S2 2236~2261 │ S1 2249~2275 │ TS 2262~2287
```

학습셋은 밴드가 **완전히 분리**돼 있다(S2 최대 3269 < TS 최소 3273, TS 최대 3328 < S1 최소 3483).
평가셋은 밴드 폭이 16자 남짓이고 경계에서만 몇 자 겹친다.

## 함의

1. **0.816 ↔ 0.990 논쟁은 잘못된 자리였다.** 두 수치 모두 길이만으로 94% 나오는 셋에서 쟀다.
   서빙 가드를 어떻게 처리하든 이 셋의 숫자는 분류 능력을 말해 주지 않는다.
2. **봉인 800 은 홀드아웃으로 쓸 수 없다.** `family_overlap 0` 으로 문서 계통은 분리했지만
   길이 규칙은 분할을 그대로 통과했다. 계통 분리는 길이 누출을 막지 못한다.
3. **학습 로그의 `eval_f1_macro = 1.0`(에폭 1부터, eval_loss 0.0008)** 도 같은 원인이다.
   `TRAINING_EXECUTION.json` 이 이 수치를 `diagnostic_epoch_metrics_only` 로 못박고
   `deployable=False` 를 유지한 것은 옳았다.
4. **기준의 일관성.** 2026-08-09 에 KL 검수 후보 777건을 길이-only **0.793** 을 이유로 반려하고
   120건(0.208)으로 재발행했다. 이 셋들은 **0.94~1.00** 이다. 같은 기준이면 전부 반려 대상이다.
5. **게이트는 있으나 여기에 안 걸려 있다.** `lloydk.dataset_leakage` 는 `build_kl_review_pool`
   (검수 후보 빌더)에만 배선돼 있다. 학습셋 materialize · 평가셋 split 경로에는 없다.
   그래서 통과했다.

## 남은 작업 (프록시 파이프라인 담당자)

1. **누출 게이트를 학습셋·평가셋 빌더에 배선.** 지금 상태로 재생성하면 또 통과한다.
   `check_or_raise(docs, label=...)` 를 materialize/split 산출 직전에 부른다.
2. **생성기에서 등급↔길이 상관 제거.** 등급별 길이 분포가 서로 겹치도록 만든다
   (같은 등급 안에서 길이를 넓게 흩고, 등급 간 중앙값 차이를 없앤다).
3. **tell 문장 제거** — 학습셋 91종 · 평가셋 12~13종. 한 등급에만 나오는 문장이다.
4. 그 다음에 재학습 → 재-finalize → dev200 측정. **순서를 바꾸면 측정이 무의미하다.**

## 곁가지 — 최종화 번들 재-finalize 필요

`5c2d2c66` 에서 M5 집계 계약을 v3 로 올렸다(선언되지 않은 post-model 규칙이 라벨을 바꾸던 것을
`excluded_post_model_serving_rules` 에 선언). v1·v2 시점 번들은 fail-closed 로 로드가 거부된다:

```
ValueError: finalized proxy operating point was calibrated
            for a different M5 aggregation contract
```

`artifacts_out/proxy_classifier_finalized/` 아래 12개 번들이 해당한다. 재-finalize 필요.

측정 중 확인된 사실 하나 — `v4_3` 와 `v4_3_guarded` 는 **선택 체크포인트(checkpoint-3740) ·
temperature(1.3865) · 보정셋 지표(f1_macro 0.8439, 등급별 F1 전부)가 완전히 동일**하다.
최종화기는 모델 성분만 재고 서빙 후처리를 타지 않기 때문이다. 즉 0.816↔0.990 차이가 모델이
아니라 서빙 규칙에서 나왔다는 것이 산출물로 확인된다.
