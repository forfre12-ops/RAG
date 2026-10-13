# v6 학습 실행 지침 — 지름길 없이 잰 첫 수치를 만든다

> 작성 2026-08-12. 이 문서는 **학습셋과 평가셋을 둘 다 고친 뒤** 처음 도는 run 을 위한 것이다.
> 여기서 나오는 숫자는 "v5 대비 개선"이 아니라 **"지름길 없이 잰 첫 기준선"** 이다.
> 그 구분을 놓치면 이 작업 전체가 회귀로 오독된다.

---

## 0. 먼저 알아야 할 것 — 숫자는 떨어질 것이다

v6/v3 는 모델을 좋게 만들지 않았다. **측정을 정직하게 만들었다.**

| | 이전(v3_9 학습 / v2_2 평가) | 현행(v6 학습 / v3 평가) |
|---|---|---|
| 학습셋 길이-only 1NN | 0.979 | **0.392** |
| 학습셋 Theil's U | 0.695 | **0.018** |
| 학습셋 tell 커버 | 1.000 | **0.090** |
| 학습셋 인용의 tell 포함 | 88.3% | **2.6%** |
| 평가셋 길이-only 1NN | 0.968 | **0.300** |
| 평가셋 Theil's U | 0.800 | **0.008** |
| 평가셋 tell 커버 | 1.000 | **0.063** |

즉 종전 모델이 쓰던 지름길(문서 길이·정답을 적어 둔 문장)이 양쪽에서 사라졌다.
**F1 이 떨어지는 것이 정상이고, 그 하락이 곧 이전 수치가 부풀려져 있었다는 증거다.**

⛔ 이 run 의 결과를 v5 계보 수치와 나란히 놓고 "회귀"라고 부르지 말 것.
서로 다른 자로 잰 값이라 비교 자체가 성립하지 않는다.

---

## 1. 사전 확인 (GPU 장비에서)

```bash
# 소스 계보 — 미커밋/미추적이 있으면 finalize 가 막는다(그게 맞다)
git status --porcelain -- poc/src poc/scripts        # 비어 있어야 한다
git rev-parse HEAD                                    # 매니페스트에 이 값이 박힌다

# 데이터 무결성 — 매니페스트의 records_sha256 과 대조
cd poc
python - <<'PY'
import hashlib, json, pathlib
for name in ("proxy_gold/direct_authored_catalog_training.v6",
             "proxy_eval/direct_authored_proxy_eval.v3"):
    body = pathlib.Path(f"datasets/{name}.jsonl").read_bytes()
    manifest = json.loads(pathlib.Path(f"datasets/{name}.manifest.json").read_text("utf-8"))
    got = hashlib.sha256(body).hexdigest()
    print(name, "OK" if got == manifest["records_sha256"] else f"MISMATCH {got}")
PY
```

## 2. 비교 전 게이트 — 자가 쓸 만한지 먼저 센다

```bash
python scripts/report_holdout_independence.py \
  --train datasets/proxy_gold/direct_authored_catalog_training.v6.jsonl \
  --holdout datasets/proxy_eval/direct_authored_proxy_eval_split.v3/final_800.locked.jsonl \
  --out reports/holdout_independence_v6_v3.json --strict
```

2026-08-12 실측: `계보 독립 True · 길이-only 0.264 · Theil's U 0.012 · tell 커버 0.071`
→ **usable_for_comparison = True**

⚠ `development_200` 은 tell 커버 0.185 로 권고(0.10)를 넘는다. 같은 코퍼스인데
1,000건 0.063 · final_800 0.071 · dev200 0.185 인 것은 **표본이 작아 지표가 흔들리기**
때문이다(문턱이 "그 등급 문서의 5%"라 n 이 작으면 한두 건 차이로 넘는다).
**판단은 final_800 으로 하고 dev200 은 빠른 확인용으로만 쓴다.**

## 3. 학습 → finalize

```bash
python scripts/p1_train_classifier.py  ...   # 기존 절차. 학습셋 경로만 v6 로.

python scripts/finalize_proxy_classifier.py \
  --training-run-dir <run> --checkpoint-root <ckpt> \
  --no-baseline
```

### `--no-baseline` 을 쓰는 이유

`--baseline-f1-macro` 는 이제 필수다(빠뜨리면 무회귀 검사가 통째로 안 돌고 COMPLETE 가
나던 결함을 막았다). 그런데 **이 run 에는 넣을 baseline 이 없다.**
v5 계보 수치는 지름길이 있는 자로 잰 값이라 여기 넣으면 시작부터 막히고, 막히는 이유가
품질이 아니라 **자가 바뀐 것**이다.

`--no-baseline` 은 "기준이 없다"를 **선언**하는 것이고 매니페스트에 남는다 — 인자를
빠뜨린 것과 구분된다. 이 run 의 결과가 **다음 run 부터의 baseline** 이 된다.

### 게이트 순서를 기억할 것

① 고등급 FNR 상한 → ② 퇴행성 0 → ③ 검수부담 상한 → ④ F1 무회귀 **(마지막)**

RFP 에 정확도 수치목표가 없고 핵심목표는 미탐 최소화다. ④를 앞으로 당기면
**미탐을 줄이면서 F1 이 소폭 떨어지는 개선** — 이 사업이 정확히 원하는 개선 — 이
자동 차단된다. (`proxy_training_finalization.fit_escalation_operating_point` 주석)

## 4. 결과 기록

매니페스트에서 반드시 함께 인용할 것:

- `source.git.commit` / `source.git.dirty` / `source.git.bypassed` — 어느 소스가 만들었나
- `selection.*` — 어느 체크포인트를 왜 골랐나
- 학습셋·평가셋 매니페스트의 `leakage` 블록 — **어떤 자로 잰 값인가**
- `claim_ceiling` — 합성 내부 일관성 + 안전 무회귀까지

---

## 5. 배포본 교체는 이 run 의 결론이 아니다

현행 배포본은 `artifacts/classifier_p1_v5_clean/v-fe4b386b`(v5 계보)이다.
이 run 은 **기준선 수립**이 목적이고, 교체 판단은 별도다. 최소한 다음이 있어야 한다:

1. 같은 자(v3 final_800)로 잰 v5 계보 모델의 수치 — 없으면 비교 대상이 없다
2. 고등급 FNR 비교 (F1 이 아니라 **여기가** 핵심 목표다)
3. 검수부담 변화 — 사람이 감당 못 할 운영점은 배포해도 실제로 안 쓰인다

---

## 6. 그래도 남는 천장

이 전부를 통과해도 나올 수 있는 주장은 **"합성 내부 일관성 + 안전 무회귀"까지**다.
실 일반화 근거가 아니다 — 자체 실측으로 교차silver→gold_real F1 0.26 이 그 천장을
보여줬고, 그건 이번 검증이 부족해서가 아니라 **데이터 정책(실데이터·검수·반출 0)이 만든
구조적 한계**다. 실 일반화 증거는 고객사 현장 운영학습에서만 나온다.

감리에는 "미달"이 아니라 **"구조적 한계 + 운영학습이 유일한 레버"** 로 서술한다.
