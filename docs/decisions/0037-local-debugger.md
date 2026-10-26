# ADR 0037: Opt-in local debugger composition

Status: accepted for the local backend increment.

The debugger session APIs are implemented, but the standalone launcher does not yet configure their providers. Add an explicit `enable-debugger` local command using the existing installation lock. Existing installations stay disabled until the command succeeds. This remains synthetic, single-user Dev composition, not production Entra or a React Designer.

## Profile and recovery boundary

Persist a strict, bounded `debugger.json` profile bound to the installation ID and organization/Dev scope, with the fixed database name `debug-sessions.db`. Create the session repository before publishing the marker. Repeated enable returns the same profile without restoring permissions or rewriting keys/projects/activation. No role grants are added: the existing Dev business-author grant already supplies debug.execute.

Profile reads validate marker size/schema/identity and inspect required session/command tables and columns using read-only SQLite. Missing enabled files, orphan database state, corrupt schemas or foreign installation identities fail closed. They must not be silently recreated by a writable provider. Validate before starting an owned Temporal process or constructing writable journals. Partial initialization remains preserved for explicit recovery.

## Owned composition

The enabled backend owns a SQLiteDebugSessionRepository through its existing ExitStack and constructs TemporalDebugRuntime from the owned TemporalRuntimeGateway client factory and permanent dispatch journal. A generic/fake gateway cannot silently become a debugger runtime. The initializer seed path remains unchanged. Existing loopback-only server ownership, placement checks, start fingerprint compatibility, token behavior and normal execution APIs remain unchanged.

Paused debug workflows and command receipts survive orderly launcher shutdown/restart with the same directory, ports, keys, profile, session database, dispatch journal and Temporal history. Preserve the whole state directory as one recovery unit. Enabling or re-enabling debugging never restores revoked access. Step-over/continue remain exact-run requests and actual timer waits.

## Remaining scope

This increment exposes the existing session backend through the local CLI/HTTP launcher. Browser controls, variable inspection/redaction, cancellation/expiry, events, mock connections, parallel stepping, production identity/providers and production operational guarantees remain open. Timer-only local acceptance does not establish every managed-approval or custom-activity debug integration.
