# No-Human Proxy Gold Report

## Verdict

- locked_gold_eval: 0
- proxy_eval: 229 / 777
- silver_train_only: 356
- quarantine: 192
- Use proxy_eval for smoke/regression only. Do not call it true gold.

## Inputs

- regate_gold: `datasets\gold_real\builds\regate_gold_20260702_013914.jsonl`
- regate_review: `datasets\gold_real\builds\regate_review_20260702_013914.jsonl`

## Proxy Eval Label Counts

{
  "TS": 55,
  "S1": 54,
  "S2": 52,
  "S3": 68
}

## Proxy Eval Sources

{
  "rule_llm_agreement": 43,
  "codex_review": 19,
  "public_definitive": 50,
  "koipa_case_based": 81,
  "nkt_designated": 36
}

## Quarantine Reasons

{
  "ts_downgrade_suspect": 37,
  "public_or_ruling_labeled_high_risk_by_llm": 150,
  "public_or_ruling_labeled_high_risk_by_llm+ts_downgrade_suspect": 5
}

## Train Text Overlap

{
  "train": "datasets\\gold_real\\train_subset.jsonl",
  "proxy_eval": {
    "count": 63,
    "total": 229,
    "rate": 0.2751
  },
  "silver_train_only": {
    "count": 356,
    "total": 356,
    "rate": 1.0
  },
  "quarantine": {
    "count": 192,
    "total": 192,
    "rate": 1.0
  }
}

## Operational Rule

1. Evaluate only on `proxy_eval_nohuman.jsonl` when human review is unavailable.
2. Train may use `silver_train_only_nohuman.jsonl`, but do not report it as accuracy.
3. Exclude `quarantine_nohuman.jsonl` by default until a human or customer-side authority resolves it.
4. If proxy_eval has train text overlap, report scores as regression smoke only, not honest accuracy.
5. September customer documents should run in shadow mode because proxy_eval is distribution-limited.
