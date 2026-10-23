# Durable runtime proof

This is the NF-T004 feasibility increment, not full feature 05. `compile_plan` validates a JSON workflow, trusted scope, pinned release and input before any Temporal start. `ProofWorkflow` interprets only the supported linear subset. `types.py` is the frozen proof-specific control/binding contract; it does not supersede the whole product API.

The worker must register `ProofWorkflow` and an approved activity named `nexusflow.proof.dispatch`. There is deliberately no production worker bootstrap or automatic Python-code loading. The integration suite supplies a synthetic activity and disposable local Temporal server. Do not expose direct Temporal control to browsers; Entra authorization, approved activity lookup and tenant routing must be added at their feature boundaries.

```sh
uv sync --locked
uv run pytest nexusflow/tests/runtime/test_preflight.py
uv run pytest -m integration nexusflow/tests/runtime
```

Integration uses `NEXUSFLOW_TEMPORAL_CLI` if provided, otherwise `temporal` on PATH. If neither is available, the SDK downloads an ephemeral dev server; network access is then required. CI installs a pinned CLI version. Startup failure fails the integration suite rather than reporting a skipped pass. The SDK selects a free loopback port and stops the server on teardown.

Queries return release/version/step/status metadata only. Raw input/output inspection, redaction rules, event projections and actual task inboxes belong to later features. Approval rejection fails this proof workflow; the full Designer's configured rejection/error routes remain unsupported. Cancellation uses Temporal's native workflow cancellation, including at durable waits and pause gates.

See [ADR 0002](../../../docs/decisions/0002-runtime-proof.md) and the implementation report in specs/001-nexus-flow-designer/implementation-runtime-proof.md for scope and evidence.
