# 3_평가셋 / A_holdout109_라벨정정후

- 원본: `poc/datasets/gold_real/holdout_eval.jsonl` (sha256 `ed1ae881de7e377d…`)
- 건수: **109** · 정답 분포 TS 17 · S1 15 · S2 12 · S3 65
- 글자수 중앙값 1119 · 최대 6000 · 3,000자 초과 13건
- 본문에 등급 표기(특급·1급 비밀·대외비·TS/S1… 등)가 적힌 문서: **2건** (1.8%) — 검수자가 답을 보고 읽게 되는 문서다
- 사람 서명 정답: **0건**
- 원본에 doc_id 가 없는 행: **0건** (정답표의 doc_id 는 본문 지문 앞 16자로 만든 `nodoc-…` 대체값)

## 자격

109건. 2026-09-20 에 규칙=S3 인데 LLM 판정이 덮어쓴 22건을 S3 로 정정한 판(label_before_correction_2026_09_20 필드에 원 라벨 보존). hardened42·clean42 는 이 안의 같은 42문서라 별도 세트가 아니다. 길이가 등급을 알려 줘 모델 간 비교 불가(Theil's U 0.372).

## 폴더 구성

- `문서.jsonl` 원본 행(정답·라벨출처 포함)  ·  `본문/` 정답 없는 텍스트 (검수자에게 줄 것)  ·  `정답표.csv` (검수자에게 주지 말 것)

## 라벨 출처 (또는 원천) 분포

- llm_judge_primary: 53
- koipa_case_based: 24
- nkt_designated: 11
- llm_judge_consensus: 8
- public_definitive: 7
- codex_review: 6
