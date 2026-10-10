# 1_학습셋 / B_체크포인트선택용_val

- 원본: `poc/datasets/labeled_p1_v5_clean/val.jsonl` (sha256 `d5ce5a415db98e55…`)
- 건수: **256** · 정답 분포 TS 50 · S1 46 · S2 58 · S3 102
- 글자수 중앙값 1017 · 최대 8081 · 3,000자 초과 61건
- 본문에 등급 표기(특급·1급 비밀·대외비·TS/S1… 등)가 적힌 문서: **119건** (46.5%) — 검수자가 답을 보고 읽게 되는 문서다
- 사람 서명 정답: **0건**
- 원본에 doc_id 가 없는 행: **76건** (정답표의 doc_id 는 본문 지문 앞 16자로 만든 `nodoc-…` 대체값)

## 자격

가중치 갱신에는 안 쓰였지만 에폭별 성능을 보고 최종 체크포인트를 고르는 데 쓰였다(trainer.py `metric_for_best_model=early_stop_metric`, `eval_dataset=ds_val`). 그래서 최종 평가 정답으로 쓰지 않는다.

## 폴더 구성

- `문서.jsonl` 원본 행(정답·라벨출처 포함)  ·  `본문/` 정답 없는 텍스트 (검수자에게 줄 것)  ·  `정답표.csv` (검수자에게 주지 말 것)

## 라벨 출처 (또는 원천) 분포

- synthetic_llm: 157
- rag_corpus_v2: 62
- bilingual_en: 14
- llm_judge_primary: 10
- koipa_case_based: 5
- nkt_designated: 4
- llm_judge_consensus: 2
- codex_review: 2
