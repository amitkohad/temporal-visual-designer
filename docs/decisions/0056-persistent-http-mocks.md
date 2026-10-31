# ADR 0056: Persistent private HTTP mock fixtures

Status: accepted for explicitly composed local host storage.

## Decision

Add `SQLiteHTTPMockScenarios` as an alternative to the existing in-memory
`HTTPMockScenarios` port. A trusted host writes validated synthetic fixtures with
`put`; the existing mock executor reads them with the asynchronous `get` port.
This is internal storage, not an authenticated administration API or a project
package format. No default host wiring, workflow format or live transport changes.

Freeze `HTTPMockFixtureReceipt`, private storage errors and canonical codec APIs
before parallel implementation. The receipt contains only scope, connection
ID/revision, existing scenario digest and encoded byte count. It contains no
request path, headers or body. Private fixture documents use the same versioned
definition and digest as static scenarios; replacing storage must preserve pins.

The tuple organization/environment/connection/revision is immutable. Identical
retries return the original logical receipt; a different fixture at the same key
conflicts. An immediate transaction serializes independent writers and atomically
checks per-scope fixture count and encoded byte quotas. An exact retry remains
valid at quota. There is no overwrite, delete or mutable latest pointer.

Strict canonical decoding rejects malformed types, unknown/duplicate fields,
non-finite values, invalid base64 and noncanonical documents. Bound encoded bytes
before loading stored fixture data; validate content, digest, byte count and
scope/key binding before returning a fresh independent snapshot. Corruption
fails closed and must never be repaired by silently overwriting a retry. Quotas
cover the canonical encoded document, including base64 expansion.

## Trust and operational limits

The SQLite file contains private fixture request and response data in plaintext.
Use synthetic data and host-controlled storage permissions. Checksums detect
accidental corruption; they are not signatures against a database administrator.
Storage does not establish Dev eligibility or worker permission. Those remain
fresh checks in `HTTPMockExecutor`, including after lookup. UAT/Prod must not gain
mock execution merely because a scoped row exists.

SQLite work is serialized per instance and dispatched off the event loop for
runtime reads. Cancellation can stop awaiting a read without interrupting the
underlying bounded storage operation; it does not authorize releasing a result
after cancellation. No external network or live fallback is introduced.

This local adapter does not establish cloud-scale storage, encrypted secrets,
retention/backup policy, admin/Designer fixture management, project export/import,
multi-step mock scripts, durable external effects or executable publication.

## Parallel work and evidence

The codec lane owns serialization and malformed-input tests; the repository lane
owns SQLite transactions, integrity, quotas and concurrent writer tests; the
independent acceptance lane composes actual storage, policy, executor and activity
adapters. The coordinator owns frozen types, documentation and regression checks.
See the [increment evidence](../../specs/001-nexus-flow-designer/implementation-persistent-http-mocks.md).
