# 3_평가셋 / B_v5_test

- 원본: `poc/datasets/labeled_p1_v5_clean/test.jsonl` (sha256 `36a5f3394ecde632…`)
- 건수: **256** · 정답 분포 TS 50 · S1 46 · S2 58 · S3 102
- 글자수 중앙값 1005 · 최대 8081 · 3,000자 초과 56건
- 본문에 등급 표기(특급·1급 비밀·대외비·TS/S1… 등)가 적힌 문서: **124건** (48.4%) — 검수자가 답을 보고 읽게 되는 문서다
- 사람 서명 정답: **0건**
- 원본에 doc_id 가 없는 행: **70건** (정답표의 doc_id 는 본문 지문 앞 16자로 만든 `nodoc-…` 대체값)

## 자격

v5_clean 을 근접중복 없이 나눈 내부 홀드아웃 256건 — 학습과 같은 분포라 낙관적이다. 정정 전 라벨 판이며 65행 정정본(s3fix)에서는 3행이 바뀐다.

## 폴더 구성

- `문서.jsonl` 원본 행(정답·라벨출처 포함)  ·  `본문/` 정답 없는 텍스트 (검수자에게 줄 것)  ·  `정답표.csv` (검수자에게 주지 말 것)

## 라벨 출처 (또는 원천) 분포

- synthetic_llm: 167
- rag_corpus_v2: 54
- bilingual_en: 16
- llm_judge_primary: 8
- koipa_case_based: 6
- nkt_designated: 3
- llm_judge_consensus: 1
- codex_review: 1
