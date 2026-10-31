# Run an HTTP mock step on an explicit Temporal worker

`HTTPJSONMockActivities` bridges the existing proof workflow dispatch activity to
the typed HTTP adapter in mock mode. A host must register it explicitly alongside
`ProofWorkflow` and compose the real Dev mock policy/catalog/executor. The default
launcher, API and package activation flow do not enable this adapter.

```python
mock_adapter = HTTPJSONActivityAdapter(mock=mock_executor)
activities = HTTPJSONMockActivities(
    mock_adapter,
    scope,
    namespace=client.namespace,
    task_queue=task_queue,
)
# Explicit worker composition:
# Worker(client, task_queue=task_queue, workflows=[ProofWorkflow],
#        activities=[activities.dispatch])
```

The activity name is `nexusflow.proof.dispatch`. Only the exact HTTP JSON
descriptor reference and literal `execution_mode="mock"` inputs are accepted.
Live mode fails before adapter invocation. The actual Temporal namespace,
workflow namespace/type, queue, activity type/ID and workflow/node operation
identity must match the configured worker and call. Scope and release/configuration
pins are checked rather than accepted as business input.
These are structural/context checks, not a durable lookup proving that an app
release was published or an authorized start was recorded. This mock-only worker
owns the generic dispatch activity name; do not register another activity with
that same name on the worker.

A new `http-...` operation ID hashes actual workflow/run identity, node and pinned
scope/release/configuration/reference/input data. It is valid for the HTTP adapter,
stays stable across retry attempts and differs for a new run. No live effect,
durable deduplication or recovery guarantee follows from that ID.

The underlying mock executor rechecks trusted Dev eligibility and worker grants
on every call. Fixture digest and exact request matching still apply. Results are
canonical JSON with only operation ID, status and mock mode. Private fixture
headers/body do not become workflow output. Request literals can be recorded in
Temporal history; keep them synthetic and do not use secrets in this Dev path.

Validation/provider failures become fixed-text non-retryable application errors;
delivered activity cancellation propagates and late answers cannot become success. The
bounded activity deadline does not preempt arbitrary synchronous host code.
The proof's activity scheduling has no heartbeat timeout, so a cancelled workflow
does not guarantee immediate interruption of a running mock lookup. The mock
deadline still bounds that lookup; integration verifies cancellation and bounded
quiescence separately from unit tests of delivered worker cancellation.

Real integration tests use direct compiled plans on a disposable local Temporal
server. Descriptor metadata is still not an executable Python package, so these
tests do not prove built-in project publication/activation, the default launcher,
API execution or browser integration. See
[ADR 0055](decisions/0055-temporal-http-mocks.md).
