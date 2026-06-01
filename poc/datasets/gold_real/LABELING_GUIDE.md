# Gold Real — 라벨링 가이드 및 레인 정의

## 목적

이 디렉토리는 합성 데이터와 무관하게 수집·라벨링된 평가셋을 담는다.
합성 gold(gold/)와 달리, 이곳 파일은 운영 성능 참고/근거에 사용 가능하다.

## 현재 파일 상태 (2026-06-01 기준)

| 파일 | 건수 | 용도 |
|------|------|------|
| `classification_gold.jsonl` | 610건 | 분류 평가셋 |
| `retrieval_gold.jsonl` | 미구축 | 검색 평가셋 목표: 30 query |
| `answer_gold.jsonl` | 미구축 | 답변 평가셋 목표: 20 query |
| `labeling_log.csv` | 이력 | 검수 기록 |
| `uncertain_cases.jsonl` | 참고 | 불일치 케이스 보관 |

---

## label_source 4-레인 정의

| label_source | 신뢰도 | 생성 방법 | 운영 근거 |
|-------------|--------|---------|---------|
| `human_review` | ★★★★★ | 사람이 직접 검수 | **가능** |
| `codex_review` | ★★★★ | AI 생성 + requires_human_signoff=True | 조건부 가능 |
| `llm_judge_consensus` | ★★★ | 룰 라벨러 + LLM 동의, 둘 다 고신뢰 | 참고용 |
| `llm_judge_primary` | ★★ | 룰 라벨러 무의견 + LLM 고신뢰 단독 | 참고용 |

**현재 분포 (610건)**:
- llm_judge_primary: 524건 (85.9%)
- llm_judge_consensus: 67건 (11.0%)
- codex_review: 19건 (3.1%)
- human_review: 0건 (0%) ← 아직 미구축

---

## 3층 평가 체계

```
make eval-synthetic     # ① F1=1.0   (합성, 운영 근거 불가)
make eval-llm-gold      # ② F1=0.37  (LLM pseudo-gold, 참고용)
make eval-human-gold    # ③ N/A      (human_review=0건, 미구축)
make eval-all-tiers     # 3가지 순차 실행 + 요약
```

human_review 데이터가 들어오면 ③이 N/A에서 실제 수치로 바뀜.

---

## 스키마 (classification_gold.jsonl)

```jsonc
{
  "doc_id": "hash-or-uuid",
  "text": "제목\n\n본문...",
  "label": "TS|S1|S2|S3",
  "label_source": "human_review|codex_review|llm_judge_consensus|llm_judge_primary",
  "source": "판례|금융보고서|synthetic_grounded|real_deidentified|public",
  "domain": "tech|business|finance|hr|legal|mixed|경영정보|화학_제약|...",
  "review_status": "accepted|needs_review|rejected",
  "reviewer_id": "r1|llm_judge_anthropic|llm_judge_local_openai",
  // LLM judge 추가 필드
  "rule_grade": "S3", "rule_confidence": 0.0,
  "llm_grade": "S1",  "llm_confidence": 0.85,
  "llm_rationale": "...",
  // human_review 시 권장 추가 필드
  "evidence_spans": [{"start": 0, "end": 80, "factor": "NON_PUBLICITY", "reason": "..."}],
  "notes": ""
}
```

---

## human_review import 절차 (미구축 상태)

1. `labeling_log.csv` 에 검수 이력 기록
2. 검수 완료 항목 → `label_source: human_review`, `review_status: accepted`
3. `make check-gold` → PASS 확인
4. `make eval-human-gold` → 실제 F1 측정

---

## 등급 판단 기준

| 등급 | 기준 | 예시 |
|------|------|------|
| **TS** | 유출 시 회사 존립 위협/국가 안보 영향 | 미공개 핵심 기술, 진행 중 M&A 가격, 루트 CA 키 |
| **S1** | 유출 시 경쟁우위 상실/영업비밀 침해 | 공정 노하우, 원가 구조, 고객 DB |
| **S2** | 외부 공개 시 협상력 약화/이미지 손상 | 미확정 사업계획, 내부 검토 초안 |
| **S3** | 공개 예정이거나 이미 공개된 정보 | 보도자료, 채용공고, 공시 |

모호하면 한 단계 위 등급으로 (FNR 회피 원칙).
