# ADR 0059: Explicit local-demo mock fixture administration

Status: accepted for the synthetic loopback local-demo host.

## Decision

Add `nexusflow-local enable-mock-fixtures` as an explicit installation-locked
operation. Initialize a fixed private fixture SQLite store and installation-bound
Dev marker, then wire existing fixture PUT/GET/discovery APIs into `open_backend`
when the profile is present. Default initialization remains disabled and creates
no fixture files or administrator grant. No fixture is seeded automatically.

Freeze local profile/load/enable interfaces before parallel implementation. The
profile records schema `0.1`, type `local-demo-http-mock-fixtures`, canonical
installation ID, exact installed Dev scope and literal `fixtures_database` value
`http-mock-fixtures.db`. Marker file is `http-mock-fixtures.json`. Bound the marker
to 16 KiB, reject malformed/duplicate fields and validate existing storage schema
read-only before startup. Do not create or repair state while loading profiles.
Validate the effective committed schema, including WAL changes, rather than
using SQLite immutable mode, which ignores WAL. Read-only SQLite may manage
transient locking/WAL sidecars; the guarantee is no fixture data/schema or grant
repair, not byte-for-byte immutability of locking files.

Explicit first enablement installs `local-mock-fixture-admin`, a synthetic
`organization_administrator` grant for the demo user in Dev, using expected
revision zero. This role includes existing administrator capabilities beyond
fixture operations; CLI output and documentation state the role explicitly.
Do not add a new role or weaken API permissions. The command does not restore
membership or the original author grant and does not grant UAT/Prod or worker
permission. It configures no live destination or HTTP transport.

Write the marker last. Partial initialization, missing files, orphan database
sidecars or mismatched installation/scope fail closed and preserve existing data.
Repeated enablement of a valid installation is read-only and does not recreate
a missing/revoked administrator grant. Serving and restarting also do not grant
or repair permissions. Grant tombstones retain their existing protection against
recreation with revision zero.

## Lifecycle and boundaries

The serve command checks the optional profile before starting runtime/network
resources. Backend composition opens the actual store through its owned exit
stack, sets only the installed Dev scope as eligible and closes it on shutdown.
Seed initialization does not activate optional fixture providers. A malformed
profile must not silently disable the feature or replace its database.

This makes fixture administration usable in the local host. It does not register
the mock dispatch activity in the worker or make HTTP descriptors publishable.
Ordinary welcome workflows, managed tasks and debugging retain their existing
composition. The local server may own a real Temporal process while these API
operations remain administration only; do not infer mock workflow execution from
launcher lifecycle tests.

All identities/signing material and grants here are synthetic local-demo state.
The private fixture database contains plaintext synthetic content. Browser
screens, fixture package transfer/history/delete, cloud/Entra deployment,
persistent-fixture workflow dispatch and live effects remain separate work.

See [increment evidence](../../specs/001-nexus-flow-designer/implementation-local-mock-fixtures.md).
