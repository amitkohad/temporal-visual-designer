# Durable start reliability increment

Implemented on `codex/011-durable-starts` following the activity catalog increment. Scope: isolated manual/API start orchestration using the existing proof interpreter. See [ADR 0009](../../docs/decisions/0009-durable-starts.md).

## Implemented boundaries

- `nexusflow/modules/durable_runtime/starts.py`: explicit authorization, atomic release snapshot and idempotent dispatch ports; start request/receipt/error contracts; durable SQLite intent reservation and acknowledgement; canonical input conflict detection; preflight before reservation; pinned-plan retries.
- `nexusflow/tests/runtime/test_starts.py`: independent provider fakes with real SQLite, restart and lost-ack recovery, separate-connection concurrency/conflicts, reauthorization, scope separation, changed active release/configuration, invalid input/definition and unsupported-source rejection.

RT-T01a, RT-T03a and RT-T04a are complete at this bounded port/SQLite boundary. This contributes to RT-FR-01/05 and RT-A2/A4/A6 without claiming their full acceptance. The existing runtime proof remains unchanged.

## Remaining acceptance

Full RT-T01–05 remain open. No live Temporal start adapter or production active-release/identity adapter is supplied. No automatic retry daemon, release/run hold orchestration, HTTP API, webhook, schedule, form start, projection, checkpoint, control-flow extension or UI is implemented. The dispatcher fake enforces the required idempotency contract; real Temporal execution and retention semantics need separate integration tests. See ADR 0009 for storage and delivery limitations.

## Verification

Verified locally on Python 3.12.2:

- `uv run pytest nexusflow/tests/runtime/test_starts.py -q`: 19 passed.
- `uv run pytest -q`: 487 passed, 6 integration tests deselected.
- Ruff lint and format checks passed; strict mypy passed for 47 source files.
- `uv build`: source distribution and wheel built successfully.
- `git diff --check`: passed.

Real Temporal integration tests were not rerun: this increment adds a host-side start service, leaving proof workflow code unchanged. Hosted CI has not been run for this uncommitted branch.
