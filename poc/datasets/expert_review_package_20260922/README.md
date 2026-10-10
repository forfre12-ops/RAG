# 전문가 검수 전체 패키지 (2026-09-22)

- 총 1584건. `documents_all.jsonl`(review_id+본문만, 등급·S/V/M 없음)이 검수자에게 줄 파일이다. 등급별 배분은 `SUMMARY.json` 참고.
- **`documents_by_batch/`** 는 같은 문서를 4묶음으로 나눈 사본이다(콘솔 적재 시 `review_batch` 태그로 그대로 쓸 수 있게):
  - `fs7_train_loss.jsonl` (963) — 모델이 학습에 직접 쓴 문서. 검수 결과는 **라벨 품질 검증용**이며 모델 재현율·미탐율 재계산에 쓰면 안 된다(train-on-test).
  - `fs7_val_holdout.jsonl` (67) — 체크포인트 선택에만 영향. 위와 동일하게 조심해서 다룬다.
  - `eval_dev_never_trained.jsonl` (354) — 모델이 전혀 안 본 우리 내부 시험 문서(개발·표현 154+6차 86+8차 114). 검수 결과를 그대로 **골든셋(평가 정답)**으로 승격할 수 있다.
  - `golden_sealed_untouched.jsonl` (200) — 우리도 아직 안 열어본 봉인 문서. 가장 깨끗한 골든셋 후보.
- `internal_manifest.jsonl` 에는 review_batch·라운드·등급·S/V/M 이 들어 있다 — **검수자에게 절대 주지 않는다.**
- 라벨은 전부 AI 가 곱셈표로 계산하고 AI 판정자가 재현한 잠정 라벨이다. 전문가 확정 전까지는 정답이 아니다.
- 다음 단계(미실행): 콘솔(`ProxyGoldCandidateService`)이 읽는 `{doc_id}.md`+`{doc_id}.metadata.json` 형식으로 변환해 적재 → 전문가 계정 발급 → 콘솔에서 결정+서명 → 서명분은 `golden_tiers.tier_of()` 가 자동으로 `locked_gold_eval` 로 계산해 `datasets/gold_real/locked_eval.jsonl` 에 쌓인다(등급당 5건 이상이면 `eval_readiness()` 통과).
