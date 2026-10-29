# HTTP connection registry increment

Branch: `codex/051-http-connection-registry`, based on main `6ce9950`
(merged PR #48).

## Implemented boundary

Start feature 08 with immutable environment-scoped HTTP connection revisions.
The definition selects connector version 1.0.0, HTTPS DNS base origin, 1–50
canonical allowed origins and optional opaque secret-reference label. Fixed
fields reject credential-bearing URLs, arbitrary headers/bodies, paths, query,
fragment, IP literals and malformed origins. No DNS, secret or network provider
is invoked; these checks are not execution-time SSRF enforcement.

SQLite atomically reserves scoped connection/revision identities and per-ID
monotonic revision numbers. Exact canonical retries retain original metadata;
changed aliases conflict. Defaults cap each scope at 100 connections and each
connection at 100 revisions, with retries permitted at quota. Private definition
reads verify canonical digests; metadata reads verify checksum/key bindings and
select no definition column. Recovery and concurrent claims are tested.

An optional `ApiBackend.connections` port exposes signed administrator-only
revision PUT/GET/history under fresh `connection.manage`. Public responses contain
identity, scope, revision, digest, reference-presence flag and creator/time, never
origins or reference labels. Invalid requests and provider failures do not echo
submitted values. Existing API domain status behavior is unchanged; connection
quota conflicts are scoped to the new error type.

Service validation uses raw fields before serialization to reject malformed
provider booleans without coercion or serializer-warning leaks. Repository input
revalidation is strict and warning-free. Metadata-only APIs do not call private
definition getters, runtime, secret or transport ports.

See [ADR 0049](../../docs/decisions/0049-http-connection-registry.md) and
[walkthrough](../../docs/http-connections.md). Hosts must explicitly provide and
own the registry; default launcher stores/grants are unchanged. Connection
definitions are outside project packages and have no activation pointer.

## Parallel ownership and verification

The coordinator froze typed contracts before repository/recovery,
service/API/authentication and independent schema/privacy acceptance lanes.
Independent review identified and verified fixes for malformed-model boolean
coercion and serialization warnings. No blocking findings remain.

Focused tests: 36 repository tests passed (1.17 seconds); 55 definition and 18
independent acceptance tests passed together (73, 0.89 seconds); 25 signed API
tests passed (12.73 seconds). Coverage includes immutable retries, scoped quotas,
concurrent writes, rollback, corruption, metadata-only SQL, fresh revocation,
cross-environment isolation, sanitized errors and zero DNS/network calls.

Ruff lint and formatting (451 Python files), mypy (102 source files), source/wheel
build and `git diff --check` passed. Full default regression: **2,264 passed,
one expected Linux TCP TIME_WAIT test skipped on Windows, 75 integration tests
deselected** (405.37 seconds). Runtime/Temporal code was not changed and the real
Temporal suite was not rerun for this metadata-only change. No actual connector,
cloud or browser acceptance is claimed.

## Remaining scope

Actual HTTP/email/notification activities, secret-reference existence and worker
authorization/resolution, DNS/IP/redirect egress checks, transport retries,
idempotency/ambiguous delivery, mocks, package binding, default local composition,
Admin/Designer UI and full F08 acceptance remain open.
