# ADR 0066: Opt-in Temporal live HTTP JSON dispatch

Status: accepted for explicit host/worker registration of live (and dual) HTTP
JSON proof dispatch; default launcher remains off until `enable-live-http`.

## Decision

Provide `HTTPJSONActivities` as an explicit Temporal bridge that owns
`nexusflow.proof.dispatch` for the exact HTTP JSON descriptor reference. Unlike
mock-only `HTTPJSONMockActivities` ([ADR 0055](0055-temporal-http-mocks.md)),
this bridge accepts `execution_mode` `live` or `mock` when the host composes the
matching adapter ports. Live forbids `fixture_digest`; mock still requires it.
Missing ports fail without fallback. Keep mock-only factories unchanged for hosts
that must refuse live.

Derive a run-bound operation identity with kind
`temporal-http-live-operation` for live and retain
`temporal-http-mock-operation` for mock. Identity stays stable across retry
attempts and differs for a new run. Live remains `maximum_attempts=1` /
non-retryable at the descriptor and application-error boundary; durable ambiguous
effect recovery stays open.

## Local-demo enablement

`nexusflow-local enable-live-http` writes a Dev-only profile marker and ensures a
local secret map file exists. `serve` loads the profile and registers
`HTTPJSONActivities.dispatch` on the owned Temporal worker together with the
connection registry, authorized planner/executor, and optional mock fixtures when
also enabled. Without the profile, proof dispatch stays unregistered.

Worker authorization for live HTTP is a dedicated Dev grant of `worker.execute`
for connection resources. Revoked grants are never restored by enable retries.
`put-secret` stores Bearer tokens in the local map keyed by opaque reference;
connection administration continues to store only the reference label.

## Non-claims

Direct compiled-plan Temporal acceptance does not establish built-in project
publication, palette registration, Entra/Key Vault, public egress, browser
F08-A01–A05, or F04-T007 custom Python sandbox isolation. local-demo secrets and
loopback TLS are not production-provider acceptance.
