# No-Human Proxy Gold Report

## Verdict

- locked_gold_eval: 0 (사람서명 없음 — 이건 true gold 아님)
- proxy_eval (leakage-free): 166 / 777
  - external_authority (nkt+public, 실측 인용 가능): 85
  - synthetic_proxy (koipa/rule_llm/codex, 스모크 전용): 81
- train_leak (학습중복 → eval서 배제): 48
- silver_train_only: 356
- quarantine: 207
- Use proxy_eval for smoke/regression only. Do NOT call it true gold.

## ⚠️ 단일 F1 인용 금지 (중요)

- proxy_eval 의 62%가 합성/LLM 프록시(synthetic_proxy)다. 이 라벨은 분류기와 편향을
  공유할 수 있어(correlated error) '통과'가 실문서 정확도를 **보증하지 않는다**.
- 실측 인용이 필요하면 `authority_breakdown.external_authority`(nkt+public) 서브셋의
  지표만 별도로 보고하라. 합성 프록시 포함 단일 F1 = 거짓 확신.
- 반드시 **실제 배포(ship) 모델**로 평가하라. 실데이터-학습 모델(v-dd3abab9 등)로 잰 값은
  고객사에 갈 합성-only 모델(실문서 F1≈0.26)의 성능과 무관하다.

## Inputs

- regate_gold: `datasets\gold_real\builds\regate_gold_20260702_013914.jsonl`
- regate_review: `datasets\gold_real\builds\regate_review_20260702_013914.jsonl`

## Proxy Eval Label Counts

{
  "TS": 36,
  "S1": 40,
  "S2": 41,
  "S3": 49
}

## Proxy Eval Authority Breakdown

{
  "external_authority": {
    "count": 85,
    "labels": {
      "TS": 36,
      "S1": 0,
      "S2": 0,
      "S3": 49
    },
    "label_source": {
      "public_definitive": 49,
      "nkt_designated": 36
    },
    "note": "정부지정·공개 확정 = 사람서명 없이도 진짜 정답. 실측 인용 가능."
  },
  "synthetic_proxy": {
    "count": 81,
    "labels": {
      "TS": 0,
      "S1": 40,
      "S2": 41,
      "S3": 0
    },
    "label_source": {
      "koipa_case_based": 81
    },
    "note": "손작성/LLM 프록시 = 분류기와 편향 공유 가능(상관오류). 스모크용, 실정확도 근거로 인용 금지."
  }
}

## Quarantine Reasons

{
  "public_or_ruling_labeled_high_risk_by_llm": 165,
  "ts_downgrade_suspect": 37,
  "public_or_ruling_labeled_high_risk_by_llm+ts_downgrade_suspect": 5
}

## Train Text Overlap (자기검증: proxy_eval=0 이어야 함)

{
  "train": "datasets\\gold_real\\train_subset.jsonl",
  "proxy_eval": {
    "count": 0,
    "total": 166,
    "rate": 0.0
  },
  "train_leak": {
    "count": 48,
    "total": 48,
    "rate": 1.0
  },
  "silver_train_only": {
    "count": 356,
    "total": 356,
    "rate": 1.0
  },
  "quarantine": {
    "count": 207,
    "total": 207,
    "rate": 1.0
  }
}

## Operational Rule

1. Evaluate only on `proxy_eval_nohuman.jsonl` when human review is unavailable.
2. Report `external_authority` subset metrics separately; never cite a blended single F1 as accuracy.
3. Always evaluate with the actual ship model, not a real-data-trained proxy model.
4. Train may use `silver_train_only_nohuman.jsonl`, but do not report it as accuracy.
5. `train_leak_nohuman.jsonl` and `quarantine_nohuman.jsonl` are excluded from eval by default.
6. September customer documents should run in shadow mode because proxy_eval is distribution-limited.
