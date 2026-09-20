# labeled_p1_v5_clean_s3fix — 후보 학습셋 (미학습·미배포)

`labeled_p1_v5_clean`(배포본 v-fe4b386b 의 학습셋)에서 **65행의 라벨만 S3 로 정정**한 사본이다.
본문은 한 글자도 바꾸지 않았다(원본과 대조: 본문 다른 행 0). 원본은 배포본 재현을 위해 그대로 둔다.

## 무엇을 정정했나

`label_source=llm_judge_primary` ∧ `rule_grade=S3` ∧ `label≠S3` 인 행.

| 분할 | 행수 | 정정 | 원 라벨 |
|---|---:|---:|---|
| train | 2,042 | 56 | S2 44 · TS 8 · S1 4 |
| val | 256 | 6 | S2 5 · S1 1 |
| test | 256 | 3 | S2 2 · S1 1 |

train 라벨 분포: S1 365→361 · S2 463→419 · S3 813→869 · TS 401→393.

## 근거

65행 전부를 읽었다(2026-09-20, 본문 앞 700자 + 끝 150자, 전문 비공개 표지어 스캔 0건).
전부 증권사 시황·산업 가격 동향·경제지표·IR 후기·공개 조사 요약이다. 공개 출처의 내용은 민감도와 무관하게
비공지성 실패로 S3 라는 이 프로젝트 정책(`apply_public_ruling_rule` 등)과 같은 이유이고, holdout109 에서
정정한 22건(커밋 `5137f153`)과 같은 유형이다. 행마다 `label_before_correction_2026_09_20` 과
`label_correction_reason` 을 남겼다.

## 출처 표기 주의

정정한 65행의 `source` 는 전부 `금융보고서`인데, 이 출처는 실문서가 아니라 HuggingFace `nmixx-fin/synthetic_financial_report_korean`
합성 데이터다(9/19 확인, 커밋 `a6c3700b`). 행에 적힌 `document_origin=public_real` 은 그 커밋 이전의 분류 버그의 산물이라
근거로 쓰지 않는다. 내용이 "공개된 시황·논평 문체"라는 판단(→S3)에는 영향이 없다.

## ⚠ 한계

- **정정 주체는 사람이 아니라 AI 정독이다.** 사람 서명이 아니다 — `review_status` 는 원본 그대로다.
- 각 문서의 중간 부분(앞 700자와 끝 150자 사이)은 눈으로 읽지 않았고 키워드 스캔만 했다.
- **재학습 효과는 작다(2026-09-20 측정).** 원본·정정본을 시드 42·43·44 로 각각 학습해 짝 비교하니, 정정 후 holdout109 의
  금융보고서 S3 49건 중 과대분류가 평균 21.3→19.0건(짝 차이 −2·−5·0)이었다 — 시드 노이즈와 구분되지 않는다. 고등급 미탐이
  늘었다는 근거는 없다. 승격 후보가 아니다. `scripts/eval_s3fix_retrain_pairs.py`, 메모리
  `train-set-llm-overrode-rule-s3-56-candidates-2026-09-20.md`.
- 나머지 label_source(synthetic_llm 1,093 · rag_corpus_v2 604 등)에 같은 종류 오류가 있는지는 확인하지 않았다.

## 재생성

`python scripts/build_v5_clean_s3fix.py` (poc 에서). 정정 대상 수가 65행이 아니면 멈춘다.
원본·정정본의 sha256 은 `manifest.json`.
