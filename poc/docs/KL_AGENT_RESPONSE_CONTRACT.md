# KL Portal Agent Response Contract

## Purpose and non-negotiable rule

The KL Portal Agent **always returns a classification answer** for an accepted
classification job.  It never waits for a reviewer before returning the
answer.  The portal displays that answer and performs a human review on every
document afterwards.

`needs_review` is therefore an attention and follow-up marker, not an
"answer unavailable" result.  A missing body, unavailable model auxiliary,
or unavailable regulation retrieval must not turn a completed request into a
blank result.  The conservative missing-body response is still a structured
answer (`label=TS`, `status=needs_review`) so that the portal can show the
reason and record the reviewer decision.

The reviewer confirmation is a separate event:

```
KL request -> Agent answer -> portal shows evidence -> reviewer confirms/corrects
           -> POST /confirm -> confirmed_* audit trail and learning candidate
```

The original `label` remains the Agent prediction.  A reviewer decision is
reported separately as `confirmed_label`, `confirmed_by`, and
`confirmed_at`; it does not overwrite the prediction.

## One result contract across delivery modes

Use the asynchronous interface for ordinary portal traffic.  Synchronous
`POST /api/v1/classify` is supported for a low-latency compatibility call,
but it has the same semantic result contract when the caller is authenticated
as `kl_backend`.

| Delivery mode | How KL receives the final result | Required result behaviour |
| --- | --- | --- |
| Synchronous | `POST /api/v1/classify` | Return the Agent answer in that response. |
| Async poll | `POST /api/v1/classify/async`, then `GET /api/v1/classify/jobs/{job_id}` | `queued`/`running` is not a final answer; a terminal `done` result contains `results[]`. |
| Async callback | Supply `callback_url` when submitting async or batch work | Terminal `done` callback contains the same `results[]` form as polling.  A callback transport failure is retried/DLQ-managed; it must not change the stored job result. |
| Stored-result lookup | `GET /api/v1/classify/{doc_id}` | Read the stored prediction and the later `confirmed_*` reviewer fields.  This is a snapshot, not a replacement for the rich per-inference result. |

For a successful classification result, the portal must treat these as the
stable Agent-facing fields:

- `label`: the representative Agent answer; always one of `TS`, `S1`, `S2`,
  `S3` for a completed result.
- `rule_grade`: an additional rule-engine opinion only when it differs from
  `label`; if it agrees, it is `null`/omitted so the UI shows one grade.
- `status`, `warnings`, `confidence`, `confidence_tier`: outcome and reviewer
  priority context.  They do not suppress `label`.
- `evaluation_factors`, `rule_evaluation_factors`, `evidence`,
  `decision_path`: evidence and decision context for the every-document
  portal review.  Unknown S/V/M evidence must remain unknown rather than be
  rendered as a zero.
- `regulation_reference`: retrieved regulation source text, if available.
- `regulation_summary`: optional local-LLM summary of the retrieved source;
  it must never be used by itself as a legal or classification authority.

Internal diagnostic fields such as score vectors, raw model grade, candidate
sets, elapsed time, and automation-shadow observations are not a KL Portal
contract.  Do not build portal business rules on them.

## Explanation degradation is explicit, not a missing answer

RAG retrieval and local-LLM summarisation are **explanatory supplements**.
The classifier and rule result are created first.  When a supplement is
disabled, has no applicable source, exceeds its batch time budget, or fails,
the Agent still returns `label`, rule disagreement where relevant, and its
classification evidence.

The KL wire result exposes:

- `explanation_status`: whether the regulation/RAG+LLM explanation was
  available, not applicable, disabled, skipped for budget, or degraded.
- `fallback_reason`: a machine-readable reason whenever the expected
  explanatory material is absent or degraded.  It explains the supplement
  state only; it never changes the Agent grade.

Portal rendering rule:

1. Always render `label` first, then an additional `rule_grade` only when
   present.
2. Render source citations before an LLM summary.
3. When `explanation_status` is not available, retain the answer and show a
   compact "explanation unavailable" state using `fallback_reason`; do not
   show an empty answer or infer that the document is public.
4. Send the reviewer’s every-document confirmation/correction through
   `POST /api/v1/confirm` with the authenticated reviewer identity.

## Identity and review record

Each portal confirmation must preserve the person who made the review.  With
JWT authentication, the server binds the audit identity to the JWT subject;
the client-provided `actor.user_id` is not authoritative.  A portal
integration using a shared API key must have an approved identity propagation
mechanism before it can be treated as a human-review audit source.

The resulting record must retain at least the document and inference identity,
Agent label, confirmed label, reviewer identity, confirmation time, reason,
policy/version context, and any correction lineage.  These operational review
records are the source for quality measurement and future retraining
candidates; they are not a blocking pre-answer queue.

## Deployment verification procedure

Run these against the target portal integration environment, never against a
customer document corpus.  The supplied E2E tool creates synthetic records
and leaves audit/job records behind.

```bash
# From the bundle/repository poc directory.  Verify live/ready first.
curl -fsS http://TARGET:8000/api/v1/healthz/live
curl -fsS http://TARGET:8000/api/v1/healthz/ready
curl -fsS http://TARGET:8000/api/v1/healthz | jq '.build, .model_version, .readiness'

# End-to-end async, stored-result, fail-secure, and optional reviewer-confirmation check.
BASE_URL=http://TARGET:8000 API_KEY="$API_KEY" \
  python scripts/e2e_kl_api_flow.py --json reports/kl-agent-e2e.json

# Include a controlled callback receiver and an authorised reviewer token when available.
BASE_URL=http://TARGET:8000 API_KEY="$API_KEY" CONFIRM_TOKEN="$REVIEWER_JWT" \
  python scripts/e2e_kl_api_flow.py \
  --callback-url https://controlled-receiver.example/koipa-callback \
  --confirm --json reports/kl-agent-e2e-confirm.json
```

Record the `healthz.build.git_sha`, model version, configuration profile,
result JSON, callback receiver log, and reviewer audit event together.  A
local unit test or a `healthz` response alone is not proof that the KL Portal
received the answer or preserved reviewer identity.

## Release evidence boundary

Before claiming the Agent contract is released, collect all of the following
for the exact deployed SHA:

1. Sync, async polling, and callback results with matching Agent fields.
2. A RAG/LLM-success case and each expected explanation fallback state.
3. A rule-agreement and rule-disagreement case.
4. An unavailable-body case proving the terminal structured `TS` answer and
   reviewer marker rather than an empty answer.
5. A portal reviewer confirmation whose authenticated identity and
   `confirmed_*` lookup fields agree.
6. The current release manifest and strict gate evidence bound to the same
   source, image, model, dataset, and configuration hashes.

The last two items require the actual 211/portal deployment and cannot be
proved from this checkout alone.
