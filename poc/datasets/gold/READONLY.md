# Gold Evaluation Set — 읽기 전용

## 평가셋 등급 구분

보고서에서 F1을 표기할 때 아래 세 지표를 반드시 구분한다:

| 지표명 | 파일 | eval_type | 운영 F1 근거 |
|--------|------|-----------|-------------|
| `synthetic_masked_eval_f1` | `gold/classification_gold.jsonl` | `synthetic_masked` | **불가** |
| `llm_judge_gold_f1` | `gold_real/classification_gold.jsonl` | `human_review` | **가능** |
| `external_holdout_f1` | (미구축) | `external` | **가능** |

## classification_gold.jsonl — synthetic_masked (현재 파일)

**출처**: `datasets/test_set_v2/` (144개 JSON, 구 generator 기반 합성 문서)

**전처리**:
- 제목: 괄호형 등급 표기 제거 (`(1급 비밀)`, `[TS]` 등)
- 본문: 등급 분류 선언 구절 → `[등급분류]`, `[기밀자료]` 마스킹

**한계 (중요)**:
- 원본이 구 generator(등급 키워드 주입 방식)로 생성됨
- 마스킹 후에도 문서 내용 자체가 등급 시맨틱 반영 → 룰 라벨러 F1=1.0
- 이 수치는 "합성 파이프라인 내부 일관성" 확인용이며, **납품·운영 성능 근거 불가**

## 규칙 (위반 금지)

1. 학습(train/val) 데이터로 사용 금지
2. generator 프롬프트 seed로 사용 금지
3. 룰 라벨러 키워드 seed로 사용 금지
4. 파일 수정 시 반드시 `make_gold_set.py` 경유 또는 명시적 수작업 검수

## 진짜 gold set 구축

`datasets/gold_real/LABELING_GUIDE.md` 참조.
초기 목표: 분류 100건 / 검색 30 query / 답변 20 query.
