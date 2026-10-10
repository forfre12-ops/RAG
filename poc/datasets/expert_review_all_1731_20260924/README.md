# 전문가 검수 전달 패키지 — 사실 우선 모의문서 전량 1731건 (2026-09-24)

- `documents_all.jsonl` 이 검수자에게 줄 파일이다. 열 = `review_id`(MD-####) · `text` 뿐이다. 등급·S/V/M·라운드·학습 이력 어디에도 없다.
- 라벨은 전부 가이드 12쪽 곱셈표(S×V×M) 계산값을 AI 판정자가 재현한 **잠정 라벨**이다. 전문가가 확정하기 전 정답이 아니다.
- `internal_manifest.jsonl` 은 review_id → 라운드·가족·등급·S/V/M·작성 모델·`training_use` 를 담는다. **검수자에게 절대 주지 않는다.**
- **`training_use` 읽는 법**(결과를 나중에 어떻게 쓸 수 있는지가 여기 갈린다):
  - `fs_train_loss` — 모델이 학습 손실에 직접 쓴 문서. 검수 결과는 **라벨 품질 검증용**이며 모델 재현율·미탐율 재계산에 쓰면 train-on-test가 된다.
  - `fs_val_holdout` — 체크포인트(에폭) 선택에만 영향. 위와 같이 조심해서 다룬다.
  - `never_trained` / `never_trained_sealed` — 모델이 전혀 안 본 문서. 검수 결과를 그대로 **골든셋(평가 정답)**으로 승격할 수 있다. `sealed` 는 우리도 아직 안 열어본 문서다.
  - `unknown` — 최종 세트 매핑에서 못 찾은 경우(0건이어야 한다, SUMMARY.json 로 확인).
- 구성: {"R7": 384, "R4": 151, "R2": 228, "R5": 273, "R9": 147, "R8": 114, "R1": 233, "R6": 86, "R3": 115} · 등급 {"S1": 552, "S3": 373, "TS": 381, "S2": 425} · 학습 이력 {"fs_train_loss": 1110, "never_trained": 354, "fs_val_holdout": 67, "never_trained_sealed": 200}
- 생성: scripts/build_expert_review_all_1731.py · 각 라운드 생성·검증 기록은 reports/CLAUDE_DOCGEN_*/PREREG_*.md
