# 재현 리허설 — 문서가 지목한 재현 스크립트가 ★ 수치를 못 낸다

작성 2026-08-13. 8/31 KL 성능 수치 검증 대비. `V8_MASTER_PLAN_2026-08-13.md` §1.3 의 실행 결과다.

---

## 1. 결론

★ 수치는 **재현된다.** 다만 **문서가 지목한 스크립트로는 안 된다.**

```
제출본 §2 ★        F1_macro 0.917 · S1 recall 0.857 · 고등급 미탐 0
gate_p1_candidate  f1 0.9166      · S1 recall 0.8571 · high→S3 0     [일치]
eval_serving_path  f1 0.9245      · S1 recall 0.929  · fnr_S1 0.0714 [불일치]
```

제출본 `분류_정확도_테스트방식.html` §1.4 는 "서빙 경로 채점 → `scripts/eval_serving_path.py`"
라고 적어놨다. KL 이 문서대로 실행하면 **0.9245 가 나오고 문서에는 0.917 이라 적혀 있다.**

수치 차이는 작고(+0.008) 방향도 유리하지만, 성능 수치 검증에서 "문서와 스크립트가 다른 값을
낸다"는 지적은 치명적이다. 감리 자리에서 "어느 쪽이 맞느냐"가 되고, 그 순간 나머지 수치의
신뢰도까지 같이 흔들린다.

---

## 2. 무엇이 다른가

### 2.1 원 출처 확인

승격 커밋 `f5316df`(2026-07-29) 의 메시지에 원 수치가 있다:

```
gate_p1_candidate.py 3축 게이트 v5(v-fe4b386b) vs v4(v-dd3abab9) 전축 PASS:
- axis1 hardened holdout: f1 0.69→0.92 · S1 recall 0.43→0.86 · high→S3 0=0
```

재실행 결과가 정확히 일치한다:

```
axis1 hardened holdout : f1 0.9166 vs 0.9166 | high→S3 0 vs 0 | S1 0.8571 vs 0.8571  [PASS]
axis3 adversarial      : high→S3 1 vs 1 | under-grade 39 vs 39                       [PASS]
OVERALL: PASS
```

### 2.2 `eval_serving_path.py` 는 τ 에 따라 값이 갈린다

같은 모델 · 같은 42건:

```
τ 미적용   F1 0.9776   accuracy 0.9762
τ 0.15     F1 0.8417   accuracy 0.8571
τ 0.25     F1 0.9245   accuracy 0.9286   ← 배포 설정
τ 0.30     F1 0.9484   accuracy 0.9524
τ 0.35     F1 0.9484   accuracy 0.9524
```

τ=0.25 값이 8/12 에 HTTP API 전 경로로 측정한 `serving_records_hardened42.records.jsonl`
에서 직접 계산한 F1 0.9245 · accuracy 0.9286 과 정확히 일치한다. 즉 **배포 설정은 τ=0.25 이고
그 값은 0.9245 다.** 어떤 τ 에서도 0.917 은 나오지 않는다.

### 2.3 드리프트가 아니다 — 확인함

의심 세 가지를 전부 배제했다.

```
모델      artifacts/classifier_p1_v5_clean/v-fe4b386b   .env 6곳 전부 동일 지정
평가셋    holdout_eval.hardened.jsonl 최종 변경 2026-07-05 < 승격 2026-07-29
서빙로직  b3dc8aaf 가 pipeline.py 에 넣은 것은 주석 20줄뿐 (요소 경계 게이트 철회 사유)
```

두 스크립트가 **애초에 다른 것을 재고 있다.** 코드가 변한 것이 아니다.

---

## 3. 조치

**문서 §1.4 의 재현 스크립트 표에 ★ 수치의 산출기를 명시한다.**

```
현행   서빙 경로 채점            scripts/eval_serving_path.py
수정   ★ 대표값(경화 42) 산출     scripts/gate_p1_candidate.py --candidate <model> --skip-fpr
       서빙 경로 τ 감도 분석      scripts/eval_serving_path.py --taus 0,0.25,0.30
```

코드 수정이 없다. 문서 표 한 줄이고, 배포본을 건드리지 않으므로 8월 동결선과 충돌하지 않는다.

덧붙여 §2 격자에 τ 를 명기하는 편이 낫다. 지금은 "서빙 경로"라고만 적혀 있어 τ 를 바꾼
값(0.8417 ~ 0.9776)이 전부 "서빙 경로 결과"로 보인다.

---

## 4. 부수 확인 — 합의 게이트는 배포에서 켜져 있다

미확정으로 남아 있던 항목이 해결됐다. `serving_records_hardened42.json` 의 검수 사유 집계:

```
review_reason_counts   agreement-gate 7 · fnr-safe override 2 · low-confidence 18
status_distribution    needs_review 25 · staging 17   (자동확정 40.5%)
```

`agreement-gate` 사유로 7건이 검수행했으므로 **배포 API 에서 활성**이다(`config.py:478` 의
기본값 `False` 는 라이브러리 기본이고, 배포 프로파일이 켠다).

따라서 9월의 S3 정책 역전은 **검수 큐 물량을 바꾼다.** 등급 수치(★)는 안 바뀌지만 검수 부담은
바뀌므로, KL 이 검수량도 보는 경우 설명이 필요하다. 사전등록 문서에 예상 변동폭을 적어둔다.

---

## 5. 남은 재현 항목

```
[ ] axis2 공개 판례 FPR 400건 — 과분류 4% 재현 (--skip-fpr 로 건너뛴 축)
[ ] 메타모픽 게이트 — forward 위반 Wilson 하한 0
[ ] KL 서버 223 배포본을 문서 버전과 일치시킴 (현재 구버전)
[ ] 전 API 서빙 경로 · 실패 요청 0건
```

⚠ 위 측정은 로컬 venv 에서 수행했다. 배포 이미지 안에서 한 번 더 확인해야 한다 — 로컬 venv 로
배포본을 진단했다가 오진한 전례가 있다(파서 진단 2026-08). 수치가 아니라 **환경 정합**을 보는
목적이다.
