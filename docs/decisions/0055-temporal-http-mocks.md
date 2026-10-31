# ADR 0055: Opt-in Temporal dispatch for HTTP mocks

Status: accepted for explicit Dev mock workflow verification, not live HTTP dispatch.

## Decision and worker boundary

Register `HTTPJSONMockActivities.dispatch` as `nexusflow.proof.dispatch` only when
a host deliberately includes it in a Temporal worker. The existing proof
workflow already dispatches activity nodes through that name; its serialized
plan, control flow and replay implementation need no change. The new adapter
accepts the exact HTTP JSON contract reference and mock mode only. It has no
live executor or transport dependency and does not change the default launcher.

Freeze trusted activity context, prepared-call and adapter/error contracts before
parallel implementation. A pure host-side validator checks actual SDK activity
metadata against configured worker scope, namespace and task queue, including
workflow namespace, workflow/activity types and node/activity identity. Validate
release/configuration pins, proof runtime version, exact descriptor reference,
bounded strict literal JSON and the legacy workflow/node operation identity.
Reject mismatches and live mode before invoking the HTTP mock adapter.

The underlying HTTP mock path still requires fresh trusted Dev eligibility and
worker permission before and after fixture lookup. Placement checks do not infer
Dev eligibility from the environment name and are not substitutes for that policy.
Host ports and Temporal access are trusted boundaries; this is not a public start
authorization service or a sandbox for malicious worker adapters.
Release/configuration fields are structurally validated and bound into operation
identity; this bridge does not look them up in a durable release/start registry.

## Run-bound operation identity

The existing proof call operation ID contains `:` and omits Temporal run identity.
Do not forward it as an HTTP Identifier. Verify its expected workflow/node value,
then derive an Identifier-safe `http-` hash from versioned canonical identity:
worker/workflow namespaces, queue, actual workflow ID/run ID, scope, node,
release/configuration pins, exact activity reference and validated input digest.
Exclude retry attempt. Redelivery in one run reuses identity; another run or
different pinned invocation receives a different identity.

This is deterministic mock repetition, not a durable effect journal, exactly-once
delivery or live HTTP retry policy. Live dispatch remains blocked until recovery
for ambiguous external effects is designed. Mock fixture/runtime authorization
is still checked on retries; operation identity does not grant permission.

## Privacy, failures and compatibility

Parse input objects with duplicate-key/non-finite rejection and bounded UTF-8
bytes/structure. Forward only validated mock business inputs and trusted derived
context. Return canonical JSON containing operation ID, status and mode only;
never return mock headers/body. Inputs and workflow literals may exist in Temporal
history because the proof already serializes them: use synthetic Dev data and
do not claim input-history redaction or secret detection.

Normalize failures to fixed-text non-retryable Temporal application errors with
closed safe types and no provider details. Propagate delivered activity cancellation
and enforce a bounded deadline with late-answer/cancellation-suppression guards. Infrastructure
redelivery can still occur; tests demonstrate deterministic mock identity rather
than enabling automatic live retries.

The existing proof activity scheduling does not configure heartbeats. Workflow
cancellation therefore does not establish immediate delivery of cancellation to
a running worker lookup. Integration verifies workflow cancellation and provider
quiescence under the mock executor's bounded deadline; unit tests separately
verify propagation when activity cancellation is actually delivered. Do not
claim an immediate worker interruption guarantee from this bridge.

The descriptor digest continues to identify contract metadata, not executable
Python. Existing project publication, activation, package materialization and
default catalog remain unchanged and may reject this built-in. Integration tests
start explicitly compiled plans on an explicitly composed disposable worker; they
do not establish an app-level publish/deploy/start path.

## Parallel ownership and verification

Pure lane owns context/input/identity validation and tests. Worker lane owns SDK
metadata adaptation, result validation, errors and deadline/cancellation tests.
Independent acceptance owns real Temporal workflows with actual mock components,
retry identity, debug/restart and replay verification. Coordinator owns shared
contracts, architecture, checklist evidence and final regression.

Use the installed pinned Temporal CLI for disposable local servers. No public
HTTP/DNS, live Entra, cloud, business-user UI or production acceptance is claimed.
Executable built-in publication/capabilities, default host integration, general
bindings, live effect recovery, credentials and other connector types remain open.
