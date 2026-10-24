# Temporal start dispatch increment

Branch: `codex/012-temporal-start-dispatch`, based on merged PR #10. Scope: actual Temporal start delivery for the existing linear proof interpreter. Decision: [ADR 0010](../../docs/decisions/0010-temporal-start-dispatch.md).

## Implementation

- `durable_runtime/starts.py`: common preparation boundary used by synchronous and async services; async dispatcher port and `AsyncStartService` preserve existing preflight and reauthorization behavior.
- `durable_runtime/temporal_starts.py`: persistent SQLite dispatch claims/receipts; destination and immutable plan binding; SDK start policies; run-pinned history verification; permanent local deduplication and conservative ambiguous-delivery recovery.
- `tests/runtime/test_temporal_starts.py`: independent SDK failure fixtures cover closed-run receipts after restart/history expiry, conflicts, missing history, cancellation, invalid scope/runtime/ID, concurrent journal claims and async service error/authorization behavior.
- `tests/runtime/test_temporal_integration.py`: actual Temporal async-service recovery after completed-run lost acknowledgement and journal reopen; conflict with pre-existing wrong plan. Existing real workflow replay/restart scenarios remain intact.

## Acceptance mapping

Completes RT-T01c, RT-T03c and RT-T04c at their stated proof-runtime boundary, contributing to RT-A2/A3/A4/A6. The receipt remains bound to the original intent's release/configuration; the prior isolated start tests still verify activation changes and request concurrency. The real Temporal tests use synthetic authorization/release ports, not deployed Entra/activation services.

## Remaining work

Full RT-T01–05 and NF-T009 remain open. The conservative claim protocol deliberately requires operator remediation when a start never reached Temporal or cannot be verified after retention; no automatic resend/drainer or recovery UI is supplied. Full control flow/checkpoints/projections, production identity/activation/placement/capability adapters, transactional release/run holds and start HTTP/webhook/form/schedule adapters remain separate tasks. See ADR 0010 for destination and journal backup/retention limitations.

## Verification

Verified locally on Python 3.12.2:

- `uv run pytest -q`: 501 passed, 8 integration tests deselected.
- `uv run pytest nexusflow/tests/runtime/test_temporal_starts.py -q`: 14 passed.
- `uv run pytest -m integration nexusflow/tests/runtime -q`: 8 passed against a disposable local Temporal server (63 nonintegration tests deselected at that run).
- Ruff lint/format checks and strict mypy passed (48 source files).
- `uv build`: source distribution and wheel built successfully.
- `git diff --check`: passed.

Hosted CI has not been run for this uncommitted branch. Actual Temporal acceptance uses the SDK's local test environment; it establishes neither deployed Azure infrastructure nor production identity/activation integration.
