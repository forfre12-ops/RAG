# 1_학습셋 / A_배포본_가중치학습

- 원본: `poc/datasets/labeled_p1_v5_clean/train.jsonl` (sha256 `5b623472decfcc50…`)
- 건수: **2,042** · 정답 분포 TS 401 · S1 365 · S2 463 · S3 813
- 글자수 중앙값 431 · 최대 8062 · 3,000자 초과 182건
- 본문에 등급 표기(특급·1급 비밀·대외비·TS/S1… 등)가 적힌 문서: **1021건** (50.0%) — 검수자가 답을 보고 읽게 되는 문서다
- 사람 서명 정답: **0건**
- 원본에 doc_id 가 없는 행: **694건** (정답표의 doc_id 는 본문 지문 앞 16자로 만든 `nodoc-…` 대체값)

## 자격

배포본 v-fe4b386b 의 가중치를 직접 학습시킨 문서. 이 문서들로 다시 재면 암기 성능이 나온다(같은 문서 정확도 94.9% — _deployed_model_train_docs_2042/README.md, 2026-09-19).

## 폴더 구성

- `문서.jsonl` 원본 행(정답·라벨출처 포함)  ·  `본문/` 정답 없는 텍스트 (검수자에게 줄 것)  ·  `정답표.csv` (검수자에게 주지 말 것)

## 라벨 출처 (또는 원천) 분포

- synthetic_llm: 1,093
- rag_corpus_v2: 604
- llm_judge_primary: 160
- bilingual_en: 90
- koipa_case_based: 45
- llm_judge_consensus: 22
- nkt_designated: 18
- codex_review: 10
