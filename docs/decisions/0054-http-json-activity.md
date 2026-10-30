# ADR 0054: HTTP JSON activity metadata and internal mapping

Status: accepted for a typed, metadata-only authoring contract and internal adapter.

## Decision and artifact boundary

Provide `HTTPJSONActivityInput`, metadata-only `HTTPJSONActivityOutput`, closed
`HTTPActivityError` and explicit live/mock executor ports. Expose a fresh HTTP
descriptor through `HTTPJSONDescriptorCatalog.descriptor(reference)`. It has no
`resolve`, publication, palette API or worker registration behavior.

The descriptor pins canonical UTF-8 metadata bytes of kind
`http-json-activity-contract` with adapter contract version 1.0.0. Compute its
`ActivityRef` digest from that document without a self-referential digest field.
This identifies actual contract metadata, not Python source or an executable
artifact. Declare `connector.http.json.v1` as a required capability without
claiming that any current host provides it. Existing Python bundle catalog and
project artifact resolution must continue to reject this reference as unavailable
for publication until built-in executable artifact/capability support exists.

## Business input and authoring

Current `AuthoringValidator` consumes `input_schema`, not a node configuration
slot. Put connection ID/revision, explicit live/mock mode, optional fixture pin,
method, relative path and optional JSON object body in that input schema.
`configuration_schema` is an empty object. Scope and operation ID come from trusted
host arguments and are forbidden business input fields. No credentials, custom
headers, selected origin or secret resolver is exposed.

Live mode forbids a fixture digest; mock mode requires one and still passes through
the existing trusted Dev gate. GET/HEAD forbid a JSON body. Other supported methods
accept an optional JSON object; null/omission means no body, not the JSON literal
`null`. Encode present bodies as canonical UTF-8 JSON. Bound body shape to a tree,
32 levels, 10,000 nodes and 1 MiB encoded bytes; reject cycles, aliases, unsupported
values and non-finite floats. Bound pending traversal and aggregate character
counts before encoding to avoid unbounded intermediate work.

The portable descriptor is ref-free because current offline authoring rejects
even local schema references. Its JSON object body schema and cross-field
conditions describe the supported literals; backend validators additionally
enforce full path, tree and byte rules. Current conservative binding validation
cannot safely combine general variable bindings with these conditional schemas;
bindings remain rejected rather than weakening validation.

## Execution, privacy and errors

`HTTPJSONActivityAdapter` snapshots strict trusted context/business inputs, then
maps to existing live or mock requests. Select exactly the requested port; missing
providers fail without fallback. The live executor retains all scoped registry,
DNS and pre-write checks; the mock executor retains Dev/digest/exact fixture gates.
Ports are trusted host adapters and are not enabled by default.

Validate bounded private provider results, then return only operation ID, status
and selected mode. Never map raw response headers/body into activity output.
Statuses 200–599, including redirects and provider errors, are returned without
automatic status classification, forwarding or retries. Response extraction and
sensitive-field mapping remain a separate feature. JSON payload fields can still
contain caller-chosen secrets: private representations and status-only output are
not a general secret detector or input/package redaction system.

The deadline is at most 30 seconds for provider invocation/result checks after
bounded synchronous normalization. Late results and suppressed cancellation
cannot become success. Synchronous host code must remain bounded/cooperative.
Errors expose closed codes, fixed text and `retryable=False`. Preserve valid known
live transport delivery flags; unknown live invocation failures/timeouts retain
uncertainty. Mock failures never imply external delivery. Cancellation propagates.

## Parallel ownership and remaining scope

Coordinator owns shared contracts/body validation, contract tests, docs and checks.
Descriptor lane owns metadata/schema identity and exact-pin catalog tests. Adapter
lane owns mapping/projection/error tests. Independent acceptance owns real offline
authoring, publication rejection and actual mock/live TLS composition.

Controlled TLS uses test-only asserted numeric-pin loopback dialing; no live public
egress, Entra, cloud or browser acceptance is claimed. Executable built-in artifact
publication, runtime capability registration, Temporal/launcher/API/palette wiring,
variable bindings, general response mapping, credentials, durable retries and
email/notification descriptors remain open. This does not complete feature 08.
