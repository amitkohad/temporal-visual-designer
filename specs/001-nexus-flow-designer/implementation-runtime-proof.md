# NF-T004: Python Temporal runtime feasibility proof

Date: 2026-10-23. Status: implemented and locally verified.

Branch codex/004-runtime-proof is based on published foundation commit 178b52a. The foundation was published on its feature branch and had not been merged into main when work started.

## Delivered

Host preflight compiles validated definitions into frozen plans with release/configuration/runtime/input/activity bindings and a canonical definition digest. Sandboxed NexusFlowProofV1 executes a single sequence of start, activity, approval wait, durable timer and end. Durable approval/debug Updates have IDs and expected revisions; identical retries return recorded receipts while open, changed-content reuse and stale commands fail. Debug gates are Dev-only. Activities retain operation IDs and pinned timeout/retry settings. Snapshots expose metadata rather than raw payloads. Unsupported graphs fail preflight.

Code: nexusflow/modules/durable_runtime. Tests: nexusflow/tests/runtime. [ADR 0002](../../docs/decisions/0002-runtime-proof.md) records the proof contract and limits.

## Evidence

82 isolated tests and six real Temporal integration tests pass on Python 3.12.2 and 3.13.3 on Windows. Ruff lint/format, mypy (10 production files) and source/wheel builds pass. All six integration scenarios replay captured history through the SDK Replayer and sandbox.

Tests used local Temporal CLI 1.9.1 / Server 1.32.0 with loopback networking and in-memory persistence. No deployed cluster was used. Worker stop/recreation tests recovery against a live server, not server backup/restore. CI adds a separate pinned-CLI integration job; hosted execution is pending publication.

Verified: completed activity, approval wait and debug pause across two Worker restarts; duplicate approval receipts; concurrent A/B/A releases with different activity versions/digests; retry identity; duplicate live start rejection; durable timer recovery; rejection stops subsequent effects; cancellation at a pause dispatches nothing; retry exhaustion fails before approval. All histories replay.

## Acceptance mapping and limits

NF-T004 is complete at proof scope. Evidence covers RT-A3 and pinned-version aspects of RT-A4, subsets of RT-A2/A6/A7 and DB-A1/A3/A6. Feature 05 and RT-T01–05 are not complete.

Deferred: branching/join/loop/child execution, expressions/dataflow, complete output-schema validation, error/compensation routes, production Entra/artifact trust, activation/rollback routing, start adapters and input-conflict ledger, task-service transaction/outbox, control retries after closure, payload inspection/redaction, projections, history continuation, cross-interpreter upgrades and production placement. Rejection fails this proof workflow; configurable rejection routing is later work.

## Reproduce

```sh
uv sync --locked
uv run pytest
uv run pytest -m integration nexusflow/tests/runtime
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
```

Integration uses NEXUSFLOW_TEMPORAL_CLI or temporal on PATH; otherwise the SDK downloads a dev server and needs network access. Startup failure fails the suite instead of skipping. Fixture teardown stops the local server.
