# ADR 0053: Explicit deterministic Dev HTTP mocks

Status: accepted for the internal HTTP subset of feature 08 mock support.

## Selection and trust

Add `HTTPMockExecutor`, a separate internal adapter with explicit fixture digest
selection. The live executor does not import or fall back to it. Missing or
mismatched fixtures fail closed; no DNS, socket, live connection, sender or secret
provider is available to the mock executor. Mock responses represent fixture
values, not evidence that an external effect occurred.

Freeze `HTTPMockScenario`, `HTTPMockGrant`, scenario and authorization ports, and
closed `HTTPMockError` before parallel implementation. The executor requires a
fresh scoped grant with authoritative `environment_kind="dev"` before private
fixture lookup and repeats authorization after that asynchronous lookup. UAT,
Prod, malformed and cross-scope grants are rejected. Do not infer environment
kind from an ID or display name; the existing environment routing record has no
kind field.

Provide `ExplicitDevHTTPMockPolicy` for hosts with an explicitly configured set
of trusted Dev scopes plus existing fresh `worker.execute` authorization. It
does not discover real deployment kinds or connect to Entra. Host provisioning
must supply correct classification; changing that classification requires
replacing its configuration or implementing a fresh authoritative policy port.
There is no default launcher/API/worker registration or automatic scope grant.

## Fixtures and deterministic behavior

`StaticHTTPMockScenarios` snapshots a bounded immutable catalog keyed by scope,
connection ID and revision. Duplicate keys are rejected, even for identical
fixtures. All reads return fresh snapshots. This is explicit in-memory host
configuration, not SQLite persistence, version history, project package support
or an admin publication API.

Each scenario specifies an exact relative path, method/content type/body and a
bounded status/header/body response. A versioned canonical digest includes the
scope, revision, expected request and response. The executor requires the selected
digest and exact request match before returning the response with the caller's
operation ID. A changed fixture under the same revision cannot satisfy the old
digest pin. Hosts must retain original fixtures/configuration for reproducibility
across restarts; no execution journal or automatic fixture retention is added.

No scenario sequence is consumed and no stateful fail-then-succeed behavior is
simulated. Repeated and concurrent matches return the same fixture bytes; this
does not model provider deduplication, remote effects or retry-after-acceptance
recovery. No real HTTP connection definition or credential is read/resolved by
mock execution. Mock fixture revisions occupy a separate explicit namespace and
do not silently substitute for live registry revisions.

## Bounds, privacy and verification

Validate strict request, grant and fixture snapshots, identifiers, digest, relative
path rules, status, header syntax/count and byte limits. Fixture request paths,
payloads and response headers/bodies stay out of representations and errors. They can contain sensitive
data even though no credential provider exists; use synthetic fixtures and never
publish raw fixture content to logs, export or debugger interfaces.

One bounded outer timeout covers authorization/fixture lookup/result checks;
late answers and suppressed caller cancellation cannot become successful results.
Sync host providers must be bounded/cooperative. Errors have fixed text/closed
codes, `retryable=False` and `delivery_may_have_occurred=False`, since this adapter
has no external delivery capability. Cancellation propagates.

Parallel lanes: fixture validation/catalog, executor/matching, and independent
isolation/privacy/no-network acceptance. Coordinator owns contracts, explicit
host policy, documentation/checklists and regression. No shared dependency change
is required. Independent acceptance uses actual static catalog/policy with a fake
worker authorization port; it does not establish live identity, deployment kind,
persistent fixture storage, Temporal or browser acceptance.

HTTP/email/notification descriptors, other mock connector types, typed workflow
bindings, durable delivery/retry recovery, credentials and Designer/runtime/admin
integration remain open. Built-in descriptor publication also requires artifact
and capability semantics distinct from the current Python bundle catalog.
