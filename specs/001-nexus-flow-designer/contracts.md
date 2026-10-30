# Shared contracts, version 0.1 — proposed for review

This document is the single contract authority. Root/integration owner alone edits it. Feature agents propose changes; no consumer silently changes a shared shape. Freeze approved contracts at task NF-T001 before parallel implementation. These are normative semantic contracts, not generated schemas or implemented APIs.

## C01 — Identity and scope

RequestContext: request_id, organization_id, environment_id where applicable, actor {issuer, subject, directory_tenant_id}, resolved_permissions, correlation_id. Never trust tenant or role claims from a browser without server verification and local membership resolution. Entra identity keys include issuer/directory tenant plus subject; email is not an identity key. Validate signature, issuer, audience, lifetime and organization binding. Directory-tenant mode is a decision gate; do not infer an organization from email domain.

Every resource lookup includes organization_id; environment-owned resources include environment_id. Service-to-service access has a scoped workload identity. Entra groups use stable object IDs; group-overage resolution is server-side, with bounded caching and denied authorization when required membership cannot be resolved. Errors distinguish unauthenticated, forbidden, not found, conflict, invalid input, incompatible dependency, and unavailable without exposing another organization's resources.

Default roles: business author (Dev author/debug, Dev/UAT package import/activate), task participant (eligible task actions), activity publisher (Admin import/publish executable activities), organization administrator (membership/connections), production support (Prod import/activate/rollback). Membership alone grants none of the other roles. Explicit scoped grants may combine roles. All permissions checked on API calls, downloads and event subscriptions.

## C02 — Project revision and executable definition

Project: project_id, organization_id, name. Revision: revision_id, project_id, parent_revision_id, author, created_at, content_digest, definitions, layout. Mutable draft saves require expected_revision; stale writes return conflict without overwriting. Restore creates a new revision. Layout never changes execution semantics.

WorkflowDefinition: schema_version, workflow_id, input_schema, output_schema, start_definitions, nodes, edges, error_routes. Node: stable node_id, kind, activity_ref where applicable, input_mapping, output_schema_ref, policy, named_ports. Edges reference node IDs and named ports. Conditions use a versioned restricted expression contract, no arbitrary code/eval or implicit I/O. Explicit bounded iteration is allowed; arbitrary cyclic graphs are rejected. Compiler validation covers joins, reachability, types, output references, terminal paths and cancellation.

FormDefinition: form_id, schema_version, data_schema, layout, visibility_rules, validation_rules, field_permissions, attachment_policy. Server enforces validation and field permissions independently of client visibility. Expression engine/language selection is pending; contract semantics and golden fixtures precede implementation.

## C03 — Activity descriptor and executable bundle

ActivityRef: activity_id, version, artifact_digest. Descriptor: descriptor_schema_version, display_name, category, description, input_schema, output_schema, configuration_schema, connection_requirements, execution_policy, runtime_requirements, documentation. Versions are immutable. Python bundle declares entrypoint, Python compatibility, locked dependency artifacts, artifact digests, required capabilities and provenance. No install hooks or package code execute in the import API process. Validation/build/run occur in isolated jobs with bounded resource and network policy. Source-only uploads require a controlled build before publication; executable release artifacts are immutable.

Catalog publication scope and availability are explicit; default organization-local. Project packaging captures exact descriptors and executable content/dependencies for destination validation. An imported project cannot publish arbitrary executable code under a business-author role; required activity digests must be approved by an activity publisher in the destination. This distinction preserves whole-project portability without granting execution trust automatically.

## C04 — Project package

Manifest: package_schema_version, project_id, release_id, organization_id, created_at, source_revision_id, workflow_refs, form_refs, activity_refs, configuration_schema, connection_declarations, runtime_compatibility, content_index, package_digest. Each content_index item has normalized relative path, media type, size and digest. Packaging digest canonicalization excludes its own digest/signature field; define exact encoding before implementation.

Whole project archive contains immutable definitions, forms, activity descriptors/artifacts or included content-addressed dependency artifacts, optional tests/docs. No credentials, execution payloads, environment addresses or approval data. Initial policy permits promotion only within the same organization. Cross-organization transfer is a future explicit authorization policy.

Import validates scope, supported schemas, total/expanded size, entry count, path traversal, symlinks, duplicate normalized paths, checksums, dependencies, executable trust and runtime compatibility. Validation is non-activating and has no connector side effects. Interrupted imports expose no partial release; repeat identical imports are idempotent. Dependency/compatibility failures return a report. Limits are configuration with test fixtures for each boundary.

## C05 — Deployment and start routing

Deployment: deployment_id, organization_id, environment_id, project_id, release_id, package_digest, binding_revision_id, expected_active_generation, status, actor, timestamp, optional_evidence. BindingRevision: immutable mapping of connection/config declarations to environment-local resources with secret references, never secret values. Secret rotation is audited; pin binding identity/version policy without embedding or indefinitely retaining secret material.

Activation transaction compare-and-swaps a single project active pointer {release_id, binding_revision_id, generation}. All included workflows resolve through this pointer; never update pointers per workflow. Concurrent activations return one winner and a conflict. Start and activation races resolve one coherent generation; a start pins release/runtime/activity digests before enqueue. Use an outbox/idempotent delivery mechanism where database and Temporal cannot share a transaction. Retries preserve the selected release. Removed workflows reject new starts after activation; existing runs continue.

Schedules and webhook registrations belong to deployment routing: activation reconciles them idempotently and disables superseded registrations without duplicate starts. Define a safe activation/reconciliation state machine before enabling external starts. A package is not reported ready until its required workers and trigger configuration are available. Rollback uses the same validation/CAS path. Garbage collection cannot remove dependencies referenced by retained releases or live executions.

Evidence: optional change_request_number, notes, approval_attachment_refs; empty evidence allowed in Prod. Audit always records actor, scope, requested/previous/next release, checksum, outcome, time and correlation. Evidence upload has authorization, size/type handling, malware quarantine and retention; file extensions are not a trust boundary.

## C06 — Run and step events

StartRequest: scope, project_id, workflow_id, start_source, optional release_ref for authorized Dev test, input, idempotency_key, mode (normal/debug), test_configuration_ref. Idempotency identity is organization/environment/project/workflow/start_source/key; store input digest alongside it, not as part of uniqueness, so changed-input reuse returns conflict. Run: run_id, release_id, binding_revision_id, runtime_version, mode, state, started_at. Test runs also pin a frozen draft snapshot if not published.

StepEvent: event_id, run_id, sequence, node_id, invocation_id, loop_iteration/branch_path, attempt, kind, timestamp, redacted_payload_refs. Sequence resumes subscriptions without gaps/duplicates affecting UI state; events are projections, Temporal history is execution authority. Result payload schema validation occurs at boundaries. At-least-once external effects require idempotency support or explicit documented duplicate risk; do not claim external exactly-once execution.

## C07 — Human tasks

Task: task_id, run_id, node_invocation_id, scope, form_ref, resolved_assignment, approval_rule, status, deadline, revision. Eligible Entra users/groups and approval membership resolve at task creation and are audited; reassignment/delegation are explicit versioned changes. Fresh authorization is checked at action time; unavailable directory resolution must not grant access.

DecisionCommand: task_id, expected_revision, idempotency_key, action, form_response, comment, attachment_refs, actor. Actions include claim, approve, reject, request_information, submit_information, delegate, reassign. Rules cover any-one, all, sequential stages and conditional routing. Quorum is deferred unless explicitly selected. Terminal task transition is serialized; duplicate commands return original result and cannot advance twice. An outbox delivers accepted completion to runtime until acknowledged. Reminder/escalation timers have stable IDs and cancel on terminal completion. Race rules: a terminal decision committed first wins against deadline completion; later submissions receive current terminal state. Approver and task-assignee semantics must be tested separately.

## C08 — Debugger

Debug commands: pause, continue, step_over, cancel, with run_id, expected_debug_revision and idempotency_key. Breakpoint: node_id, before-step. Dev-only permission required. Step-over permits one selected ready node invocation through its boundary; running external effects continue. Parallel branches expose per-invocation states and use stable invocation IDs; a permit cannot accidentally release another loop iteration. Inspect returns authorized redacted snapshots. Pauses use durable signals/state, not suspended worker threads. No arbitrary Python stepping, variable mutation or live definition edits.

## C09 — Connections and connector invocation

Connection: connection_id, scope, connector_type/version, nonsecret_configuration, secret_reference, allowed_destinations, revision. ConnectorInvocation: run/step identity, activity_ref, binding_revision_id, mapped_input, stable operation_id, timeout/cancellation policy. Resolve credentials only in authorized workers. Enforce egress/SSRF protections at every redirect and resolved destination, including metadata/private addresses according to configured allowlists. Logs, errors, debugger and exported fixtures redact secrets. HTTP status retry classification is explicit; email/notifications document uncertain delivery and duplicate behavior.

## C10 — Operational boundaries

Telemetry carries organization/environment/project/release/run correlation without payloads or secrets. High-cardinality run IDs belong in logs/traces, not unbounded metrics labels. Quotas cover starts, active effects, waiting tasks, artifact storage and import execution. Placement adapter maps scope to Temporal namespace/task queues/worker pools; no UI can supply arbitrary destinations. Storage, secrets, identity and artifact adapters expose portable interfaces; Azure implementation is initial target, not proof of another provider implementation.

Each contract needs positive, invalid-input, authorization, retry and compatibility fixtures. Providers and consumers run the same conformance corpus. Changes require versioning, migration notes, and affected feature approval.

### C09 executable HTTP connection metadata subset

The bounded registry subset is frozen in `connectors/types.py` and [ADR 0049](../../docs/decisions/0049-http-connection-registry.md). HTTPConnectionWrite selects fixed HTTP version 1.0.0, canonical HTTPS DNS `base_origin`, 1–50 `allowed_origins` including the base, and optional opaque `secret_reference`. These fixed fields are the first nonsecret HTTP configuration shape; generic configuration and other connector types remain gated.

Connection identity is scope/connection ID/revision label. Revisions are immutable and numbered per connection; retries retain the original metadata, changed aliases conflict. ConnectionMetadata exposes scope/identity/revision/type/version, definition digest, reference-presence flag and creator/time. Private ConnectionRecord retains canonical definition JSON; metadata/history reads select no definition column. There is no active pointer or package/export binding.

Secret labels are not resolved or verified here. DNS/IP/redirect restrictions, authorized worker resolution, ConnectorInvocation/Result/Error execution contracts, actual activities, transport retry/idempotency and mock ports remain open. This subset does not close all of C09 or feature 08.

## Executable foundation subset

The initial Python models in nexusflow/contracts implement schema version 0.1 for request-context, error, activity-descriptor, workflow, project and step-event. JSON Schema export is 2020-12. Use validate_contract for structural plus foundation graph checks; exports alone cannot enforce graph semantics. Scope/RequestContext contain explicit organization/environment scope and are transport contracts, not trusted authentication. The initial project contract covers workflow/activity dependency composition, not the final deployable project package or forms. Unimplemented sections of this document remain future contracts. React consumes exported schemas; Python is the authoritative model source. See ADR 0001 and docs/development.md for exact boundaries.

## Authoring binding subset (0.1)

The offline authoring increment reserves a complete activity input field envelope `{"$binding":{"schema_version":"0.1","source":"input","path":"/employee"}}`; step sources add `node_id`. See `nexusflow/modules/designer_forms/types.py` and [ADR 0005](../../docs/decisions/0005-authoring-validation.md) for source availability and conservative type proof. This contract supports variable selection only; the general expression language remains pending. Proof runtime version `proof.v1` rejects bindings, so authoring validation alone does not establish executability. Portable binding schema export is `uv run nexusflow-binding-schema`.

## Form validation subset (0.1)

FormVisibilityRule contains schema_version, field_id and a native JSON Schema `when` predicate. FormValidationRule contains schema_version and `data_schema`; predicates and business rules are evaluated offline with no general expression engine. See `designer_forms/form_types.py` and [ADR 0006](../../docs/decisions/0006-form-validation.md). FormGateway supplies trusted per-resource readable/editable fields and current revision, and rechecks authorization/revision at commit. The preview contract never carries unreadable/writeOnly values. Export these feature contracts with `uv run nexusflow-form-schemas`; live task/start APIs remain separate work.

## Activity source-bundle intake subset (0.1)

ActivityManifest contains schema_version, common descriptor, Python runtime metadata and exact source-file hashes/sizes. PythonRuntime declares language, python_version, entrypoint and locked vendored dependencies; mutable aliases and undeclared payload files are invalid. The artifact digest covers metadata and file hashes, excluding its own declared value. See `activity_catalog/types.py`, `digests.py` and [ADR 0007](../../docs/decisions/0007-activity-bundle-intake.md). Export with `uv run nexusflow-bundle-schema`. Metadata validation does not publish a descriptor or establish executable worker compatibility; dispatch/sandbox contracts remain open.

## Catalog lifecycle subset (0.1)

CatalogEntry is an immutable metadata snapshot with scope, exact ActivityRef, manifest_json, state, generation and publisher/update provenance. Lifecycle is published/deprecated/withdrawn; deletion retains a permanent version reservation. RuntimeCapabilities is a required publication-policy port; existing publication retries cannot resurrect a withdrawn entry. Internal usage holds protect retained exports and logical purge. See `activity_catalog/catalog_types.py` and [ADR 0008](../../docs/decisions/0008-activity-catalog.md). The bundle schema exporter also writes activity-catalog-entry.schema.json. Worker dispatch and automatic consumer retention remain open.


## C09 executable destination preflight subset

[ADR 0050](../../docs/decisions/0050-http-destination-preflight.md) freezes the internal HTTPDestinationRequest and ApprovedHTTPDestination types, WorkerDestinationAuthorization and IPv4Resolver ports. Requests bind scope/connection ID/revision/operation ID to an origin-relative path. Approved plans carry exact checked IPv4 addresses, HTTPS origin/TLS host/port, selected definition digest and bounded redirect count; URLs remain private routing data, not log/export/debug payloads.

Fresh worker permission precedes registry/DNS access at each hop. Canonical allowlists precede bounded injected A-record resolution; every answer must pass the conservative public IPv4 policy. Redirects retain operation identity and require fresh approval. Validation/authorization/blocked targets are non-retryable; unavailable providers/deadlines have a retryability hint without automatic retries or effects.

Preflight itself performs no HTTP or credential resolution. Downstream transport must honor pins/TLS identity and disable independent DNS/proxy/redirect fallbacks. Invocation run/project/step binding, workflow response/body schemas, delivery journal/idempotency, private/IPv6 policy and full F08 acceptance remain open.

## C09 internal pinned HTTPS attempt subset

[ADR 0051](../../docs/decisions/0051-http-pinned-transport.md) freezes internal
`HTTPAttemptRequest` (fixed method/content type and private bytes),
`HTTPAttemptResult` (operation ID, status and private raw headers/body),
`PinnedHTTPSender` and closed `HTTPTransportError` contracts. Errors have fixed
text, `retryable=False` and a conservative `delivery_may_have_occurred` flag set
before writing. Cancellation propagates; it does not establish no remote effect.

The actual A-only resolver validates all bounded answers and deadlines. The
single-attempt HTTPS adapter validates structural pins, dials the first numeric
IPv4 only, verifies the original TLS hostname, and bounds framing/body/response
time. It neither follows redirects nor supplies credentials, custom headers,
proxies, automatic retries or decompression. Raw response data is not a debugger
or export contract. Routing records are not signed capabilities; fresh worker
authorization and secret/run binding require a trusted execution coordinator.

## C09 internal credential-free attempt coordination subset

[ADR 0052](../../docs/decisions/0052-http-attempt-execution.md) freezes
`CredentialFreeDestinationPlanner`, `HTTPAttemptExecutor`, `HTTPExecutionError`
and the optional synchronous `PinnedHTTPSender.before_write` hook. Requests
provide scoped revision/operation/path and bounded method/body snapshots rather
than external routing approvals. Reject configured secrets before DNS; recheck
authorization and immutable bindings after DNS and after TLS before the first
HTTP write. The guard must return `None` and be called exactly once by the
coordinated sender. Authorization is not atomic with a remote effect after the
last decision, and host ports remain trusted.

Closed error codes include `credentials_required`; all execution errors have
`retryable=False`. Preserve trusted pre-write no-transmission flags; unknown
sender failures, invalid provider results and outer timeouts during send retain
delivery uncertainty. Private responses are validated for operation identity,
status, header shape/size and body bytes; raw values are not safe workflow/debug
or export contracts. No redirect chain, secret resolution, durable effect/retry
journal, run/project/step binding, default worker or browser endpoint is added.

## C09 explicit Dev HTTP mock subset

[ADR 0053](../../docs/decisions/0053-dev-http-mocks.md) freezes private
`HTTPMockScenario`, `HTTPMockGrant`, async scenario lookup and fresh mock policy
ports. `StaticHTTPMockScenarios` snapshots bounded scoped revisions;
`scenario_digest` uses a versioned canonical definition with binary fields encoded
as base64. `HTTPMockExecutor` requires an explicit digest pin and exact
path/method/content-type/body match, returning private fixture results with the
requested operation ID. Fixture digests include scope, revision and response;
operation IDs are not fixture identity.

Require authoritative Dev classification and fresh permission before lookup and
again afterward. `ExplicitDevHTTPMockPolicy` combines an explicit trusted Dev
scope configuration with fresh worker permission; it does not discover live
environment kind. Environment names are not eligibility evidence. The mock path
has no live sender, DNS, HTTP or secret provider, and live execution never falls
back to it. Closed mock errors are non-retryable with no external delivery.
Fixtures are in-memory host configuration, not persisted project/admin artifacts.
No descriptors, stateful retry simulation, workflow registration or UI are added.

## C09 HTTP JSON activity metadata and mapping subset

[ADR 0054](../../docs/decisions/0054-http-json-activity.md) freezes
`HTTPJSONActivityInput`, status-only `HTTPJSONActivityOutput`, closed
`HTTPActivityError` and explicit live/mock executor ports. Business input contains
connection ID/revision, mode, fixture pin, method, relative path and optional JSON
object. Scope and operation ID are trusted host arguments, not input properties.
GET/HEAD forbid bodies; mock mode requires a digest and live mode forbids it.
Canonical body encoding enforces tree/depth/node/byte bounds before dispatch.

`HTTPJSONActivityAdapter` selects one provider without fallback, validates bounded
private results and drops all raw response headers/body. Output includes mode so
mock metadata is distinguishable from live attempts. Statuses do not imply
automatic retries or success/failure routing. Unknown live failures retain delivery
uncertainty; mock failures never imply remote delivery. No general response
mapping or secret detection/redaction is promised.

`HTTPJSONDescriptorCatalog` supports exact metadata references only; it has no
executable artifact resolver. The digest hashes actual versioned canonical
contract metadata, not source code. Ref-free schemas support current literal
authoring while backend rules enforce additional body/path limits. Existing
Python catalog/publication stays blocked for this built-in until executable
artifact and capability semantics are implemented. General bindings, default
palette/API/worker registration, credentials and other descriptors remain open.
