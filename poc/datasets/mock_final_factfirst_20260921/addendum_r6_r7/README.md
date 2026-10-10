# 최종 모의문서 부속 패키지 — 6차(독립 작성 모델 시험) · 7차(학습 증량) · 8차(문체·양식 변형 시험)

- 총 584건 = 판정=명세로 검증된 6차 86건 + 7차 384건 + 8차 114건. 등급은 곱셈표(S×V×M) 계산값을 **다른 계열 판정자(opus·fable)** 가 재현한 잠정 라벨이다(전문가 검수 전).
- `train_r7` = 7차(작성 opus·sonnet, 경계 대조 가족 Q·희귀 조합 자유 가족 P): 학습 전용. `test_r6_independent` = 6차(작성 haiku·opus·fable): 학습에 쓰지 않는 독립 작성 모델 시험(haiku 문서는 판정을 통과한 소수뿐이라 해석 주의). `test_r8_style_shift` = 8차(작성 opus·fable, 새 업무 유형·비정형 문체): 학습에 쓰지 않는 문체 변형 시험.
- 이 파일들은 **전문가 검수용이 아니다.** 봉인 200건은 `../reviewer_documents_sealed.jsonl` 뿐이며 이 부속 패키지와 가족·문서가 겹치지 않는다.
- 생성: scripts/build_final_mock_addendum.py · 생성·검증 기록 reports/CLAUDE_DOCGEN_R6_20260921·R7_20260921 (PREREG_R6.md·PREREG_R7.md·PREREG_R8.md)
