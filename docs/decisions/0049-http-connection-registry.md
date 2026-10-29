# ADR 0049: Immutable environment-scoped HTTP connection registry

Status: accepted for the connection-metadata subset of feature 08.

Start connector implementation with immutable HTTP connection revisions. A
definition selects HTTP connector version 1.0.0, one HTTPS DNS base origin, a
canonical allowlist of 1–50 HTTPS origins and an optional opaque secret-reference
label. The base must be included. No arbitrary headers, bodies, credential-value
or free-form configuration fields are provided. Email/notification definitions have
separate future contracts.

Origins have ASCII DNS names and optional valid ports. Canonicalize host/scheme
case, default 443 and root slash; reject user information, paths, query/fragment,
IP literals, whitespace/control characters, backslashes and malformed labels or
ports. These are syntactic metadata checks, not DNS resolution or SSRF/egress
enforcement. Execution must later check every resolved address and redirect
before transmitting credentials or payloads.

Identity is organization/environment/connection ID/revision label. Appending a
new label allocates a monotonically increasing revision for that connection.
Exact canonical retries return original actor/time metadata; changed reuse
conflicts. SQLite serializes claims atomically, with defaults of 100 connection
IDs per scope and 100 revisions per ID. Existing exact retries work at quota.
There is no mutable active pointer, update, delete or activation operation.

Store the canonical definition privately and bind it through its logical digest
to checksum-protected metadata. Private reads validate both; metadata/history
reads select no definition column. Public responses contain identity, scope,
revision, digest, connector version, reference-presence flag and creator/time;
origin values and reference labels are absent. Checksums detect stored corruption,
not arbitrary coordinated deletion or tampering by a database administrator.

Every public operation requires fresh scoped `connection.manage`. Integrate the
repository through an explicit optional host port; unconfigured authorized hosts
fail unavailable. Default local-demo grants and stores remain unchanged. Signed
API reads and errors do not echo definitions or submitted credential fixtures.
This is additive and does not alter project packages or execution configuration.

An opaque label is not verified to exist in a secret provider and is never
resolved here. Secret-reference authorization/resolution, actual connector
activities, retries/timeouts/cancellation, mocks, package bindings, default local
composition, Admin/Designer UI and full F08 acceptance remain open.
