# 등급 서명 안내

`review_pack.jsonl` 만 검수자에게 제공하십시오.
`blind_key.jsonl` 은 **모든 서명이 끝나기 전까지** 제공하지 마십시오.

## 적어야 하는 것 (`answer_template.jsonl`)

문서마다 아래를 채웁니다. **모르면 비워 두지 말고 "확인 안 됨" 이라고 적으십시오** —
추측해서 고르면 등급이 바뀝니다.

| 칸 | 무엇 |
|---|---|
| `grade` | TS · S1 · S2 · S3 · 판정보류 |
| `secrecy` / `value` / `management` | 0 · 1 · 2 · 확인 안 됨 |
| `evidence_start` / `evidence_end` / `evidence_quote` | 그렇게 본 근거가 본문 어디인가 |
| `rule_id` | 적용한 규칙 |
| `not_higher_reason` / `not_lower_reason` | 왜 한 등급 위·아래가 아닌가 |
| `policy_version` | 어느 판 기준으로 판단했는가 |
| `signer_id` / `signer_role` | 누가 · 어느 자격으로 |

## 지켜야 할 것

- 문서의 출처나 모델 예측을 **추정하지 마십시오.** 본문과 주어진 사실만 봅니다.
- 다른 검수자의 답을 보지 마십시오. 일치도를 재려면 독립 판단이어야 합니다.
- `signer_role` 은 `holder`(비밀 보유 기업) · `client`(고객사) · `orderer`(발주처) ·
  `vendor`(수행사) 중 하나입니다. **vendor 서명은 GOLD 로 치지 않습니다** — 자기채점입니다.
