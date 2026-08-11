# 평가셋 역할 구분 — 어느 자로 잰 값인지 헷갈리지 않게

작성 2026-08-12.

## 정본 — `direct_authored_proxy_eval_split.v3/final_800.locked.jsonl`

모델 비교·정확도 판단에 쓰는 **유일한** 봉인셋.

| 지표 | 값 | 권고 상한 |
|---|---|---|
| 길이-only 1NN | 0.264 | 0.55 (무작위 0.25) |
| Theil's U | 0.012 | 0.25 |
| tell 커버 | 0.071 | 0.10 |
| v6 학습셋과 계보 독립 | True | — |

쓰기 전에 반드시:

```bash
python scripts/report_holdout_independence.py \
  --train <학습셋> --holdout datasets/proxy_eval/direct_authored_proxy_eval_split.v3/final_800.locked.jsonl \
  --strict
```

## 빠른 확인용 — `direct_authored_proxy_eval_split.v3/development_200.jsonl`

tell 커버 **0.185** 로 권고를 넘는다. 결함이 아니라 **표본 크기 효과**다 — 같은 코퍼스인데
1,000건 0.063 · final_800 0.071 · dev200 0.185 이고, tell 문턱이 "그 등급 문서의 5%"라
n 이 작으면 한두 건 차이로 넘는다.

**판단 근거로 인용하지 말 것.** 학습이 도는지 보는 용도로만 쓴다.

## ⛔ 폐기(정확도 근거로 사용 금지) — `direct_authored_proxy_eval_split.v2_2/`

`final_800.locked` · `development_200` 둘 다 해당.

| 지표 | final_800 | development_200 |
|---|---|---|
| 길이-only 1NN | **0.960** | **0.940** |
| Theil's U | **0.794** | **0.857** |
| tell 커버 | **1.000** | **1.000** |

**본문을 한 글자도 읽지 않고 글자 수만 세면 96% 가 맞는다.** 이 셋으로 잰 어떤 F1 도
분류 능력의 증거가 아니다. v4_3 이 여기서 0.99 를 받고 실문서에서 0.61 로 무너졌다.

더 나쁜 것은 요인 배정이 퇴화해 있다는 점이다 — 9개 요인 수준 중 6개가 등급과 1:1
(`management=1` ⟺ S2 250/250 · `management=2` ⟺ TS 200/200 · `secrecy=1` ⟺ S2 ·
`value=1` ⟺ S2). 현실에는 없는 관계다(관리가 잘 된 S2 는 얼마든지 있다). 즉 이 셋은
**틀린 지름길을 쓰는 모델에 만점을 준다** — v6 로 제대로 학습한 모델을 여기서 재면
틀린 방향으로 평가된다.

### 남은 쓸모: 회귀 스모크 테스트

지우지 않는다. 길이 지름길이 있어도 "0.96 → 0.30 으로 떨어졌다면 뭔가 크게 깨졌다"는
판정에는 여전히 쓸 수 있다. **그 용도로만** 쓰고, 그 수치를 정확도로 인용하지 않는다.

### 과거 수치는 어떻게 하나

이 셋으로 낸 비교는 **이미 무효였다.** v3 를 만든 것이 그 사실을 드러냈을 뿐이고,
과거 결론을 새로 무효화한 것이 아니다. 문서에 남은 수치는 "어느 자로 쟀는지"를 함께
적어 한정한다.

---

관련: `docs/V6_TRAINING_RUNBOOK.md` · `src/lloydk/holdout_independence.py` ·
`src/lloydk/dataset_leakage.py`
