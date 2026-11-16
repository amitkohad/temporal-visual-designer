# ADR 0070: Authenticated webhook start contracts

Status: accepted for the RT-T01b webhook subset (register/disable + secret-authenticated
deliver into durable starts). Form starts are ADR 0071. Full interpreter, checkpoints
and projections remain open under RT-T01b/RT-T03b parents.

## Decision

Webhook starts reuse the existing durable start boundary ([ADR 0009](0009-durable-starts.md)).
Each accepted delivery becomes a `StartRequest` with `source="webhook"` and the caller
supply idempotency key. Authorization for admin mutate/list uses the signed session
`run.start` permission. Delivery authentication uses a per-webhook shared secret, not
the Designer session token. Active-release snapshotting, input fingerprinting and
at-least-once dispatch semantics are unchanged.

### Definition

A webhook endpoint record is scoped to organization/environment/project/workflow:

- `webhook_id` — Identifier; unique within the org/env scope
- `secret` — shared secret presented on deliver; stored only as SHA-256 hex digest
- `replay_window_seconds` — inclusive absolute skew vs server clock (default 300, max 3600)
- `enabled` — disabled webhooks reject deliver; create/list/disable are the admin surface

### Delivery authentication and replay

Deliver requests must present:

- `X-Nexusflow-Webhook-Secret` — exact shared secret; compared via digest equality
- `X-Nexusflow-Timestamp` — Unix epoch seconds (digits only); rejected when
  `|now - timestamp| > replay_window_seconds`
- `X-Nexusflow-Delivery-Id` — Identifier used as the start idempotency key (≤128)

Missing/wrong secret → typed `unauthenticated`. Replay outside the window →
`invalid_input` (`replay_rejected`). No start intent is reserved on auth/replay failure.

### Deduplication

Start ledger primary key `(org, env, project, workflow, source, key)` with
`source="webhook"` deduplicates deliveries across retries and launcher restarts.
Reuse of the same delivery id with a different JSON payload returns start `conflict`.
Delivery receipt rows mirror schedule occurrence records for restart-safe admin reads.

### Errors

Typed webhook errors: `invalid_input`, `unauthenticated`, `not_found`, `conflict`,
`forbidden`, `unavailable`, `replay_rejected` (surfaced as `invalid_input` on HTTP).

### Local enablement

`nexusflow-local enable-webhooks` writes a Dev-only profile marker and webhooks
database. `serve` loads the profile and exposes signed admin HTTP plus secret-authenticated
deliver. Without the profile, webhook APIs are unavailable.

## Non-claims

No Entra, HMAC request-body signatures, cloud API gateway, form starts (ADR 0071),
checkpoints, projections or Designer palette. This slice does not complete RT-T01b
or RT-T03b parents.
